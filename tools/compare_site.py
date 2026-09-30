"""Compare two builds file by file.

    python3 tools/compare_site.py A B [--only api] [--quiet]

A and B are export folders (e.g. two tools/replay_export.py outputs, or two site/ folders).
Reports files only in one of them and files whose bytes differ; for JSON files it also
shows the first few paths inside the document where the values differ. Exit code 0 when
everything is identical, 1 otherwise.
"""
import argparse
import json
import sys
from pathlib import Path


def files(root, only):
    base = root / only if only else root
    return {str(p.relative_to(root)): p for p in sorted(base.rglob("*")) if p.is_file()}


def json_diff(a, b, path="$", out=None, limit=5):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: {json.dumps(a)[:80]} != {json.dumps(b)[:80]}")
    elif isinstance(a, dict):
        if list(a) != list(b):
            only_a, only_b = set(a) - set(b), set(b) - set(a)
            out.append(f"{path}: keys differ (only A: {sorted(only_a)[:5]}, only B: {sorted(only_b)[:5]}"
                       + (", same keys in another order" if not only_a and not only_b else "") + ")")
        for k in a:
            if k in b:
                json_diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {json.dumps(a, ensure_ascii=False)[:80]} != {json.dumps(b, ensure_ascii=False)[:80]}")
    return out


def compare(a_root, b_root, only=None, quiet=False, show=20):
    a, b = files(a_root, only), files(b_root, only)
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    changed = [rel for rel in sorted(set(a) & set(b)) if a[rel].read_bytes() != b[rel].read_bytes()]
    same = len(set(a) & set(b)) - len(changed)
    if not quiet:
        for rel in only_a[:show]:
            print(f"only in A: {rel}")
        for rel in only_b[:show]:
            print(f"only in B: {rel}")
        for rel in changed[:show]:
            print(f"differs:   {rel}")
            if rel.endswith(".json"):
                try:
                    for line in json_diff(json.loads(a[rel].read_text()), json.loads(b[rel].read_text())):
                        print(f"    {line}")
                except ValueError:
                    pass
    print(f"{same} identical, {len(changed)} differ, {len(only_a)} only in A, {len(only_b)} only in B")
    return not (changed or only_a or only_b)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--only", help="compare only this subfolder, e.g. site/api")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    sys.exit(0 if compare(Path(args.a), Path(args.b), args.only, args.quiet) else 1)


if __name__ == "__main__":
    main()
