"""The SQLite history index from the command line.

    python3 -m backend.storage status            schema version, files and rows per table
    python3 -m backend.storage refresh           load what changed in data/
    python3 -m backend.storage rebuild           delete the database and load everything again
    python3 -m backend.storage sql "SELECT ..."  run a query (read-only)
"""
import argparse
import sqlite3

from backend.storage import db, ingest

TABLES = ("leagues", "teams", "players", "player_rounds", "games", "advanced_rounds", "standings", "matchups",
          "transfers", "transfer_players", "draft_picks", "lineups", "lineup_slots", "injury_episodes",
          "injury_updates")


def main():
    parser = argparse.ArgumentParser(prog="python3 -m backend.storage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=("status", "refresh", "rebuild", "sql"))
    parser.add_argument("query", nargs="?")
    args = parser.parse_args()
    if args.cmd == "rebuild":
        db.remove()
    conn = db.connect()
    if args.cmd in ("refresh", "rebuild"):
        print(ingest.refresh(conn, force=args.cmd == "rebuild"))
    if args.cmd == "sql":
        ro = sqlite3.connect(f"file:{db.path()}?mode=ro", uri=True)
        cur = ro.execute(args.query or "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        print("\t".join(d[0] for d in cur.description))
        for row in cur.fetchall():
            print("\t".join("" if v is None else str(v) for v in row))
        return
    print(f"{db.path()}  schema version {db.schema_version(conn)}")
    print(f"files loaded: {conn.execute('SELECT COUNT(*) FROM ingested').fetchone()[0]}")
    for table in TABLES:
        print(f"  {table:18} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:7} rows")


if __name__ == "__main__":
    main()
