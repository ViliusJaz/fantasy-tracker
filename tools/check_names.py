"""Static check for the Python files: names used but never defined (NameError waiting to
happen) and imports never used. A small stand-in for pyflakes that needs nothing installed.

    python3 tools/check_names.py [files...]      (default: server.py export.py backend/ tools/)
"""
import builtins
import symtable
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def module_names(table):
    """Names bound at module level (assignments, defs, imports, `global` assignments in functions)."""
    names = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()}

    def walk(t):
        for s in t.get_symbols():
            if s.is_declared_global() and s.is_assigned():
                names.add(s.get_name())
        for c in t.get_children():
            walk(c)
    walk(table)
    return names


def globals_used(table, out):
    for sym in table.get_symbols():
        if sym.is_referenced() and (sym.is_global() or (table.get_type() == "module")):
            out.add(sym.get_name())
    for child in table.get_children():
        globals_used(child, out)
    return out


def check(path):
    source = path.read_text(encoding="utf-8")
    table = symtable.symtable(source, str(path), "exec")
    defined = module_names(table)
    used = globals_used(table, set())
    problems = []
    for name in sorted(used - defined - set(dir(builtins)) - {"__file__", "__name__", "__doc__"}):
        problems.append(f"{path.relative_to(ROOT)}: undefined name {name}")
    imported = {s.get_name() for s in table.get_symbols() if s.is_imported()}
    # an import counts as used when any scope reads it (module-level __all__ is not used here)
    reads = set()

    def walk(t, top):
        for s in t.get_symbols():
            if s.is_referenced() and (top or s.is_global() or s.is_free()):
                reads.add(s.get_name())
        for c in t.get_children():
            walk(c, False)
    walk(table, True)
    for name in sorted(imported - reads):
        problems.append(f"{path.relative_to(ROOT)}: unused import {name}")
    return problems


def main():
    args = [Path(a).resolve() for a in sys.argv[1:]]
    if not args:
        args = [ROOT / "server.py", ROOT / "export.py", *sorted((ROOT / "backend").rglob("*.py")),
                *sorted((ROOT / "tools").glob("*.py"))]
    problems = [p for path in args for p in check(path)]
    print("\n".join(problems) or "no problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
