"""Paths, upstream constants and the tracked leagues (leagues.json).

The data folder, leagues.json, the export folder and the local state folder can be moved
with FT_DATA_DIR, FT_LEAGUES_FILE, FT_SITE_DIR and FT_VAR_DIR (tests and offline replays do).
data/ is committed (the history); var/ stays on the device (health, caches, snapshots).
Code reads the paths as config.X at call time, so set_data_dir() moves all of them.
"""
import os
import threading
from pathlib import Path

from backend.util import read_json


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
LEAGUES_FILE = Path(os.environ.get("FT_LEAGUES_FILE") or ROOT / "leagues.json")
SITE_DIR = Path(os.environ.get("FT_SITE_DIR") or ROOT / "site")
VAR_DIR = Path(os.environ.get("FT_VAR_DIR") or ROOT / "var")
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LOCALE = "lt"
HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", 8124))

LIVE_TTL = 60             # seconds to cache data that can still change
SETTLED_TTL = 60 * 60     # seconds to cache finished rounds
INJURY_TTL = 30 * 60      # seconds between injury report downloads
BACKGROUND_EVERY = 10 * 60  # seconds between lineup snapshots / injury checks


def set_data_dir(path):
    """Point every data file at `path` (recorded lineups, injury log, Proballers links)."""
    global DATA_DIR, LINEUPS_DIR, INJURY_LOG_FILE, PROBALLERS_FILE
    DATA_DIR = Path(path)
    LINEUPS_DIR = DATA_DIR / "lineups"
    INJURY_LOG_FILE = DATA_DIR / "injuries.json"
    PROBALLERS_FILE = DATA_DIR / "proballers.json"


set_data_dir(os.environ.get("FT_DATA_DIR") or ROOT / "data")

_config_lock = threading.Lock()


def load_config():
    return read_json(LEAGUES_FILE, [])


def config_entry(fid):
    return next((e for e in load_config() if e["id"] == fid), None)
