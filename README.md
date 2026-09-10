# Games order watcher

Polls a game store's public Facebook page every 5 minutes and pushes an
[ntfy](https://ntfy.sh) notification to your phone when a new post mentions one
of your keywords. Built for catching pre-orders and launch-day drops before
they sell out.

No Facebook account or login is involved.

## How it works

Facebook returns HTTP 400 to ordinary anonymous clients, but serves the full
page to crawler user-agents. `watcher.py` fetches the page that way, pulls the
posts out of the JSON embedded in the HTML (`post_id`, message text,
`creation_time`), and compares them against `state/seen.json`.

Anything new that matches `keywords.txt` becomes a notification with the post
text and a direct link. Everything else is silently recorded as seen.

The store edits posts in place to mark stock state — `(Agotados)`,
`(Reserva cerrada)`, `(Últimas 2 unidades)`. If a post is *already* marked when
the watcher first sees it, the alert is downgraded and titled `[YA CERRADO]`,
so you can tell at a glance that you were too slow. A run of those means the
polling interval needs tightening.

## Setup

Pick a notification backend. If both are configured, Telegram wins.

### Telegram (recommended)

Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, and
follow the prompts. It replies with a token like `8123456789:AAF...`. Then open
a chat with your new bot and send it any message — a bot cannot message you
first, so this step is required.

Add two repo secrets: `TELEGRAM_BOT_TOKEN` (the token) and `TELEGRAM_CHAT_ID`
(your numeric chat id). To find the chat id after messaging the bot:

```sh
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['result'][-1]['message']['chat']['id'])"
```

Telegram is free with no message limits that matter here, and its iOS push is
far more reliable than ntfy's — ntfy on iPhone depends on a third-party server
holding your APNs device token, which is a common point of failure.

### ntfy (fallback)

**1. Pick an ntfy topic.** It's a password, not a username — anyone who knows it
can read your alerts. Use something unguessable:

```sh
python3 -c "import secrets; print('games-'+secrets.token_hex(8))"
```

**2. Subscribe on your phone.** Install ntfy ([iOS](https://apps.apple.com/app/ntfy/id1625396347),
[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)),
tap **+**, and enter that topic name. Leave the server as `ntfy.sh`.

**3. Keep the repo public.** GitHub bills private-repo Actions **rounded up to
the nearest minute per job**, so a 20-second run costs a full minute:

| | runs/day | billed min/month | Free tier (2,000) |
|---|---|---|---|
| Private, every 5 min | 144 | ~4,320 | **over by ~2,320 (~$18/mo)** |
| Private, every 15 min | 48 | ~1,440 | fits |
| **Public, every 5 min** | 144 | unlimited | **free** |

Public repos get unlimited Actions minutes, so the 5-minute schedule is only
free on a public repo. The ntfy topic lives in a repo secret, and secrets are
never exposed to forks or pull requests. The tradeoff is that Actions logs
(post text, which keywords matched) are publicly readable.

To go private instead, change the cron in `.github/workflows/watch.yml` from
`*/5` to `*/15` to stay inside the free tier.

**4. Add the secret.** Repo → Settings → Secrets and variables → Actions → New
repository secret, named `NTFY_TOPIC`, set to your topic string.

**5. Prime it — only if the committed state is stale.** `state/seen.json` ships
already primed with the 20 posts visible on 2026-09-10, so setting this up now
gives you no backlog. If it's been a while, re-prime first: Actions →
*Watch game store* → Run workflow → check **prime** → Run. That marks everything
currently visible as seen without alerting, so you only hear about genuinely
new posts.

## Testing that notifications reach your phone

Three ways, no state changed and nothing marked as seen by any of them:

```sh
NTFY_TOPIC=your-topic python3 watcher.py --test    # from your machine
```

From GitHub (works from your phone via the GitHub app): Actions →
*Watch game store* → Run workflow → check **test** → Run.

Or the raw one-liner, which tests only ntfy and not the watcher:

```sh
curl -H "Title: Prueba" -H "Priority: high" -d "Test" ntfy.sh/your-topic
```

`--test` re-sends the newest real post through the normal notification path, so
it exercises the actual title, priority, link and formatting — not a dummy
string.

## Alerting on every post

Set the **`ALERT_ALL` repo variable** to `1` (Settings → Secrets and variables →
Actions → *Variables*) to be notified about every new post regardless of
keywords. Those alerts are labeled `post nuevo`; posts that *do* match a keyword
still show the keyword.

Set it to `0` or delete it to go back to keyword-only. It's a variable rather
than a code change, so flipping it takes no commit and no redeploy.

## Editing keywords

Edit `keywords.txt` and push. One per line, case- and accent-insensitive, so
`pokemon` matches `Pokémon`. Substring matching, so `zelda` matches
`The Legend of Zelda`. Prefix a line with `re:` for a raw regex.

The file ships with `zelda`, `pokemon`, and `resident evil` active, plus a
commented list of other suggestions and the store's own hashtags
(`#Reserva`, `#LanzamientoMundial`, `#HotPrice`) if you'd rather catch every
drop of a kind than specific titles.

## Watching a different page

Change `PAGE_SLUG` at the top of `watcher.py` to the page's Facebook slug, then
run `python3 watcher.py --prime` to reset the state.

## Local use

```sh
python3 watcher.py --dry-run --force   # print what it would send
python3 watcher.py --prime             # mark current posts seen, alert nothing
python3 watcher.py --force             # real run, ignoring the hours window
```

`--force` bypasses the 10:30–21:30 Bogotá window. Dry runs never write state,
so testing won't suppress a real alert. No dependencies; stdlib only.

## Known limitations

**GitHub Actions cron is not punctual.** Scheduled runs are best-effort and get
delayed under load, sometimes 5–15 minutes, worst around the top of the hour.
The 5-minute schedule is a ceiling, not a guarantee. If the `[YA CERRADO]`
alerts pile up, move this to a small always-on VPS where cron is exact.

**The crawler-UA access path is unofficial.** It sends a user-agent we aren't,
which is a Facebook ToS gray area. Nothing is tied to any account, so the
personal risk is nil, but Meta could start verifying crawler IPs and break it
without notice. The watcher alerts you (`Game watcher is broken`) after 3
consecutive polls that return nothing, so it fails loudly rather than silently
going quiet. If that happens, the fallback is a real browser session via
Playwright with saved cookies.

**Feed freshness is partly verified.** On 2026-09-10 the crawler-facing view
showed the same newest post as the Facebook app, so it isn't serving a stale
cache. What that check *can't* tell us is whether the view lags by seconds or
by minutes, since both sides were idle.

Every alert therefore carries its own age (`publicado hace 7 min`), and CI logs
print `lag=Nmin`. That number is cache lag plus cron lag combined — the one that
actually matters. Watch the first few real alerts: consistently under ~10 min is
working as intended; consistently higher points at GitHub's cron scheduler, and
the fix is an always-on VPS with real cron.

**Scheduled workflows auto-disable after 60 days of repo inactivity.** State
commits count as activity, so as long as the page posts occasionally this stays
alive on its own.
