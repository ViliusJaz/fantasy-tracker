"""Logging: one line per event, tagged with the pipeline stage it belongs to.

    12:00:03 [FETCH] HLA 1 Divizionas: round 2 of 38 (live), 8 teams, 331 players
    12:00:09 [STORAGE] lineups changed in 1 file, injury log unchanged
    12:00:21 [EXPORT] 1702 files, 0 failed

Modules log through get("fetch"), get("storage"), ... export.py and server.py call setup().
"""
import logging
import sys
import time
from contextlib import contextmanager

STAGES = ("fetch", "validation", "storage", "analytics", "export", "publish", "build", "http")


class _StageFormatter(logging.Formatter):
    def format(self, record):
        record.stage = record.name.rsplit(".", 1)[-1].upper()
        return super().format(record)


def setup(level=logging.INFO):
    """Log to stdout (publish.sh sends it to ~/Library/Logs/fantasy-tracker.log)."""
    root = logging.getLogger("tracker")
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StageFormatter("%(asctime)s [%(stage)s] %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)


def get(stage):
    return logging.getLogger(f"tracker.{stage}")


@contextmanager
def timed():
    """with timed() as t: ...   then t() -> seconds since the block started."""
    start = time.monotonic()
    yield lambda: time.monotonic() - start
