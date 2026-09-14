# Games order watcher

Watches a game store's public Facebook page and sends a Telegram message when a
new post appears, so pre-orders and launch-day drops don't get missed while
Facebook's own notifications lag.

No Facebook account or login is involved.

---

## Daily use

Everything below runs from the repo directory.

```sh
./status.sh                 # is it running? when did it last poll?
tail -f ~/Library/Logs/games-watcher.log     # live feed
```

### Start / stop the local watcher

```sh
# stop (survives reboots, so this is the real off switch)
launchctl bootout gui/$(id -u)/com.josemariogutierrez.gameswatcher

# start again
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.josemariogutierrez.gameswatcher.plist

# poll right now without waiting for the next 5-minute tick
launchctl kickstart -k gui/$(id -u)/com.josemariogutierrez.gameswatcher

# is it loaded?
launchctl list | grep gameswatcher
```

`launchctl print gui/$(id -u)/com.josemariogutierrez.gameswatcher` shows detail.
Note that `state = not running` there is **normal**: the job runs ~3 seconds
every 5 minutes and is idle the rest of the time. Health is `runs` climbing and
`last exit code = 0`.

### Send a test notification

```sh
set -a && . ./.env && set +a && python3 watcher.py --test
```

Re-sends the newest real post through the normal alert path, so it exercises the
true formatting. Touches no state. From a phone: GitHub → Actions → *Watch game
store* → Run workflow → check **test**.

### Resource use

Measured, not estimated: **31 MB peak RAM for ~3 seconds per poll**, then the
process exits — nothing persists between polls, so it cannot leak. The log grows
~24 KB/day (~8 MB/year).

---

## How it works

Facebook returns HTTP 400 to ordinary anonymous clients but serves the full page
to crawler user-agents. `watcher.py` fetches it that way, pulls posts out of the
JSON embedded in the HTML (`post_id`, message text, `creation_time`), and
compares them against `state/seen.json`.

Anything new that matches `keywords.txt` becomes a Telegram message with the post
text, its age, and a direct link. Everything else is recorded silently.

The store edits posts in place to mark stock state — `(Agotados)`,
`(Reserva cerrada)`, `(Últimas 2 unidades)`. A post already marked when first
seen is titled `[YA CERRADO]` and downgraded, so a run of those means detection
is too slow.

Python 3 standard library only — no dependencies, no install step.

---

## Why it runs in two places

| | Mac (primary) | GitHub Actions (backup) |
|---|---|---|
| Polls | every 5 min via `launchd` | every 5 min inside a long-running job |
| Facebook blocking | **0%** measured | **~52%** of polls, in 40–85 min stretches |
| Timing | exact | start delayed 2–4h; one post arrived 151 min late |
| Runs when Mac sleeps | no | yes |

Facebook blocks GitHub's datacenter IPs but not a residential one, so the Mac is
the reliable watcher and GitHub covers the hours the Mac is asleep.

Both share `state/seen.json` through git and **both pull before polling**, so
whichever sees a post first records it and the other stays quiet. Only the
seen-posts map is shared; failure counters stay per-machine, since the Mac is not
blind when GitHub is.

GitHub attempts 36 starts a day at `:07/:27/:47` — deliberately odd minutes,
since `:00` and `:30` are the most congested and were being delayed for hours.
Each job then polls every 5 minutes internally until 21:30 Bogotá or 5 hours
elapse, which is immune to cron drop once running.

### A caveat about Mac sleep

`launchd` cannot poll while the Mac is asleep, and this Mac sleeps after 1 minute
idle, which already produced a 20-minute gap. To hold the window properly:

```sh
sudo pmset -c sleep 0     # only while plugged in; battery and display unaffected
```

---

## Configuration

### Keywords

Edit `keywords.txt`. One per line, case- and accent-insensitive (`pokemon`
matches `Pokémon`), substring-based (`zelda` matches `The Legend of Zelda`).
Prefix with `re:` for a raw regex. Ships with `zelda`, `pokemon`,
`resident evil` plus commented suggestions and the store's own hashtags
(`#Reserva`, `#LanzamientoMundial`, `#HotPrice`).

### Alert on every post

Currently **on**. It ignores keywords and alerts on everything, labeled
`post nuevo`.

- Mac: `ALERT_ALL=1` in `.env` — set to `0` for keyword-only
- GitHub: the `ALERT_ALL` repo variable (Settings → Secrets and variables →
  Actions → Variables)

Change both, or they'll disagree.

### Secrets

`.env` beside `watcher.py`, gitignored, `chmod 600`:

```sh
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ALERT_ALL=1
```

GitHub reads the same values from repo secrets. ntfy is still supported as a
fallback via `NTFY_TOPIC`; Telegram wins when both are set.

### Active hours

10:30–21:30 Bogotá, set by `ACTIVE_START` / `ACTIVE_END` in `watcher.py`.
Outside it the watcher exits immediately, so no scheduler rules are needed.

### Watching a different page

Change `PAGE_SLUG` at the top of `watcher.py`, then `python3 watcher.py --prime`
to reset state.

---

## Setup from scratch

1. **Telegram bot** — message [@BotFather](https://t.me/BotFather), send
   `/newbot`, keep the token. Then message your new bot once; bots cannot
   message you first. Get the chat id:

   ```sh
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" \
     | python3 -c "import sys,json;print(json.load(sys.stdin)['result'][-1]['message']['chat']['id'])"
   ```

2. **Local `.env`** with those two values, then `chmod 600 .env`.

3. **Install the launchd agent:**

   ```sh
   cp com.josemariogutierrez.gameswatcher.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.josemariogutierrez.gameswatcher.plist
   ```

   The plist embeds absolute paths, so regenerate it if the repo moves.

4. **GitHub backup** — keep the repo **public** (Actions minutes are unlimited
   there; private would bill ~4,320 min/month against a 2,000 free tier). Add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repo secrets.

5. **Prime** if the committed state is stale: Actions → Run workflow → check
   **prime**. Marks everything currently visible as seen without alerting.

---

## Command reference

```sh
./status.sh                            # health of both watchers
./run-local.sh                         # one manual poll (pull, poll, push)
python3 watcher.py --test              # send a test notification
python3 watcher.py --dry-run --force   # print what would be sent, write nothing
python3 watcher.py --prime             # mark current posts seen, alert nothing
python3 watcher.py --force             # real poll, ignoring the hours window
```

Dry runs never write state, so testing cannot suppress a real alert.

---

## Known limitations

**Facebook blocks datacenter IPs.** Measured at 52% of GitHub polls, in stretches
of 40–85 minutes that clear on their own, plus one 17-hour spell. A residential
IP was unblocked in 6/6 tests during one of those blocks. This is why the Mac is
primary. The block serves a 454 KB login wall titled `Facebook` instead of the
1.8 MB real page.

**The watcher alerts only when genuinely blind.** Short blocks are normal, so the
alert is time-based: one message per spell, only after the page has been
unreadable for 3 hours, cleared on recovery. It means the page could not be
*read* — not that the store hasn't posted.

**GitHub's scheduler is best-effort.** Starts were delayed 137–229 minutes on
every measured day. The 36 odd-minute attempts reduce the worst case to ~20
minutes, but there is no delivery guarantee.

**The crawler user-agent is an unofficial access path.** Nothing is tied to any
account, but Meta could start verifying crawler IPs and break it. The fallback
would be a real browser session via Playwright.

**Scheduled workflows auto-disable after 60 days of repo inactivity.** State
commits count as activity.
