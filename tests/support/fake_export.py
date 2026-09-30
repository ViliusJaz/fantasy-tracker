"""Stand-in for export.py in the publish.sh tests (tests/test_publish.py).

Environment:
  FAKE_WHO         name written into the site ("site by <who>")
  FAKE_PLAYER      add this player id to data/injuries.json
  FAKE_SLEEP       seconds to take
  FAKE_EXIT        fail like a build that did not pass validation
  FAKE_STEAL_LOCK  another device takes the publish lock while this build runs
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path.cwd()
who = os.environ.get("FAKE_WHO", "?")
time.sleep(float(os.environ.get("FAKE_SLEEP") or 0))
if os.environ.get("FAKE_STEAL_LOCK"):
    tree = subprocess.check_output(["git", "hash-object", "-w", "-t", "tree", "/dev/null"], text=True).strip()
    other = subprocess.check_output(["git", "commit-tree", tree, "-m", "publish lock: other until 9999999999"],
                                    text=True).strip()
    subprocess.run(["git", "push", "-q", "--force", "origin", f"{other}:refs/heads/publish-lock"], check=True)

failed = bool(os.environ.get("FAKE_EXIT"))
health = {"status": "failed" if failed else "healthy", "by": who}
(root / "var").mkdir(exist_ok=True)
(root / "var" / "health.json").write_text(json.dumps(health) + "\n")
if failed:
    sys.exit(1)

if os.environ.get("FAKE_PLAYER"):
    path = root / "data" / "injuries.json"
    doc = json.loads(path.read_text())
    doc["players"][os.environ["FAKE_PLAYER"]] = {"episodes": [{"start": "2026-09-30", "end": None,
                                                               "lastSeen": "2026-09-30",
                                                               "updates": [{"date": "2026-09-30", "status": "out"}]}]}
    path.write_text(json.dumps(doc, indent=2) + "\n")

site = root / "site"
(site / "api").mkdir(parents=True, exist_ok=True)
(site / "index.html").write_text(f"site by {who}")
(site / "api" / "health.json").write_text(json.dumps(health) + "\n")
