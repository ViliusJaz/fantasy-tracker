#!/bin/bash
# Rebuilds the public site and uploads it to GitHub Pages: every 15 minutes on the Mac, or
# on demand from an Android phone (phone/setup.sh adds a home-screen button for it).
#
#   ./publish.sh              (a launch agent runs this every 15 minutes, see below)
#   DRY_RUN=1 ./publish.sh    builds everything and shows what would be committed and
#                             published, but changes no branch and pushes nothing
#
# BasketNews refuses requests from GitHub's servers, so the data has to be fetched
# here. Steps: take the publish lock, take any edits made on GitHub, run export.py, commit
# the recorded lineups / injury log to main, and publish ./site as a single fresh commit on
# the gh-pages branch, which GitHub Pages serves.
#
# Mac and phone can both run this, so publishing is guarded on GitHub itself:
# - The lock is a `publish-lock` branch, taken with an atomic compare-and-swap push
#   (--force-with-lease: only if it does not exist, or still holds the expired lock we saw).
#   Its commit says who holds it and until when (LOCK_MINUTES). Another device finding a
#   valid lock skips its run without downloading anything; an expired lock is taken over.
# - Before each push the lock must still be ours; gh-pages is pushed with a lease on the
#   commit seen at the start, so an older build can never replace a newer one.
# - History files merge by content (tools/merge_history.py via .gitattributes) and a run that
#   died mid-rebase is cleaned up first, so a leftover local commit cannot jam later runs.
# - A build that fails validation publishes only api/health.json on top of the current site.
#
# Background job:
#   install:  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.viliusjaz.fantasy-tracker.plist
#   remove:   launchctl bootout gui/$(id -u)/com.viliusjaz.fantasy-tracker
#   log:      ~/Library/Logs/fantasy-tracker.log
set -euo pipefail
cd "$(dirname "$0")"
# keep the caller's PATH (Termux on Android has its own); launchd on the Mac gives a minimal one
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH:/usr/bin:/bin:/usr/sbin:/sbin"

LOCK_REF=refs/heads/publish-lock
LOCK_MINUTES=10
EXPORT_CMD=${FT_EXPORT:-python3 export.py}   # tests put a stand-in here
DRY_RUN=${DRY_RUN:-}
LOCK_SHA=""
TMP="$(mktemp -d)"

say() { echo "$(date '+%H:%M:%S') [PUBLISH] $*"; }

device() {
  case "${PREFIX:-}" in
    *com.termux*) echo phone ;;
    *) if [ "$(uname)" = Darwin ]; then echo mac; else echo other; fi ;;
  esac
}

cleanup() {
  release_lock
  rm -rf "$TMP"
}

acquire_lock() {
  local now current expect holder until lock_tree
  now=$(date +%s)
  current=$(git ls-remote origin "$LOCK_REF" | cut -f1)
  expect=""
  if [ -n "$current" ]; then
    git fetch -q origin "+$LOCK_REF:refs/ft/lock-seen"
    holder=$(git log -1 --format=%s refs/ft/lock-seen)
    until=$(echo "$holder" | sed -n 's/.* until \([0-9][0-9]*\).*/\1/p')
    if [ "${until:-0}" -gt "$now" ]; then
      say "skipped: ${holder#publish lock: } (another device is publishing)"
      exit 0
    fi
    say "taking over an expired lock (${holder#publish lock: })"
    expect=$current
  fi
  lock_tree=$(git hash-object -w -t tree /dev/null)
  until=$((now + LOCK_MINUTES * 60))
  LOCK_SHA=$(git commit-tree "$lock_tree" -m "publish lock: $(device) until $until")
  if ! git push -q --force-with-lease="$LOCK_REF:$expect" origin "$LOCK_SHA:$LOCK_REF" 2>/dev/null; then
    LOCK_SHA=""
    say "skipped: another device took the lock a moment ago"
    exit 0
  fi
}

still_locked() {
  [ -z "$DRY_RUN" ] || return 0
  if [ "$(git ls-remote origin "$LOCK_REF" | cut -f1)" != "$LOCK_SHA" ]; then
    say "lost the publish lock (the run took over $LOCK_MINUTES minutes); nothing pushed"
    LOCK_SHA=""
    exit 1
  fi
}

