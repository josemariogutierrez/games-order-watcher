#!/bin/bash
# Poll once from this Mac. Driven by launchd every 5 minutes.
#
# The Mac is the primary watcher: a residential IP that Facebook does not
# block, and exact timing. GitHub Actions stays on as a backup for when this
# machine is asleep. Both share state/seen.json through git, and both pull
# before polling, so whichever sees a post first records it and the other
# stays quiet.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO" || exit 1

# launchd gives us almost no PATH.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

[ -f .env ] && set -a && . ./.env && set +a

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Start from whatever the backup may have recorded, so we don't re-alert.
git pull -q --rebase --autostash origin main 2>/dev/null \
  || log "warn: git pull failed, continuing with local state"

python3 watcher.py
rc=$?

# Commit only a real change: last_success moves every poll and would otherwise
# produce a commit every 5 minutes.
changed=$(git diff -U0 -- state/seen.json \
  | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
  | grep -vE '"last_success"|"consecutive_failures"|"blind_since"|"blind_alerted"' || true)

if [ -n "$changed" ]; then
  git add state/seen.json
  git commit -q -m "state: update seen posts (mac) [skip ci]"
  if git pull -q --rebase --autostash origin main 2>/dev/null && git push -q origin main 2>/dev/null; then
    log "state pushed"
  else
    log "warn: state push failed, will retry next run"
  fi
fi

exit $rc
