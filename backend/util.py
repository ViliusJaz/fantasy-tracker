"""Small helpers shared by every module: the worker pool, JSON files, number and name formatting."""
import contextvars
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor


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
