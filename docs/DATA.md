# What the tracker keeps, where, and for how long

The public BasketNews API shows *current* lineups and the *current* injury report, and
can re-send finished rounds only while it keeps them. The tracker therefore records its
own history. The committed files in `data/` are the source of truth: git merges them
between the Mac and the phone. Everything in `var/` is a local copy on each device and
can be deleted at any time.

## Committed: `data/` (the history, kept forever)

| File | What | Written |
|---|---|---|
| `data/lineups/<league>.json` | Every team's lineup for every round (`locked` once the round started; `source: "import"` for rounds entered by hand) | When a fetched lineup differs from the saved one; a locked round never goes back to unlocked |
| `data/injuries.json` | The injury report turned into per-player episodes: start, end, every status / return / comment change with the time it was first seen | When the report changes, and only if the report passes validation (an empty or unreadable report never closes injuries) |
| `data/proballers.json` | Proballers links found through Wikidata (and lookups that found nothing, retried after 7 days) | After a lookup |
| `data/archive/<season>-<competition>/round-NN/players-<scoring>.json.gz` | A finished round: every player's box score, fantasy points and club, plus the games and scores | Once per finished round; the latest finished round is rewritten when its numbers change (stat corrections) |
| `data/archive/<season>-<competition>/round-NN/advanced.json.gz` | BasketNews advanced statistics of the round (values only; ranks and percentiles can be recomputed) | As above |
| `data/archive/<season>-<competition>/leagues/<league>/round-NN.json.gz` | A league's standings after the round, its head-to-head matchups and the transfers of the round (teams, players who moved, credits) | As above |
| `data/archive/<season>-<competition>/leagues/<league>/draft.json.gz` | The draft picks | When they change (i.e. once) |

`NN` counts rounds the way the site does (`round-01` is the first); inside the files
`"round"` is the 0-based index the code uses. Lineups and injuries are not repeated in the
archive, because the files above already are their history. The archive uses the tracker's
own format (backend/models.py), not BasketNews field names, so it stays readable when the
upstream API changes. It is gzip with a fixed timestamp: unchanged data never becomes a git
change. **Size:** about 40 KB per round, roughly 1.5 MB per season.

**Not kept:** public bid counts (they only exist before a round is processed), and players'
current health flags between runs (the injury report and its log cover this).

## Local: `var/` (per device, not committed, safe to delete)

| Path | What | Kept |
|---|---|---|
| `var/health.json` | The last attempt, the last success and the last failure of a build (also published as `api/health.json`) | Overwritten by every build |
| `var/cache.sqlite` | Upstream answers that no longer change: settled rounds (two or more back) for 7 days, rounds two or more ahead for 3 hours. Never: the previous, current and next round, league settings, lineups, the injury report | Until they expire (pruned every build). `python3 -m backend.cache clear`, or `FT_NO_CACHE=1` for one run |
| `var/tracker.sqlite` | SQLite index of everything in `data/` (see below) | Rebuilt from `data/` whenever a file changes. `python3 -m backend.storage rebuild` |
| `var/snapshots/live/<time>.json.gz` | Every upstream answer one build used, plus `data/` before it: the build can be replayed offline, byte for byte (`python3 tools/replay_export.py replay <file> <out>`) | The newest 12 builds, none older than 48 hours (about 1.5 MB each). Mac only; `FT_SNAPSHOTS=1` / `0` to override |

## The SQLite index

`var/tracker.sqlite` makes the history easy to query. It is derived, never edited by hand,
so it is not committed: a binary database cannot be merged when two devices change it,
and every change would add a whole new copy to the repository. Each device builds it from
`data/` after every build. Only files whose content changed are reloaded.

Tables: `leagues`, `teams` (managers), `players`, `player_rounds`, `games`,
`advanced_rounds`, `standings`, `matchups`, `transfers`, `transfer_players`, `draft_picks`,
`lineups`, `lineup_slots`, `injury_episodes`, `injury_updates`. Views: `ownership` (who held
whom in each round) and `manager_rounds` (points and place per round). Every derived row
records its `source_file`.

```bash
python3 -m backend.storage status
python3 -m backend.storage sql "SELECT title, round, points_round FROM manager_rounds ORDER BY round, points_round DESC"
```

**Schema changes** go in `backend/storage/migrations/NNN_name.sql`. `PRAGMA user_version`
records the last one applied, and a connection applies the missing ones in order, each in
its own transaction. If a change is easier as a reload, the migration can just bump the
version and the data is re-read from `data/` (`rebuild`). Either way no history is at
risk, because the database only holds copies.

## Migration from the earlier setup

Nothing was converted or deleted. `data/lineups`, `data/injuries.json` and
`data/proballers.json` keep their format and remain the history. The archive started with
the rounds that were already finished and still available from BasketNews. The lineups
and injury history from before the tracker recorded them cannot be recovered.
