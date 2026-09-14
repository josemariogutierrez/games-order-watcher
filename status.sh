#!/bin/bash
# One-glance health check for both watchers.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
L="com.josemariogutierrez.gameswatcher"
LOG="$HOME/Library/Logs/games-watcher.log"
g="\033[32m"; r="\033[31m"; y="\033[33m"; n="\033[0m"

echo ""
echo "  MAC (primary)"
if launchctl list | grep -q "$L"; then
  runs=$(launchctl print "gui/$(id -u)/$L" 2>/dev/null | awk -F'= ' '/runs =/{print $2;exit}')
  code=$(launchctl print "gui/$(id -u)/$L" 2>/dev/null | awk -F'= ' '/last exit code/{print $2;exit}')
  echo -e "    agent      ${g}installed${n}  (runs: ${runs:-?}, last exit: ${code:-?})"
else
  echo -e "    agent      ${r}NOT INSTALLED${n}"
fi

if [ -f "$LOG" ]; then
  last=$(grep -oE "^[0-9-]+ [0-9]{2}:[0-9]{2} Bogota" "$LOG" | tail -1 | sed 's/ Bogota//')
  if [ -n "$last" ]; then
    mins=$(( ( $(date +%s) - $(date -j -f "%Y-%m-%d %H:%M" "$last" +%s 2>/dev/null || echo 0) ) / 60 ))
    if   [ "$mins" -le 10 ]; then c=$g; note="polling normally"
    elif [ "$mins" -le 60 ]; then c=$y; note="late - Mac may have slept"
    else                          c=$r; note="stale - Mac asleep or agent stopped"
    fi
    echo -e "    last poll  ${c}${last} Bogota (${mins} min ago)${n}  $note"
  fi
  sent=$(grep -c "MATCH" "$LOG" 2>/dev/null | tr -d '[:space:]')
  echo    "    alerts     ${sent:-0} sent (whole log)"
fi

echo ""
echo "  GITHUB (backup)"
if command -v gh >/dev/null 2>&1; then
  gh run list --limit 3 --json conclusion,createdAt,status \
    --jq '.[] | "    \(.createdAt[5:16])  \(.status)  \(.conclusion // "-")"' 2>/dev/null \
    || echo "    (could not reach GitHub)"
else
  echo "    (gh not installed)"
fi

echo ""
echo "  MODE"
alert_all=$(grep -E '^ALERT_ALL=' .env 2>/dev/null | cut -d= -f2)
[ "$alert_all" = "1" ] && echo "    every new post (ALERT_ALL=1)" \
                       || echo "    keywords only: $(grep -vE '^\s*#|^\s*$' keywords.txt | tr '\n' ' ')"
echo ""
