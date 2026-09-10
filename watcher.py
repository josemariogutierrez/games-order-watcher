#!/usr/bin/env python3
"""Poll a game store's Facebook page and push ntfy alerts for keyword matches.

Reads the public page, parses the embedded JSON feed, and notifies about posts
that are new to us and mention a keyword from keywords.txt.

Usage:
    python3 watcher.py                # normal run
    python3 watcher.py --prime        # record current posts, notify nothing
    python3 watcher.py --dry-run      # print what would be sent
    python3 watcher.py --test         # send one test notification
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PAGE_SLUG = "SmileGamesBta"
PAGE_URL = f"https://www.facebook.com/{PAGE_SLUG}/"

# Facebook returns HTTP 400 to ordinary clients but serves the full feed to
# crawler user-agents. This is the only access path that works without a login.
USER_AGENT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state" / "seen.json"
KEYWORDS_PATH = ROOT / "keywords.txt"

BOGOTA = timezone(timedelta(hours=-5))  # America/Bogota, no DST
ACTIVE_START = (10, 30)
ACTIVE_END = (21, 30)

SEEN_LIMIT = 500  # keep the state file small
FAILURE_ALERT_THRESHOLD = 3  # consecutive bad polls before crying for help

# The store edits posts in place to mark stock state. If a post is already
# marked when we first see it, we were too slow.
SOLD_OUT_RE = re.compile(
    r"\(\s*(agotad|reserva cerrada|vendid|sold out)", re.IGNORECASE
)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lowercase and strip accents so 'Pokémon' matches 'pokemon'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def ascii_header(text: str, limit: int = 90) -> str:
    """ntfy sends metadata in HTTP headers, which must be latin-1 safe."""
    cleaned = unicodedata.normalize("NFKD", text)
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or "Game drop"


# --------------------------------------------------------------------------
# fetching and parsing
# --------------------------------------------------------------------------


def fetch_page(url: str = PAGE_URL, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-419,es;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _walk(node, out, creation_time=None):
    """Collect (post_id, text, creation_time) from Facebook's embedded JSON.

    creation_time lives on an ancestor of the node holding the message, so we
    carry the nearest one down the tree.
    """
    if isinstance(node, dict):
        creation_time = node.get("creation_time") or creation_time
        post_id = node.get("post_id")
        message = node.get("message")
        if post_id and isinstance(message, dict) and isinstance(message.get("text"), str):
            out.append((str(post_id), message["text"], creation_time))
        for value in node.values():
            _walk(value, out, creation_time)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out, creation_time)


def parse_posts(html: str) -> list[dict]:
    blocks = re.findall(
        r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S
    )
    collected: list[tuple] = []
    for block in blocks:
        try:
            _walk(json.loads(block), collected)
        except (ValueError, RecursionError):
            continue

    posts: dict[str, dict] = {}
    for post_id, text, creation_time in collected:
        existing = posts.get(post_id)
        # Prefer the copy that carries a timestamp and the longest text.
        if existing and not (creation_time and not existing["created"]):
            if len(text) <= len(existing["text"]):
                continue
        posts[post_id] = {
            "id": post_id,
            "text": text,
            "created": creation_time,
            "url": f"https://www.facebook.com/{PAGE_SLUG}/posts/{post_id}",
        }
    return sorted(posts.values(), key=lambda p: p["created"] or 0, reverse=True)


# --------------------------------------------------------------------------
# keywords
# --------------------------------------------------------------------------


def load_keywords() -> list[tuple[str, re.Pattern]]:
    """Each non-comment line is a keyword. Prefix with 're:' for a raw regex."""
    if not KEYWORDS_PATH.exists():
        return []
    keywords = []
    for raw_line in KEYWORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            pattern = line[3:].strip()
            label = pattern
        else:
            pattern = re.escape(fold(line))
            label = line
        try:
            keywords.append((label, re.compile(pattern, re.IGNORECASE)))
        except re.error as exc:
            print(f"  ! skipping bad pattern {line!r}: {exc}", file=sys.stderr)
    return keywords


def match_keywords(post: dict, keywords) -> list[str]:
    folded = fold(post["text"])
    return [label for label, pattern in keywords if pattern.search(folded)]


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            print("  ! state file corrupt, starting fresh", file=sys.stderr)
    return {"seen": {}, "last_success": None, "consecutive_failures": 0}


def save_state(state: dict) -> None:
    seen = state.get("seen", {})
    if len(seen) > SEEN_LIMIT:
        newest = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:SEEN_LIMIT]
        state["seen"] = dict(newest)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------
# notification
# --------------------------------------------------------------------------


def ntfy_send(topic: str, title: str, body: str, click: str | None,
              priority: str, tags: str, dry_run: bool) -> None:
    if dry_run or not topic:
        print(f"    [dry-run] {title}\n      {body[:200]}")
        return
    headers = {
        "Title": ascii_header(title),
        "Priority": priority,
        "Tags": tags,
        "Markdown": "yes",
    }
    if click:
        headers["Click"] = click
    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def humanize_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "hace menos de 1 min"
    if minutes < 60:
        return f"hace {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h {minutes % 60} min"
    return f"hace {hours // 24} d"


def notify_post(topic: str, post: dict, matched: list[str], dry_run: bool) -> None:
    already_gone = bool(SOLD_OUT_RE.search(post["text"]))
    snippet = " ".join(post["text"].split())[:400]
    title = f"{'[YA CERRADO] ' if already_gone else ''}Game drop: {', '.join(matched)}"

    # End-to-end lag, so you can see whether alerts are actually arriving fast.
    # This is cache lag plus cron lag combined - the number that really matters.
    age = ""
    if post.get("created"):
        delta = datetime.now(timezone.utc).timestamp() - post["created"]
        age = f" · publicado {humanize_age(delta)}"

    body = f"**{', '.join(matched)}**{age}\n\n{snippet}\n\n{post['url']}"
    if already_gone:
        body = "_Este post ya aparece marcado como agotado/cerrado._\n\n" + body
    ntfy_send(
        topic,
        title,
        body,
        post["url"],
        priority="default" if already_gone else "high",
        tags="warning" if already_gone else "video_game",
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def within_active_window(now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    start = ACTIVE_START[0] * 60 + ACTIVE_START[1]
    end = ACTIVE_END[0] * 60 + ACTIVE_END[1]
    return start <= minutes <= end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime", action="store_true",
                        help="record current posts without notifying")
    parser.add_argument("--dry-run", action="store_true",
                        help="print notifications instead of sending them")
    parser.add_argument("--force", action="store_true",
                        help="run even outside the active hours window")
    parser.add_argument("--test", action="store_true",
                        help="send one test notification without touching state")
    args = parser.parse_args()

    now_bogota = datetime.now(BOGOTA)
    if not (args.force or args.prime or args.test) and not within_active_window(now_bogota):
        print(f"Outside active window ({now_bogota:%H:%M} Bogota) - skipping.")
        return 0

    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic and not (args.dry_run or args.prime):
        print("NTFY_TOPIC is not set.", file=sys.stderr)
        return 2

    # Set the ALERT_ALL repo variable to 1 to be notified about every new post
    # regardless of keywords. Useful for confirming the pipeline works.
    alert_all = os.environ.get("ALERT_ALL", "").strip().lower() in {"1", "true", "yes"}

    keywords = load_keywords()

    if args.test:
        # Re-send the newest real post through the normal notification path, so
        # this exercises the actual title, priority, link and formatting rather
        # than a dummy string. State is never touched.
        print("Sending a test notification...")
        try:
            posts = parse_posts(fetch_page())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"  ! fetch failed: {exc}", file=sys.stderr)
            posts = []
        if posts:
            post = posts[0]
            matched = match_keywords(post, keywords) or ["prueba"]
            notify_post(topic, post, matched, args.dry_run)
            print(f"  sent, using real post {post['id']} ({', '.join(matched)})")
        else:
            ntfy_send(
                topic, "Game drop: prueba",
                "**prueba**\n\nNotificacion de prueba. El watcher puede "
                f"alcanzar tu telefono.\n\n{PAGE_URL}",
                PAGE_URL, "high", "video_game", args.dry_run,
            )
            print("  page fetch failed, sent a synthetic test instead")
        return 0

    state = load_state()
    mode = "ALERT_ALL (every new post)" if alert_all else f"{len(keywords)} keywords"
    print(f"{now_bogota:%Y-%m-%d %H:%M} Bogota | {mode} | "
          f"{len(state['seen'])} posts already seen")

    try:
        html = fetch_page()
        posts = parse_posts(html)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        posts = []
        print(f"  ! fetch failed: {exc}", file=sys.stderr)

    if not posts:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        print(f"  ! no posts parsed (failure #{state['consecutive_failures']})",
              file=sys.stderr)
        if state["consecutive_failures"] == FAILURE_ALERT_THRESHOLD:
            ntfy_send(
                topic,
                "Game watcher is broken",
                f"{FAILURE_ALERT_THRESHOLD} consecutive polls returned no posts. "
                "Facebook may have changed the page or blocked the request.\n\n"
                "Check the GitHub Actions logs.",
                None, "high", "rotating_light", args.dry_run,
            )
        if not args.dry_run:
            save_state(state)
        return 1

    print(f"  fetched {len(posts)} posts, newest "
          f"{datetime.fromtimestamp(posts[0]['created'] or 0, timezone.utc):%Y-%m-%d %H:%M} UTC")

    if state.get("consecutive_failures"):
        print("  recovered from previous failures")
    state["consecutive_failures"] = 0
    state["last_success"] = now_bogota.isoformat(timespec="seconds")

    stamp = now_bogota.isoformat(timespec="seconds")
    new_posts = [p for p in posts if p["id"] not in state["seen"]]
    print(f"  {len(new_posts)} new post(s)")

    alerted = 0
    for post in reversed(new_posts):  # oldest first, so alerts arrive in order
        state["seen"][post["id"]] = stamp
        if args.prime:
            continue
        matched = match_keywords(post, keywords)
        if not matched:
            if not alert_all:
                continue
            matched = ["post nuevo"]
        preview = " ".join(post["text"].split())[:70]
        lag = ""
        if post.get("created"):
            lag = f" lag={int((now_bogota.timestamp() - post['created']) // 60)}min"
        print(f"    MATCH {matched}{lag} {post['id']} {preview!r}")
        try:
            notify_post(topic, post, matched, args.dry_run)
            alerted += 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"    ! ntfy send failed: {exc}", file=sys.stderr)
            del state["seen"][post["id"]]  # retry on the next run

    if args.prime:
        print(f"  primed {len(new_posts)} post(s), no alerts sent")
    else:
        print(f"  sent {alerted} alert(s)")

    if args.dry_run:
        # Never persist during a dry run: marking posts seen here would
        # silently suppress the real alert on the next run.
        print("  [dry-run] state not saved")
    else:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
