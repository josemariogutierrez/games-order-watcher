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

# watcher.py alerts about its own problems, but it cannot alert if python3
# itself will not run (an unaccepted Xcode license broke every poll for hours,
# silently, because the alerting code was the thing that was broken). curl does
# not depend on the Xcode toolchain, so use it directly. Rate-limited so a
# persistent breakage does not message every 5 minutes.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
  log "ERROR: watcher.py exited $rc"
  marker="$REPO/state/.last-toolfail-alert"
  now=$(date +%s)
  last=$(cat "$marker" 2>/dev/null || echo 0)
  if [ "$(( now - last ))" -gt 21600 ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    curl -s -m 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d chat_id="${TELEGRAM_CHAT_ID:-}" \
      --data-urlencode text="El watcher local no puede ejecutarse (codigo $rc). No estas recibiendo alertas desde el Mac. Revisa: tail ~/Library/Logs/games-watcher.log" \
      >/dev/null && echo "$now" > "$marker" && log "sent tool-failure alert"
  fi
fi

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
