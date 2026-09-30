"""Run the static export against recorded upstream answers, or record a real run.

    python3 tools/replay_export.py record REC.json.gz            [--root CODE]
    python3 tools/replay_export.py replay REC.json.gz OUT_DIR    [--root CODE] [--fail REGEX] [--blank REGEX]

record  copies data/ and leagues.json to a temporary folder, runs a real export there with
        every HTTP answer kept, and saves the recording. The repository is not touched.
replay  rebuilds the export offline into OUT_DIR/site, with OUT_DIR/state holding data/ and
        leagues.json after the run and OUT_DIR/report.json the exit code and any requests
        the recording did not have. --fail makes matching requests answer HTTP 503, --blank
        an empty page (both match a regex against the URL or the request body). --keep
        leaves the site and var/ of an earlier replay in OUT_DIR (a build on top of it).

--root picks which copy of the code runs (default: this repository), so two versions can
be compared on the same inputs. The clock is frozen at the recording time, the time zone
is fixed and hashing is seeded, so replays of the same code are byte-identical.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay  # noqa: E402

ENV = {"PYTHONHASHSEED": "0", "TZ": "Europe/Vilnius", "PYTHONDONTWRITEBYTECODE": "1", "FT_NO_RETRY_WAIT": "1"}


def reexec_with_fixed_env():
    if all(os.environ.get(k) == v for k, v in ENV.items()):
        return
    os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, **ENV})


def snapshot_files(state):
    return {str(p.relative_to(state)): p.read_text(encoding="utf-8")
            for p in sorted(state.rglob("*")) if p.is_file()}


def write_files(state, files):
    for rel, text in files.items():
        path = state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_export(root, state, site):
    """Import the code under `root` with its data paths pointed at `state` and run export.main()."""
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.update(FT_DATA_DIR=str(state / "data"), FT_LEAGUES_FILE=str(state / "leagues.json"),
                      FT_SITE_DIR=str(site), FT_VAR_DIR=str(site.parent / "var"), FT_PROBALLERS_LOOKUPS="0",
                      FT_DEVICE="replay")
    if not (root / "backend").is_dir():  # code from before the backend package: patch its globals
        import server as s
        s.DATA_DIR = state / "data"
        s.LINEUPS_DIR = s.DATA_DIR / "lineups"
        s.INJURY_LOG_FILE = s.DATA_DIR / "injuries.json"
        s.PROBALLERS_FILE = s.DATA_DIR / "proballers.json"
        s.LEAGUES_FILE = state / "leagues.json"
        import export
        export.OUT = site
        export._lookups["left"] = 0
    else:
        import export
    try:
        export.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def record(args, root):
    work = Path(tempfile.mkdtemp(prefix="ft-record-"))
    state = work / "state"
    (state / "data").mkdir(parents=True)
    if (root / "data").is_dir():
        shutil.copytree(root / "data", state / "data", dirs_exist_ok=True)
    shutil.copy(root / "leagues.json", state / "leagues.json")
    files = snapshot_files(state)
    recorded_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    replay.freeze_clock(recorded_at)
    rec = replay.Recorder()
    rec.install()
    code = run_export(root, state, work / "site")
    replay.save(args.recording, recorded_at, files, rec.responses)
    size = Path(args.recording).stat().st_size
    print(f"recorded {len(rec.responses)} answers at {recorded_at} -> {args.recording} ({size / 1e6:.1f} MB), export exit {code}")
    shutil.rmtree(work, ignore_errors=True)
    return code


def replay_run(args, root):
    doc = replay.load(args.recording)
    out = Path(args.out).resolve()
    if out.exists() and not args.keep:
        shutil.rmtree(out)
    state = out / "state"
    shutil.rmtree(state, ignore_errors=True)
    write_files(state, doc["files"])
    (state / "data").mkdir(parents=True, exist_ok=True)
    replay.freeze_clock(doc["recordedAt"])
    player = replay.Replayer(doc["responses"], args.fail, args.blank)
    player.install()
    code = run_export(root, state, out / "site")
    report = {"exitCode": code, "served": player.served, "missingCount": len(player.missing),
              "missing": sorted(set(player.missing))[:50]}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"replayed: exit {code}, {player.served} answers served, {len(player.missing)} missing")
    return 0


def main():
    reexec_with_fixed_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("record")
    r.add_argument("recording")
    r.add_argument("--root")
    p = sub.add_parser("replay")
    p.add_argument("recording")
    p.add_argument("out")
    p.add_argument("--root")
    p.add_argument("--fail", action="append", default=[])
    p.add_argument("--blank", action="append", default=[])
    p.add_argument("--keep", action="store_true", help="keep OUT_DIR/site and var from an earlier replay")
    args = parser.parse_args()
    args.recording = str(Path(args.recording).resolve())
    root = Path(args.root).resolve() if args.root else HERE.parent
    sys.exit(record(args, root) if args.mode == "record" else replay_run(args, root))


if __name__ == "__main__":
    main()
