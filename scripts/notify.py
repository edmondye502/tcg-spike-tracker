"""Post the day's alerts to a Discord webhook.

One message per day, not one per card: a single message is a single
notification, keeps the channel scrollable, and sidesteps the webhook rate
limit entirely. On a day with nothing worth reporting it posts nothing at
all — silence is informative and keeps the ping meaningful.

The webhook URL comes from the DISCORD_WEBHOOK_URL environment variable
(a .env file next to this repo also works). Anyone holding that URL can post
to your channel, so keep it in GitHub secrets rather than in the repo.

    python scripts/notify.py --dry-run      # print the payload, send nothing
    python scripts/notify.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import ROOT, USER_AGENT  # noqa: E402
from detect import ALERTS  # noqa: E402

# Pokemon yellow, One Piece red — a mixed list needs to be scannable without
# reading set names.
GAME_COLORS = {
    "pokemon": 0xFFCB05,
    "onepiece": 0xD42A2A,
}
DEFAULT_COLOR = 0x5865F2

WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
EMBED_LIMIT = 10


def load_env() -> None:
    """Read a local .env if present. Nothing fancy — KEY=value lines."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def flags_for(alert: dict) -> list[str]:
    flags = []
    if alert["new_high"]:
        flags.append("🔺 new high")
    if alert["streak"] >= 3:
        flags.append(f"📈 {alert['streak']}d climb")
    # The cheapest listing not following the market up is the most actionable
    # signal available: copies may still be sitting there underpriced. The
    # inverse — cheapest listing above market — means you can't buy at market
    # at all right now, which is worth knowing before you plan a trip.
    if alert["low_state"] == "cheap":
        flags.append(f"💰 copies from ${alert['low']:,.2f}")
    elif alert["low_state"] == "above":
        flags.append(f"⚠️ nothing listed under ${alert['low']:,.2f}")
    return flags


def build_embed(alert: dict) -> dict:
    subtitle = " · ".join(
        part
        for part in (
            alert["set_abbreviation"] or alert["set_name"],
            alert["number"],
            alert["rarity"],
            alert["sub_type_name"],
        )
        if part
    )

    lines = [
        f"**${alert['baseline']:,.2f} → ${alert['price']:,.2f}**  "
        f"(+{alert['pct']:.0%}, +${alert['gain']:,.2f})",
        f"~${alert['net_per_copy']:,.2f} net per copy after fees",
    ]
    flags = flags_for(alert)
    if flags:
        lines.append(" · ".join(flags))

    return {
        "title": alert["name"][:250],
        "url": alert["url"],
        "description": "\n".join(lines),
        "color": GAME_COLORS.get(alert["game"], DEFAULT_COLOR),
        "footer": {"text": subtitle[:2000]},
        "thumbnail": {"url": alert["image_url"]} if alert["image_url"] else None,
    }


def build_payload(report: dict, page_url: str | None) -> dict | None:
    alerts = report.get("alerts") or []
    if not alerts:
        return None

    count = len(alerts)
    extra = report["candidate_count"] - count
    headline = f"**{count} spike{'s' if count != 1 else ''}** · {report['date']}"
    if extra > 0:
        headline += f"  _(+{extra} more below the cut)_"
    if page_url:
        headline += f"\n{page_url}"

    embeds = []
    for alert in alerts[:EMBED_LIMIT]:
        embed = build_embed(alert)
        embeds.append({k: v for k, v in embed.items() if v is not None})

    return {"content": headline, "embeds": embeds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to newest alert file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload instead of posting it",
    )
    parser.add_argument("--page-url", help="link to the full list, included in the message")
    args = parser.parse_args()

    load_env()

    files = sorted(ALERTS.glob("*.json"))
    if not files:
        raise SystemExit("no alert files — run scripts/detect.py first")
    path = ALERTS / f"{args.date}.json" if args.date else files[-1]
    if not path.exists():
        raise SystemExit(f"no alert file for {args.date}")

    report = json.loads(path.read_text(encoding="utf-8"))
    payload = build_payload(report, args.page_url or os.environ.get("PAGE_URL"))

    if payload is None:
        print(f"{report['date']}: nothing qualified — staying quiet")
        return

    if args.dry_run:
        print(json.dumps(payload, indent=1))
        total = len(json.dumps(payload))
        print(f"\n[dry run] {len(payload['embeds'])} embeds, {total:,} chars "
              f"(Discord allows 10 embeds / ~6000 chars of embed text)")
        return

    webhook = os.environ.get(WEBHOOK_ENV)
    if not webhook:
        raise SystemExit(
            f"{WEBHOOK_ENV} is not set. Put it in .env locally or in GitHub secrets."
        )

    response = requests.post(
        webhook,
        json=payload,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    print(f"{report['date']}: posted {len(payload['embeds'])} embeds")


if __name__ == "__main__":
    main()
