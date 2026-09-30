"""Shared test fixtures.

`ft`         the tracker's backend with its data folder moved to a temporary directory,
             so no test ever touches the real data/.
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


@pytest.fixture
def ft(tmp_path, monkeypatch):
    """server.py's functions, with every data path inside tmp_path."""
    import server
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(server, "DATA_DIR", data)
    monkeypatch.setattr(server, "LINEUPS_DIR", data / "lineups")
    monkeypatch.setattr(server, "INJURY_LOG_FILE", data / "injuries.json")
    monkeypatch.setattr(server, "PROBALLERS_FILE", data / "proballers.json")
    monkeypatch.setattr(server, "LEAGUES_FILE", tmp_path / "leagues.json")
    monkeypatch.setattr(server, "_cache", {})
    server.LANG.set("lt")
    monkeypatch.setattr(server, "data_dir", data, raising=False)  # for tests that inspect the files
    return server


class Clock:
    def __init__(self, monkeypatch, module):
        self.monkeypatch, self.module = monkeypatch, module

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
    return Clock(monkeypatch, ft)


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
