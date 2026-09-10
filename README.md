# Smile Games watcher

Polls the [Smile Games](https://www.facebook.com/SmileGamesBta/) Facebook page
every 5 minutes and pushes an [ntfy](https://ntfy.sh) notification to your phone
when a new post mentions one of your keywords.

No Facebook account or login is involved.

## How it works

Facebook returns HTTP 400 to ordinary anonymous clients, but serves the full
page to crawler user-agents. `watcher.py` fetches the page that way, pulls the
posts out of the JSON embedded in the HTML (`post_id`, message text,
`creation_time`), and compares them against `state/seen.json`.

Anything new that matches `keywords.txt` becomes a notification with the post
text and a direct link. Everything else is silently recorded as seen.

Smile Games edits posts in place to mark stock state — `(Agotados)`,
`(Reserva cerrada)`, `(Últimas 2 unidades)`. If a post is *already* marked when
the watcher first sees it, the alert is downgraded and titled `[YA CERRADO]`,
so you can tell at a glance that you were too slow. A run of those means the
polling interval needs tightening.

## Setup

**1. Pick an ntfy topic.** It's a password, not a username — anyone who knows it
can read your alerts. Use something unguessable:

```sh
echo "smilegames-$(openssl rand -hex 8)"
```

**2. Subscribe on your phone.** Install ntfy ([iOS](https://apps.apple.com/app/ntfy/id1625396347),
[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)),
tap **+**, and enter that topic name. Leave the server as `ntfy.sh`.

**3. Push this to GitHub, and make the repo public.** This matters more than it
looks. GitHub bills private-repo Actions **rounded up to the nearest minute per
job**, so a 20-second run costs a full minute:

| | runs/day | billed min/month | Free tier (2,000) |
|---|---|---|---|
| Private, every 5 min | 144 | ~4,320 | **over by ~2,320 (~$18/mo)** |
| Private, every 15 min | 48 | ~1,440 | fits |
| **Public, every 5 min** | 144 | unlimited | **free** |

Public repos get unlimited Actions minutes, so the 5-minute schedule is only
free on a public repo. Nothing here is sensitive — the ntfy topic lives in a
repo secret, and secrets are never exposed to forks or pull requests. The
tradeoff is that your Actions logs (post text, which keywords matched) become
publicly readable.

If you'd rather keep it private, change the cron in `.github/workflows/watch.yml`
from `*/5` to `*/15` to stay inside the free tier.

**4. Add the secret.** Repo → Settings → Secrets and variables → Actions → New
repository secret, named `NTFY_TOPIC`, set to your topic string.

**5. Prime it — only if the committed state is stale.** `state/seen.json` ships
already primed with the 20 posts visible on 2026-09-10, so if you set this up
now you'll get no backlog. If it's been a while, re-prime first: Actions →
*Watch Smile Games* → Run workflow → check **prime** → Run. That marks
everything currently visible as seen without alerting, so you only hear about
genuinely new posts.

## Editing keywords

Edit `keywords.txt` and push. One per line, case- and accent-insensitive, so
`pokemon` matches `Pokémon`. Substring matching, so `zelda` matches
`The Legend of Zelda`. Prefix a line with `re:` for a raw regex.

The file ships with `zelda`, `pokemon`, and `resident evil` active, plus a
commented list of other suggestions and the store's own hashtags
(`#Reserva`, `#LanzamientoMundial`, `#HotPrice`) if you'd rather catch every
drop of a kind than specific titles.

## Local use

```sh
python3 watcher.py --dry-run --force   # print what it would send
python3 watcher.py --prime             # mark current posts seen, alert nothing
python3 watcher.py --force             # real run, ignoring the hours window
```

`--force` bypasses the 10:30–21:30 Bogotá window. No dependencies; stdlib only.

## Known limitations

**GitHub Actions cron is not punctual.** Scheduled runs are best-effort and get
delayed under load, sometimes 5–15 minutes, worst around the top of the hour.
The 5-minute schedule is a ceiling, not a guarantee. If the `[YA CERRADO]`
alerts pile up, move this to a small always-on VPS where cron is exact.

**The crawler-UA access path is unofficial.** It sends a user-agent we aren't,
which is a Facebook ToS gray area. Nothing is tied to your account, so the
personal risk is nil, but Meta could start verifying crawler IPs and break it
without notice. The watcher alerts you (`Smile Games watcher is broken`) after
3 consecutive polls that return nothing, so it fails loudly rather than
silently going quiet. If that happens, the fallback is a real browser session
via Playwright with saved cookies.

**Feed freshness is unverified.** The crawler-facing view may be cached rather
than live. If alerts consistently arrive later than the Facebook app's own
notifications, that cache is the reason, and no polling interval will fix it.

**Scheduled workflows auto-disable after 60 days of repo inactivity.** State
commits count as activity, so as long as the page posts occasionally this stays
alive on its own.
