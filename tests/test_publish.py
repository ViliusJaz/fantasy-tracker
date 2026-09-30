"""publish.sh between two devices, against a local stand-in for GitHub (a bare repository).

The real export is replaced by tests/support/fake_export.py (FT_EXPORT), which writes a tiny
site, changes data/ as told and can be slow, fail, or have its lock taken away mid-build.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).resolve().parent / "support" / "fake_export.py"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")


def git(cwd, *args, check=True):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and out.returncode:
        raise AssertionError(out.stderr)
    return out.stdout.strip()


@pytest.fixture
def github(tmp_path):
    """A bare 'GitHub' with the publishing code and some history, plus clone(name) for devices."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    for rel in ("publish.sh", "tools/merge_history.py", ".gitattributes"):
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, seed / rel)
    shutil.copy(FAKE, seed / "fake_export.py")
    (seed / ".gitignore").write_text("site/\nvar/\n")
    (seed / "leagues.json").write_text("[]\n")
    (seed / "data").mkdir()
    (seed / "data" / "injuries.json").write_text(json.dumps({"players": {}}, indent=2) + "\n")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "HEAD:main")

    def clone(name):
        path = tmp_path / name
        git(tmp_path, "clone", "-q", str(origin), str(path))
        git(path, "config", "user.name", name)
        git(path, "config", "user.email", f"{name}@example.com")
        return path
    clone.origin = origin
    return clone


def publish(device, **env):
    full = {**os.environ, "FT_EXPORT": f"{sys.executable} fake_export.py", "HOME": str(device),
            "FT_DEVICE": device.name, "FAKE_WHO": device.name, **{k: str(v) for k, v in env.items()}}
    return subprocess.run(["bash", str(device / "publish.sh")], cwd=device, capture_output=True, text=True,
                          env=full, timeout=120)


def remote(github, ref):
    out = git(github.origin, "for-each-ref", "--format=%(objectname)", f"refs/heads/{ref}")
    return out or None


def remote_file(github, rev, path):
    return git(github.origin, "show", f"{rev}:{path}")


def injured(github):
    return set(json.loads(remote_file(github, "main", "data/injuries.json"))["players"])


def test_publishes_data_and_site_and_releases_the_lock(github):
    mac = github("mac")
    run = publish(mac, FAKE_PLAYER="101")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "published" in run.stdout
    assert injured(github) == {"101"}
    assert remote_file(github, "gh-pages", "index.html") == "site by mac"
    assert remote(github, "publish-lock") is None


def test_second_device_skips_while_the_first_holds_the_lock(github):
    mac, phone = github("mac"), github("phone")
    first = {}
    t = threading.Thread(target=lambda: first.update(run=publish(mac, FAKE_SLEEP=3, FAKE_PLAYER="1")))
    t.start()
    for _ in range(100):  # wait until the Mac holds the lock
        if remote(github, "publish-lock"):
            break
        threading.Event().wait(0.05)
    second = publish(phone, FAKE_PLAYER="2")
    t.join()
    assert second.returncode == 0 and "skipped: mac is publishing" in second.stdout
    assert first["run"].returncode == 0, first["run"].stdout + first["run"].stderr
    assert injured(github) == {"1"}                        # the phone changed nothing
    assert remote_file(github, "gh-pages", "index.html") == "site by mac"
    assert remote(github, "publish-lock") is None


