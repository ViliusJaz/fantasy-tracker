"""HTTP for every upstream source: TLS, gzip, timeouts, polite retries and counters.

fetch() retries only what may work a moment later: HTTP 429 and 500/502/503/504, timeouts,
dropped connections, temporary DNS failures and answers cut off mid-way. It waits 1 s, 2 s,
... (with jitter, capped) between tries and honours Retry-After. Everything else -- a 403
from Cloudflare, a 404, an HTML page where JSON was expected -- fails at once.

Each source (basketnews, advanced-stats, injury-report, wikidata, ...) has at most
MAX_PARALLEL requests in flight, and after OPEN_AFTER failed requests in a row it is paused
for PAUSE seconds (a circuit breaker), so an outage ends a build quickly instead of
hammering the site. stats() counts requests, retries, failures and bytes per source.
"""
import gzip
import http.client
import json
import os
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from backend import log

LOG = log.get("fetch")

ATTEMPTS = 3            # tries per request
BACKOFF = 1.0           # seconds before the first retry, doubled after each one
MAX_WAIT = 20.0         # longest wait between tries (also caps Retry-After)
OPEN_AFTER = 6          # failed requests in a row that pause a source
PAUSE = 120.0           # seconds a paused source stays paused
MAX_PARALLEL = 6        # requests in flight per source
RETRY_STATUS = {429, 500, 502, 503, 504}
# Offline replays and tests: retry without waiting.
NO_WAIT = bool(os.environ.get("FT_NO_RETRY_WAIT"))


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # python.org builds on macOS ship without a CA bundle; the system one works.
    if Path("/etc/ssl/cert.pem").exists():
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


SSL_CTX = _ssl_context()


RECORDER = None  # set by backend/storage/snapshots.py while a build is being recorded


def remember(method, url, data, body, status=200, content_type=None):
    """Hand an answer to the snapshot recorder, if one is running (also used for cache hits)."""
    if RECORDER is not None:
        RECORDER(method, url, data, body, status, content_type)


class FetchError(Exception):
    """A request that failed for good (after any retries). `status` is the HTTP code, if any."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _Source:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(MAX_PARALLEL)
        self.failed_in_row = 0
        self.paused_until = 0.0
        self.stats = {"requests": 0, "retries": 0, "failures": 0, "bytes": 0}


_sources = {}
_lock = threading.Lock()


def _source(name):
    with _lock:
        if name not in _sources:
            _sources[name] = _Source()
        return _sources[name]


def stats():
    """{source: {"requests", "retries", "failures", "bytes"}} since the process started."""
    with _lock:
        return {name: dict(src.stats) for name, src in _sources.items()}


def reset():
    """Forget counters and pauses (tests)."""
    with _lock:
        _sources.clear()


def _bump(src, key, n=1):
    with _lock:
        src.stats[key] += n


def classify(exc):
    """(retry?, seconds the server asked us to wait or None, short reason) for a failed try."""
    if isinstance(exc, urllib.error.HTTPError):
        wait = None
        if exc.code in (429, 503) and exc.headers is not None:
            try:
                wait = float(exc.headers.get("Retry-After") or "")
            except ValueError:
                wait = None
        return exc.code in RETRY_STATUS, wait, f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, None, f"certificate error: {reason}"
        if isinstance(reason, socket.gaierror):
            return True, None, f"DNS lookup failed: {reason}"  # often temporary on mobile data
        return True, None, f"network error: {reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True, None, "timed out"
    if isinstance(exc, (ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return True, None, f"connection dropped: {type(exc).__name__}"
    if isinstance(exc, (EOFError, zlib.error, gzip.BadGzipFile)):
        return True, None, "answer cut off"
    if isinstance(exc, http.client.HTTPException):
        return True, None, f"bad answer: {type(exc).__name__}"
    if isinstance(exc, OSError):
        return True, None, f"network error: {exc}"
    return False, None, f"{type(exc).__name__}: {exc}"


def _wait(attempt, asked):
    if NO_WAIT:
        return 0.0
    if asked is not None:
        return max(0.0, min(asked, MAX_WAIT))
    return min(MAX_WAIT, BACKOFF * 2 ** (attempt - 1) * random.uniform(0.7, 1.3))


def _looks_like_html(body):
    head = body[:200].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html")) or b"<html" in head


def fetch(req, source, timeout=30, as_json=False):
    """Body of a request (unpacked if gzip), or its parsed JSON with as_json=True.
    Raises FetchError when it cannot be had."""
    src = _source(source)
    if time.monotonic() < src.paused_until:
        _bump(src, "failures")
        raise FetchError(f"{source} paused after {OPEN_AFTER} failed requests in a row")
    url = req if isinstance(req, str) else req.full_url
    for attempt in range(1, ATTEMPTS + 1):
        _bump(src, "requests")
        try:
            with src.slots:
                with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
                    raw = resp.read()
                    encoding = resp.headers.get("Content-Encoding")
                    status, content_type = getattr(resp, "status", 200), resp.headers.get("Content-Type")
            body = gzip.decompress(raw) if encoding == "gzip" else raw
            if as_json:
                try:
                    data = json.loads(body)
                except ValueError as exc:
                    if _looks_like_html(body):  # a challenge / error page: trying again will not help
                        raise FetchError("got an HTML page instead of JSON (blocked?)") from exc
                    raise EOFError("incomplete JSON") from exc
        except FetchError:
            _failed(src, source)
            LOG.warning("%s: got an HTML page instead of JSON (%s)", source, _short(url))
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is classified below
            retry, asked, why = classify(exc)
            if not retry or attempt == ATTEMPTS:
                _failed(src, source)
                LOG.warning("%s: %s (%s)%s", source, why, _short(url), f" after {attempt} tries" if retry else "")
                raise FetchError(why, getattr(exc, "code", None)) from exc
            wait = _wait(attempt, asked)
            _bump(src, "retries")
            LOG.info("%s: %s, try %d of %d in %.1fs (%s)", source, why, attempt + 1, ATTEMPTS, wait, _short(url))
            time.sleep(wait)
            continue
        with _lock:
            src.failed_in_row = 0
            src.stats["bytes"] += len(raw)
        if isinstance(req, str):
            remember("GET", url, None, body, status, content_type)
        else:
            remember(req.get_method(), url, req.data, body, status, content_type)
        return data if as_json else body
    raise AssertionError("unreachable")


def _failed(src, source):
    with _lock:
        src.stats["failures"] += 1
        src.failed_in_row += 1
        if src.failed_in_row >= OPEN_AFTER and time.monotonic() >= src.paused_until:
            src.paused_until = time.monotonic() + PAUSE
            LOG.error("%s: %d failed requests in a row, pausing it for %ds", source, src.failed_in_row, PAUSE)


def _short(url):
    return url if len(url) <= 90 else url[:87] + "..."
