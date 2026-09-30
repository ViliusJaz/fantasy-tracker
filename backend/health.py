"""Build status for the site: api/health.json.

Written after every build attempt. The top-level fields describe the latest attempt; the
last successful and the last failed build are kept separately, so a failure never hides
when the data was last good, and the next build can compare itself with the last good one
(validation.py). Nothing secret goes in: counts, times, statuses and the code version.

    {"status": "healthy" | "degraded" | "failed", "lastAttempt": ..., "lastSuccessfulUpdate": ...,
     "buildDurationSeconds": 18.4, "basketnewsRequests": 137, "failedRequests": 0,
     "playersProcessed": 331, "pagesGenerated": 1702, "validationWarnings": 0,
     "sourceStatus": {"basketnews": "ok", "injuries": "ok", "advancedStats": "ok"},
     "lastSuccess": {...}, "lastFailure": {...}}
"""
import json
import os
import subprocess

from backend import clock, config

VERSION = 1


def load(*paths):
    """The first readable health document among `paths`, else {}."""
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return doc
    return {}


def baseline(previous):
    """Metrics of the last successful build: {"pages": n, "leagues": {id: {...}}}."""
    return (previous.get("lastSuccess") or {}).get("metrics") or {}


def device():
    if os.environ.get("FT_DEVICE"):
        return os.environ["FT_DEVICE"]
    return "phone" if "com.termux" in os.environ.get("PREFIX", "") else "mac" if os.uname().sysname == "Darwin" else "other"


def code_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def source_status(requests, report, injuries_state):
    """ok / degraded / failed / stale per source, from request counters and validation."""
    def state(name):
        st = requests.get(name) or {}
        if not st.get("requests") and not st.get("failures"):
            return "unused"
        if st.get("failures") and st["failures"] >= st.get("requests", 0):
            return "failed"
        return "degraded" if st.get("failures") else "ok"
    basketnews = state("basketnews")
    if report.blocked("injuries") or injuries_state == "stale":
        injuries = "stale"
    else:
        injuries = state("injury-report")
    return {"basketnews": basketnews, "injuries": injuries, "advancedStats": state("advanced-stats"),
            "euroleague": state("euroleague")}


def document(previous, *, ok, started, duration, requests, report, sources, players, pages, failed_pages, metrics):
    """The new health.json: this attempt on top, the last success / failure carried over."""
    now = clock.now().astimezone().isoformat(timespec="seconds")
    degraded = report.count("error") > 0 or any(v in ("degraded", "stale", "failed") for v in sources.values())
    status = "failed" if not ok else "degraded" if degraded else "healthy"
    attempt = {
        "at": now,
        "startedAt": started,
        "status": status,
        "device": device(),
        "version": code_version(),
        "durationSeconds": round(duration, 1),
        "requests": requests,
        "pages": pages,
        "failedPages": failed_pages,
        "validation": {**report.summary(), "issues": report.issues[:50]},
        "sourceStatus": sources,
        "metrics": metrics,
    }
    doc = {
        "version": VERSION,
        "status": status,
        "lastAttempt": now,
        "lastSuccessfulUpdate": now if ok else previous.get("lastSuccessfulUpdate"),
        "buildDurationSeconds": attempt["durationSeconds"],
        "basketnewsRequests": (requests.get("basketnews") or {}).get("requests", 0),
        "failedRequests": sum(st.get("failures", 0) for st in requests.values()),
        "playersProcessed": players,
        "pagesGenerated": pages,
        "validationWarnings": report.count("warning"),
        "validationErrors": report.count("error"),
        "sourceStatus": sources,
        "lastSuccess": attempt if ok else previous.get("lastSuccess"),
        "lastFailure": previous.get("lastFailure") if ok else attempt,
    }
    return doc


def write(doc, *paths):
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
