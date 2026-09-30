"""Golden output: the offline export of the recording must stay byte-for-byte the same.

tests/golden/recording-r1.sha256 lists a hash of every file the export writes under
site/api (except health.json, whose build times differ) and of data/ + leagues.json after
the run. A refactor that should not change behaviour must keep this test green. When a change is meant to alter the output, check the
differences (python3 tools/compare_site.py) and regenerate the list with:

    UPDATE_GOLDEN=1 python3 -m pytest tests/test_golden.py
"""
import hashlib
import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "recording-r1.sha256"


def manifest(out):
    rows = {}
    for base in ("site/api", "state"):
        for path in sorted((out / base).rglob("*")):
            if path.is_file() and path.name != "health.json":  # build times differ run to run
                rows[str(path.relative_to(out))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def test_export_matches_golden(replayed):
    out, report = replayed
    got = manifest(out)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text("".join(f"{h}  {rel}\n" for rel, h in got.items()))
        pytest.skip(f"wrote {GOLDEN.name} ({len(got)} files)")
    expected = dict(reversed(line.split("  ", 1)) for line in GOLDEN.read_text().splitlines())
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    changed = sorted(rel for rel in set(got) & set(expected) if got[rel] != expected[rel])
    assert not (missing or extra or changed), (
        f"{len(changed)} changed, {len(missing)} missing, {len(extra)} new -- first: "
        f"{(changed + missing + extra)[:10]} (compare with tools/compare_site.py)")