def test_simultaneous_starts_never_both_publish_at_once(github):
    mac, phone = github("mac"), github("phone")
    runs = {}
    threads = [threading.Thread(target=lambda d=d, p=p: runs.update({d.name: publish(d, FAKE_SLEEP=2, FAKE_PLAYER=p)}))
               for d, p in ((mac, "1"), (phone, "2"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.returncode == 0 for r in runs.values()), {k: r.stdout + r.stderr for k, r in runs.items()}
    published = [name for name, r in runs.items() if "published" in r.stdout]
    skipped = [name for name, r in runs.items() if "skipped" in r.stdout]
    assert len(published) == 1 and len(skipped) == 1
    assert remote_file(github, "gh-pages", "index.html") == f"site by {published[0]}"
    assert remote(github, "publish-lock") is None


def test_an_expired_lock_is_taken_over(github):
    mac = github("mac")
    empty = git(mac, "hash-object", "-w", "-t", "tree", "/dev/null")
    stale = git(mac, "commit-tree", empty, "-m", "publish lock: phone until 1000")
    git(mac, "push", "-q", "origin", f"{stale}:refs/heads/publish-lock")
    run = publish(mac, FAKE_PLAYER="7")
    assert run.returncode == 0 and "taking over an expired lock" in run.stdout and "published" in run.stdout
    assert remote(github, "publish-lock") is None


def test_a_valid_lock_is_respected(github):
    mac = github("mac")
    empty = git(mac, "hash-object", "-w", "-t", "tree", "/dev/null")
    held = git(mac, "commit-tree", empty, "-m", "publish lock: phone until 9999999999")
    git(mac, "push", "-q", "origin", f"{held}:refs/heads/publish-lock")
    run = publish(mac, FAKE_PLAYER="7")
    assert run.returncode == 0 and "skipped: phone is publishing (lock until" in run.stdout
    assert remote(github, "publish-lock") == held       # not ours to release
    assert remote(github, "gh-pages") is None


def test_losing_the_lock_mid_build_pushes_nothing(github):
    mac = github("mac")
    run = publish(mac, FAKE_PLAYER="1", FAKE_STEAL_LOCK=1)
    assert run.returncode == 1 and "lost the publish lock" in run.stdout
    assert injured(github) == set() and remote(github, "gh-pages") is None


def test_failed_build_publishes_only_health(github):
    mac, phone = github("mac"), github("phone")
    assert publish(mac, FAKE_PLAYER="1").returncode == 0
    site_before = git(github.origin, "ls-tree", "-r", "gh-pages")
    main_before = remote(github, "main")
    run = publish(phone, FAKE_EXIT=1)
    assert run.returncode == 1 and "published health.json only" in run.stdout
    assert remote(github, "main") == main_before
    assert remote_file(github, "gh-pages", "index.html") == "site by mac"          # the site is unchanged
    assert json.loads(remote_file(github, "gh-pages", "api/health.json"))["status"] == "failed"
    changed = {line.split()[-1] for line in git(github.origin, "ls-tree", "-r", "gh-pages").splitlines()} ^ \
              {line.split()[-1] for line in site_before.splitlines()}
    assert changed <= {"api/health.json"}
    assert remote(github, "publish-lock") is None


def test_diverged_history_is_merged_not_jammed(github):
    mac, phone = github("mac"), github("phone")
    # the Mac recorded something but never managed to push it (e.g. the network dropped)
    path = mac / "data" / "injuries.json"
    doc = json.loads(path.read_text())
    doc["players"]["500"] = {"episodes": [{"start": "2026-09-01", "end": None, "lastSeen": "2026-09-01",
                                           "updates": [{"date": "2026-09-01", "status": "out"}]}]}
    path.write_text(json.dumps(doc, indent=2) + "\n")
    git(mac, "commit", "-q", "-am", "Record lineups and injuries")
    # meanwhile the phone published its own change to the same file
    assert publish(phone, FAKE_PLAYER="600").returncode == 0
    run = publish(mac, FAKE_PLAYER="700")
    assert run.returncode == 0, run.stdout + run.stderr
    assert injured(github) == {"500", "600", "700"}      # nothing lost from either device
    assert not (mac / ".git" / "rebase-merge").exists()


def test_an_unfinished_rebase_is_cleaned_up(github):
    mac = github("mac")
    (mac / ".git" / "rebase-merge").mkdir()
    (mac / ".git" / "rebase-merge" / "head-name").write_text("refs/heads/main\n")
    run = publish(mac, FAKE_PLAYER="1")
    assert "cleaning up an unfinished rebase" in run.stdout
    assert run.returncode == 0, run.stdout + run.stderr
    assert injured(github) == {"1"}


def test_dry_run_changes_nothing(github):
    mac = github("mac")
    run = publish(mac, DRY_RUN=1, FAKE_PLAYER="1")
    assert run.returncode == 0 and "would publish site tree" in run.stdout
    assert remote(github, "gh-pages") is None and remote(github, "publish-lock") is None
    assert injured(github) == set()
