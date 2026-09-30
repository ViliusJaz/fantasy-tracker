"""HTTP plumbing shared by all upstream sources: TLS certificates and reading (gzip) answers."""
import gzip
import ssl
import threading
import urllib.request
from pathlib import Path


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

# Requests made and failed since the process started (shown in the build log).
STATS = {"requests": 0, "failures": 0}
_stats_lock = threading.Lock()


def _count(key):
    with _stats_lock:
        STATS[key] += 1


def read_body(req, timeout=30):
    """Response body, unpacked when the server sent it gzip-compressed."""
    _count("requests")
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            raw = resp.read()
            return gzip.decompress(raw) if resp.headers.get("Content-Encoding") == "gzip" else raw
    except Exception:
        _count("failures")
        raise
