"""Small helpers shared by every module: the worker pool, JSON files, number and name formatting."""
import contextvars
import threading
import json
import re
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor


POOL = ThreadPoolExecutor(max_workers=8)


def pool_map(fn, items):
    """POOL.map that keeps the request's language in the worker threads."""
    futures = [POOL.submit(contextvars.copy_context().run, fn, item) for item in items]
    return [f.result() for f in futures]


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def ascii_slug(text):
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip("-")


def num(v):
    if v is None:
        return "-"
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class SingleFlight:
    """Run a loader once per key at a time: threads asking for the same key meanwhile wait for
    that one call and share its result (or its exception) instead of repeating the request."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = {}

    def do(self, key, load):
        with self._lock:
            call = self._calls.get(key)
            mine = call is None
            if mine:
                call = self._calls[key] = Future()
        if not mine:
            return call.result()
        try:
            result = load()
        except BaseException as exc:
            call.set_exception(exc)
            raise
        else:
            call.set_result(result)
            return result
        finally:
            with self._lock:
                self._calls.pop(key, None)
