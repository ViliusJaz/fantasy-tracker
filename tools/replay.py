"""Record and replay the tracker's upstream HTTP traffic.

Every request the tracker makes (BasketNews GraphQL, advanced stats, the injury report,
Wikidata) ends in urllib.request.urlopen. Recording wraps it and keeps each answer;
replaying serves those answers back, so an export can be rebuilt offline from exactly the
same inputs -- the basis of the golden-output tests that prove a refactor changed nothing.

A recording is one gzip JSON file:
    {"version": 1, "recordedAt": ISO time, "files": {relpath: text}, "responses": {key: entry}}
`files` is the state of data/ and leagues.json before the run.
"""
import base64
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import threading
import urllib.error
import urllib.request
from email.message import Message

VERSION = 1


def request_key(req):
    """Stable key of a request: method, URL and a hash of the body."""
    if isinstance(req, str):
        url, data, method = req, None, "GET"
    else:
        url, data, method = req.full_url, req.data, req.get_method()
    digest = hashlib.sha1(data).hexdigest()[:20] if data else "-"
    return f"{method} {url} {digest}"


def _text(body):
    try:
        return {"text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(body).decode()}


def _bytes(entry):
    return entry["text"].encode("utf-8") if "text" in entry else base64.b64decode(entry.get("b64", ""))


class FakeResponse(io.BytesIO):
    """What urlopen returns: readable, a context manager, with .status and .headers."""

    def __init__(self, body, headers, status=200, url=""):
        super().__init__(body)
        self.status = status
        self.code = status
        self.headers = headers
        self.url = url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _headers(pairs):
    msg = Message()
    for k, v in (pairs or {}).items():
        if v is not None:
            msg[k] = v
    return msg


class Recorder:
    """Wraps the real urlopen and keeps every answer (bodies stored unpacked)."""

    def __init__(self):
        self.responses = {}
        self._lock = threading.Lock()
        self._real = urllib.request.urlopen

    def _keep(self, key, req, entry):
        if not isinstance(req, str) and req.data:
            entry["request"] = req.data.decode("utf-8", errors="replace")
        entry["url"] = req if isinstance(req, str) else req.full_url
        with self._lock:
            self.responses.setdefault(key, entry)

    def urlopen(self, req, *args, **kwargs):
        key = request_key(req)
        try:
            with self._real(req, *args, **kwargs) as resp:
                raw = resp.read()
                enc = resp.headers.get("Content-Encoding")
                ctype = resp.headers.get("Content-Type")
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            body = exc.read() or b""
            self._keep(key, req, {"status": exc.code, "error": "http", "reason": str(exc.reason),
                                  "headers": {"Retry-After": exc.headers.get("Retry-After") if exc.headers else None},
                                  **_text(body)})
            raise urllib.error.HTTPError(exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(body)) from None
        except Exception as exc:  # timeouts, DNS, resets: recorded so a replay fails the same way
            self._keep(key, req, {"error": type(exc).__name__, "message": str(exc)})
            raise
        body = gzip.decompress(raw) if enc == "gzip" else raw
        self._keep(key, req, {"status": status, "headers": {"Content-Type": ctype}, **_text(body)})
        return FakeResponse(body, _headers({"Content-Type": ctype}), status)

    def install(self):
        urllib.request.urlopen = self.urlopen


ERRORS = {"TimeoutError": TimeoutError, "ConnectionResetError": ConnectionResetError,
          "ConnectionRefusedError": ConnectionRefusedError, "URLError": urllib.error.URLError}


class Replayer:
    """Serves recorded answers. `fail` patterns turn matching requests into HTTP 503s (a source
    that is down), `blank` ones into an empty 200 page (a source that answers but with nothing
    usable); requests missing from the recording fail as network errors."""

    def __init__(self, responses, fail=(), blank=()):
        self.responses = responses
        self.fail = [re.compile(p) for p in fail]
        self.blank = [re.compile(p) for p in blank]
        self.missing = []
        self.served = 0
        self._lock = threading.Lock()

    def urlopen(self, req, *args, **kwargs):
        key = request_key(req)
        url = req if isinstance(req, str) else req.full_url
        body = "" if isinstance(req, str) or not req.data else req.data.decode("utf-8", errors="replace")
        if any(p.search(url) or p.search(body) for p in self.fail):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable (replay)", _headers({}), io.BytesIO(b""))
        if any(p.search(url) or p.search(body) for p in self.blank):
            return FakeResponse(b"<html><body></body></html>", _headers({"Content-Type": "text/html"}), 200, url)
        entry = self.responses.get(key)
        if entry is None:
            with self._lock:
                self.missing.append(key)
            raise urllib.error.URLError(f"not in recording: {key}")
        with self._lock:
            self.served += 1
        if entry.get("error") == "http":
            raise urllib.error.HTTPError(url, entry["status"], entry.get("reason", ""),
                                         _headers(entry.get("headers")), io.BytesIO(_bytes(entry)))
        if entry.get("error"):
            raise ERRORS.get(entry["error"], OSError)(entry.get("message", ""))
        return FakeResponse(_bytes(entry), _headers(entry.get("headers")), entry.get("status", 200), url)

    def install(self):
        urllib.request.urlopen = self.urlopen


def freeze_clock(at_iso):
    """Make datetime.now() / date.today() return `at_iso` (an aware ISO time). Must run
    before the tracker's modules are imported, since they import the classes by name."""
    frozen = dt.datetime.fromisoformat(at_iso)
    real_datetime, real_date = dt.datetime, dt.date

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.astimezone().replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.now()

    class FrozenDate(real_date):
        @classmethod
        def today(cls):
            return frozen.astimezone().date()

    dt.datetime = FrozenDateTime
    dt.date = FrozenDate


def save(path, recorded_at, files, responses):
    doc = {"version": VERSION, "recordedAt": recorded_at, "files": files, "responses": responses}
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(doc, fh, ensure_ascii=False, sort_keys=True)


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("version") != VERSION:
        raise ValueError(f"unsupported recording version {doc.get('version')}")
    return doc