release_lock() {
  if [ -n "$LOCK_SHA" ]; then
    git push -q --force-with-lease="$LOCK_REF:$LOCK_SHA" origin ":$LOCK_REF" 2>/dev/null || true
    LOCK_SHA=""
  fi
}

push_main() {
  if git push -q origin main 2>/dev/null; then
    return 0
  fi
  say "main changed on GitHub meanwhile; merging and pushing again"
  git pull -q --rebase --autostash origin main
  still_locked
  git push -q origin main
}

# The last good site with only api/health.json replaced: visitors keep the previous data,
# and the failure is visible without hiding when the data was last good.
publish_health_only() {
  local seen=$1 tree commit
  [ -f var/health.json ] && [ -n "$seen" ] || return 0
  git fetch -q origin "+refs/heads/gh-pages:refs/ft/pages-seen" || return 0
  [ "$(git rev-parse refs/ft/pages-seen)" = "$seen" ] || return 0
  export GIT_INDEX_FILE="$TMP/health-index"
  git read-tree refs/ft/pages-seen
  git update-index --add --cacheinfo "100644,$(git hash-object -w var/health.json),api/health.json"
  tree="$(git write-tree)"
  unset GIT_INDEX_FILE
  commit="$(git commit-tree "$tree" -m "Site $(date '+%Y-%m-%d %H:%M') (build failed: health only)")"
  still_locked
  if git push -q --force-with-lease="refs/heads/gh-pages:$seen" origin "$commit:refs/heads/gh-pages"; then
    git update-ref refs/heads/gh-pages "$commit"
    say "published health.json only (the site itself is unchanged)"
  fi
}

main() {
  local log="$HOME/Library/Logs/fantasy-tracker.log" pages_seen tree commit
  if [ -f "$log" ] && [ "$(wc -c < "$log")" -gt 1000000 ]; then : > "$log"; fi  # keep the log small
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') $(device)${DRY_RUN:+ (dry run)}"
  trap cleanup EXIT

  # A run killed in the middle of a rebase would otherwise make every later pull fail.
  if [ -d "$(git rev-parse --git-path rebase-merge)" ] || [ -d "$(git rev-parse --git-path rebase-apply)" ]; then
    say "cleaning up an unfinished rebase from an earlier run"
    git rebase --abort || true
  fi
  git config merge.ft-history.name "JSON history merge (tools/merge_history.py)"
  git config merge.ft-history.driver "python3 tools/merge_history.py %O %A %B %P"

  if [ -z "$DRY_RUN" ]; then
    acquire_lock
    git pull -q --rebase --autostash origin main
  fi
  pages_seen=$(git ls-remote origin refs/heads/gh-pages | cut -f1)

  export FT_DEVICE="${FT_DEVICE:-$(device)}"
  if ! $EXPORT_CMD; then
    say "build failed; the site stays as it was"
    if [ -z "$DRY_RUN" ]; then publish_health_only "$pages_seen"; fi
    exit 1
  fi

  if [ -n "$DRY_RUN" ]; then
    echo "would commit:"
    git status --short -- data leagues.json
  else
    git add data leagues.json
    if ! git diff --cached --quiet; then
      git commit -q -m "Record lineups and injuries"
    fi
    still_locked
    push_main
  fi

  # site/ -> gh-pages without touching the working tree: build the commit from a
  # throwaway index. Keeping the previous commit as a local ref lets git upload
  # only the files that changed.
  export GIT_INDEX_FILE="$TMP/index"
  git --work-tree=site add -A
  tree="$(git write-tree)"
  unset GIT_INDEX_FILE
  if [ -n "$DRY_RUN" ]; then
    echo "would publish site tree $tree ($(git ls-tree -r "$tree" | wc -l | tr -d ' ') files)"
    exit 0
  fi
  commit="$(git commit-tree "$tree" -m "Site $(date '+%Y-%m-%d %H:%M')")"
  still_locked
  git push -q --force-with-lease="refs/heads/gh-pages:$pages_seen" origin "$commit:refs/heads/gh-pages"
  git update-ref refs/heads/gh-pages "$commit"
  say "published $commit"
}

# on one line, so bash has read it before `git pull` may rewrite this file
main "$@"; exit
