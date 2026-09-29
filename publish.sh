#!/bin/bash
# Rebuilds the public site and uploads it to GitHub Pages: every 15 minutes on the Mac, or
# on demand from an Android phone (phone/setup.sh adds a home-screen button for it).
#
#   ./publish.sh        (a launch agent runs this every 15 minutes, see below)
#
# BasketNews refuses requests from GitHub's servers, so the data has to be fetched
# here. Steps: take any edits made on GitHub (e.g. leagues.json), run export.py,
# commit the recorded lineups / injury log to main, and publish ./site as a single
# fresh commit on the gh-pages branch, which GitHub Pages serves.
#
# Background job:
#   install:  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.viliusjaz.fantasy-tracker.plist
#   remove:   launchctl bootout gui/$(id -u)/com.viliusjaz.fantasy-tracker
#   log:      ~/Library/Logs/fantasy-tracker.log
set -euo pipefail
cd "$(dirname "$0")"
# keep the caller's PATH (Termux on Android has its own); launchd on the Mac gives a minimal one
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH:/usr/bin:/bin:/usr/sbin:/sbin"

LOG="$HOME/Library/Logs/fantasy-tracker.log"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ]; then : > "$LOG"; fi  # keep the log small
echo "=== $(date '+%Y-%m-%d %H:%M:%S')"
git pull -q --rebase --autostash origin main

python3 export.py

git add data leagues.json
if ! git diff --cached --quiet; then
  git commit -q -m "Record lineups and injuries"
fi
git push -q origin main

# site/ -> gh-pages without touching the working tree: build the commit from a
# throwaway index. Keeping the previous commit as a local ref lets git upload
# only the files that changed.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export GIT_INDEX_FILE="$tmp/index"
git --work-tree=site add -A
tree="$(git write-tree)"
unset GIT_INDEX_FILE
commit="$(git commit-tree "$tree" -m "Site $(date '+%Y-%m-%d %H:%M')")"
git push -q -f origin "$commit:refs/heads/gh-pages"
git update-ref refs/heads/gh-pages "$commit"
echo "published $commit"
