"""Shared test fixtures.

`ft`         every backend module behind one name, with the data folder moved to a
             temporary directory, so no test ever touches the real data/.
`clock`      freezes "today" / "now" for code that records dates.
`recording`  the recorded upstream answers in tests/fixtures (a real export run).
`graphql`    look up a recorded GraphQL answer by its operation name.
`replayed`   runs tools/replay_export.py offline and returns the output folder.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Local time decides the dates in the history files: pin it, whatever the machine uses.
os.environ["TZ"] = "Europe/Vilnius"
time.tzset()

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))

import replay  # noqa: E402

RECORDING = ROOT / "tests" / "fixtures" / "recording-r1.json.gz"
HLA = "6aa7fe67c95ed14589bf170d"      # head-to-head league in the recording
CLASSIC = "6aa6bddec90ec6ddaf50d152"  # classic league in the recording


MODULES = [
    "config", "clock", "i18n", "errors", "util", "net", "sources.basketnews", "sources.advanced",
    "sources.injury_report", "sources.wikidata", "rounds", "scoring", "history", "injuries", "league", "players",
    "advanced", "proballers", "previews", "projections", "pipeline", "analytics.season", "analytics.awards",
    "analytics.transfers", "payloads.standings", "payloads.team", "payloads.players", "payloads.games",
    "payloads.player", "payloads.injuries", "payloads.draft", "payloads.transfers", "payloads.records",
    "payloads.leagues",
]


class Backend:
    """All backend modules behind one name: ft.score_lineup, ft.gql, ... Setting an attribute
    (monkeypatch.setattr(ft, "gql", fake)) replaces it in every module that imported it."""

    def __init__(self):
        import importlib
        object.__setattr__(self, "_mods", [importlib.import_module(f"backend.{m}") for m in MODULES])

    def _home(self, name):
        for m in self._mods:  # the module that defines it (listed before the ones importing it)
            if name in vars(m) and getattr(vars(m)[name], "__module__", m.__name__) == m.__name__:
                return m
        for m in self._mods:
            if name in vars(m):
                return m
        raise AttributeError(name)

    def __getattr__(self, name):
        return getattr(self._home(name), name)

    def __setattr__(self, name, value):
        current = getattr(self._home(name), name)
        for m in self._mods:
            if vars(m).get(name) is current:
                setattr(m, name, value)


@pytest.fixture
def ft(tmp_path, monkeypatch):
    """The backend, with every data path inside tmp_path and empty caches."""
    from backend import config, history
    from backend.sources import basketnews
    backend = Backend()
    data = tmp_path / "data"
    data.mkdir()
    for name in ("DATA_DIR", "LINEUPS_DIR", "INJURY_LOG_FILE", "PROBALLERS_FILE"):
        monkeypatch.setattr(config, name, getattr(config, name))  # restored after the test
    config.set_data_dir(data)
    monkeypatch.setattr(config, "LEAGUES_FILE", tmp_path / "leagues.json")
    monkeypatch.setattr(basketnews, "_cache", {})
    history.discard()
    backend.LANG.set("lt")
    object.__setattr__(backend, "data_dir", data)  # for tests that inspect the files
    return backend


class Clock:
    def __init__(self, monkeypatch):
        from backend import clock
        self.monkeypatch, self.module = monkeypatch, clock

    def set(self, iso):
        """Freeze time at `iso` (e.g. "2026-09-30T12:00:00+03:00")."""
        frozen = dt.datetime.fromisoformat(iso)

        class FrozenDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

        class FrozenDate(dt.date):
            @classmethod
            def today(cls):
                return frozen.date()

        self.monkeypatch.setattr(self.module, "datetime", FrozenDateTime)
        self.monkeypatch.setattr(self.module, "date", FrozenDate)


@pytest.fixture
def clock(ft, monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture(scope="session")
def recording():
    return replay.load(RECORDING)


@pytest.fixture(scope="session")
def graphql(recording):
    """graphql("playersSearchRecordsFromClient") -> list of recorded `data` answers for that query."""
    def find(op, contains=None):
        out = []
        for entry in recording["responses"].values():
            req = entry.get("request") or ""
            if op in req and (contains is None or contains in req) and "text" in entry:
                out.append(json.loads(entry["text"])["data"])
        return out
    return find


def run_replay(out, *extra, root=ROOT):
    """Offline export of the recording into `out`; returns the replay report."""
    cmd = [sys.executable, str(TOOLS / "replay_export.py"), "replay", str(RECORDING), str(out), "--root", str(root), *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    report = json.loads((out / "report.json").read_text())
    report["log"] = proc.stdout
    return report


@pytest.fixture(scope="session")
def replayed(tmp_path_factory):
    """One offline export of the recording, shared by the tests that only read it."""
    out = tmp_path_factory.mktemp("replay") / "out"
    report = run_replay(out)
    return out, report
