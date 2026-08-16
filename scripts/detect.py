"""Find today's spikes, rank them, and write the day's alert file.

Detection is deliberately loose — the replay showed that tightening the bands
costs most of the volume while the biggest spikes mean-revert hardest. The
filtering happens at the ranking step instead, where a cap can weigh a cheap
binder find against a big-money move rather than throwing one away up front.

    python scripts/detect.py                  # newest day in the database
    python scripts/detect.py --date 2026-07-01
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis import (  # noqa: E402
    RULE_SETS,
    baseline_for,
    evaluate,
    has_clean_window,
    is_new_high,
    load_series,
    ordinals_for,
    rising_streak,
)
from config import (  # noqa: E402
    CATEGORIES,
    CHEAP_PRICE_CEILING,
    DAILY_LIMIT,
    DATA,
    MARKETPLACE_FEE_RATE,
    MAX_SET_AGE_DAYS,
    RESERVED_CHEAP_SLOTS,
    SHIPPING_COST,
    SUPPRESS_DAYS,
)
from db import connect

ALERTS = DATA / "alerts"
HISTORY = DATA / "alert_history.csv"
HISTORY_COLUMNS = ["date", "product_id", "sub_type_name", "price"]

SPARKLINE_DAYS = 30


def net_per_copy(price: float) -> float:
    """What you'd actually clear selling one copy at this price."""
    return round(price * (1 - MARKETPLACE_FEE_RATE) - SHIPPING_COST, 2)


GAME_TERMS = {"pokemon": "Pokemon", "onepiece": "One Piece"}

# Words TCGplayer puts in product names that sellers don't put in listing
# titles. Dropping them keeps the search specific without over-narrowing —
# eBay ANDs every keyword, so each extra word can cost you real results.
# "English" is here because English listings rarely say so; "Japanese" is not,
# because Japanese printings are almost always labelled.
DESCRIPTOR_NOISE = {
    "a", "and", "card", "cards", "collection", "edition", "english", "for",
    "games", "of", "pack", "packs", "series", "set", "sets", "the", "version",
}

# How many distinguishing words to carry over from a product name's
# parentheticals. Enough to separate reprints that share a collector number,
# few enough to still return listings.
MAX_DESCRIPTOR_WORDS = 3


def ebay_query(name: str, number: str, game: str = "") -> str:
    """Build a search string specific enough to land on the right printing.

    Collector numbers get reused across printings — One Piece especially, where
    a promo and its base-set original share a number. So the distinguishing
    words in a name's parentheticals ("1st Anniversary", "Alternate Art") are
    kept rather than discarded; without them a search for the anniversary Sanji
    returns every ordinary OP01-013 instead.
    """
    descriptors = re.findall(r"\(([^)]*)\)", name)
    base = re.sub(r"\s*\([^)]*\)", " ", name)
    # Trailing "- 223/197" duplicates the collector number we re-add below.
    base = re.sub(r"\s+-\s+[\w./-]+\s*$", "", base)
    base = re.sub(r"\s+", " ", base).strip()

    words: list[str] = []
    for descriptor in descriptors:
        # A parenthetical that is just the collector number adds nothing.
        if number and descriptor.strip().lower() == number.lower():
            continue
        # Split on whitespace and slashes only — hyphens are load-bearing
        # inside tokens like "e-League" and "OP01-013", and stripping them
        # from the edges handles "-BANDAI" and "Edition-" just as well.
        for raw in re.split(r"[\s/]+", descriptor):
            word = raw.strip(".,'\"!?-–—").strip()
            if len(word) < 2 or word.isdigit():
                continue
            if word.lower() in DESCRIPTOR_NOISE:
                continue
            if number and word.lower() == number.lower():
                continue
            if word.lower() not in {w.lower() for w in words}:
                words.append(word)

    parts = [GAME_TERMS.get(game, ""), base]
    if number and number.lower() not in base.lower():
        parts.append(number)

    # "Monkey.D.Luffy (One Piece Film Red)" would otherwise repeat the game
    # name back at itself; a duplicated keyword just wastes a slot.
    already = {w.lower() for p in parts for w in p.split()}
    parts.extend([w for w in words if w.lower() not in already][:MAX_DESCRIPTOR_WORDS])
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def ebay_links(name: str, number: str, game: str = "") -> tuple[str, str]:
    """(active listings cheapest first, recent sold comps)."""
    encoded = quote_plus(ebay_query(name, number, game))
    return (
        f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_sop=15",
        f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_Sold=1&LH_Complete=1&_sop=13",
    )


def load_catalog() -> dict:
    connection = connect()
    rows = connection.execute(
        """
        SELECT p.product_id, p.name, p.number, p.rarity, p.image_url, p.url,
               g.name AS set_name, g.abbreviation, g.published_on
        FROM products p
        JOIN groups g ON g.group_id = p.group_id
        """
    ).fetchall()
    connection.close()
    return {row["product_id"]: dict(row) for row in rows}


def load_history(before: str) -> dict:
    """Map (product_id, sub_type) -> most recent alert date strictly before `before`.

    Excluding the target day matters: re-running a day (an Actions retry, or a
    manual re-run) would otherwise read its own alerts back and suppress every
    pick it just made.
    """
    if not HISTORY.exists():
        return {}
    seen: dict = {}
    with HISTORY.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row["date"] >= before:
                continue
            key = (int(row["product_id"]), row["sub_type_name"])
            if key not in seen or row["date"] > seen[key]:
                seen[key] = row["date"]
    return seen


def append_history(date: str, alerts: list[dict]) -> None:
    """Record the day's picks, replacing any existing rows for that date.

    Rewriting rather than appending keeps a re-run from stacking duplicate
    rows for the same day.
    """
    kept: list[list] = []
    if HISTORY.exists():
        with HISTORY.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader, None)  # header
            kept = [row for row in reader if row and row[0] != date]

    with HISTORY.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HISTORY_COLUMNS)
        writer.writerows(kept)
        for alert in alerts:
            writer.writerow(
                [date, alert["product_id"], alert["sub_type_name"], alert["price"]]
            )


def rank(candidates: list[dict]) -> list[dict]:
    """Top DAILY_LIMIT, holding RESERVED_CHEAP_SLOTS back for cheap cards.

    Reserved slots that go unused fall through to the general pool, so a day
    with no cheap spikes still fills the whole list.
    """
    by_gain = sorted(candidates, key=lambda c: -c["gain"])
    cheap = [c for c in by_gain if c["baseline"] < CHEAP_PRICE_CEILING]

    chosen: list[dict] = []
    taken: set = set()
    for candidate in cheap[:RESERVED_CHEAP_SLOTS]:
        chosen.append(candidate)
        taken.add(id(candidate))

    for candidate in by_gain:
        if len(chosen) >= DAILY_LIMIT:
            break
        if id(candidate) not in taken:
            chosen.append(candidate)
            taken.add(id(candidate))

    return sorted(chosen, key=lambda c: -c["gain"])


def detect(target_date: str | None, rule_name: str = "loose") -> dict:
    bands = RULE_SETS[rule_name]
    dates, market, low, meta = load_series(MAX_SET_AGE_DAYS)
    if not dates:
        raise SystemExit("no price data — run scripts/backfill.py")

    day = target_date or dates[-1]
    if day not in dates:
        raise SystemExit(f"no snapshot for {day} (newest is {dates[-1]})")
    index = dates.index(day)

    ordinals = ordinals_for(dates)
    if not has_clean_window(ordinals, index):
        raise SystemExit(
            f"{day} has no usable baseline — the previous {7} snapshots span a gap. "
            "Backfill the missing days first."
        )

    catalog = load_catalog()
    history = load_history(before=day)
    cutoff = ordinals[index] - SUPPRESS_DAYS

    candidates = []
    suppressed = 0
    for key, series in market.items():
        today = series[index]
        if today != today:
            continue
        baseline = baseline_for(series, index)
        if baseline is None or baseline < 1.0:
            continue
        if not evaluate(bands, baseline, today):
            continue

        product_id, sub_type = key
        last_seen = history.get(key)
        # A card mid-run trips the threshold for days on end. Keep it out of
        # the ranked picks, but still list it: if you only check the page
        # every few days, a card that spiked and then got suppressed would
        # otherwise disappear before you ever laid eyes on it.
        is_repeat = bool(last_seen and ordinals_for([last_seen])[0] > cutoff)
        if is_repeat:
            suppressed += 1

        card = catalog.get(product_id)
        if not card:
            continue

        game = CATEGORIES.get(meta[key], "")
        ebay_url, ebay_sold_url = ebay_links(card["name"], card["number"], game)

        low_today = low[key][index]
        low_today = round(low_today, 2) if low_today == low_today else None
        # lowPrice is the cheapest *listing*; market is derived from recent
        # *sales*. For thin cards nobody lists cheap, so low sits above market
        # 19% of the time overall — and 54% of the time above $150. Those two
        # cases mean opposite things, so classify rather than show a bare
        # number: "cheap" is the actionable one (copies still underpriced),
        # "above" means you can't even buy at market right now.
        low_ratio = round(low_today / today, 3) if low_today and today else None
        if low_ratio is None:
            low_state = "unknown"
        elif low_ratio <= 0.75:
            low_state = "cheap"
        elif low_ratio > 1.0:
            low_state = "above"
        else:
            low_state = "near"

        history_window = [
            round(value, 2) if value == value else None
            for value in series[max(0, index - SPARKLINE_DAYS + 1) : index + 1]
        ]

        candidates.append(
            {
                "product_id": product_id,
                "sub_type_name": sub_type,
                "game": game or str(meta[key]),
                "name": card["name"],
                "set_name": card["set_name"],
                "set_abbreviation": card["abbreviation"],
                "number": card["number"],
                "rarity": card["rarity"],
                "image_url": card["image_url"],
                "url": card["url"],
                "ebay_url": ebay_url,
                "ebay_sold_url": ebay_sold_url,
                "repeat": is_repeat,
                "last_alerted": last_seen if is_repeat else None,
                "baseline": round(baseline, 2),
                "price": round(today, 2),
                "gain": round(today - baseline, 2),
                "pct": round((today - baseline) / baseline, 4),
                "low": low_today,
                "low_ratio": low_ratio,
                "low_state": low_state,
                "net_per_copy": net_per_copy(today),
                "streak": rising_streak(series, index),
                "new_high": is_new_high(series, index),
                "history": history_window,
            }
        )

    top = rank([c for c in candidates if not c["repeat"]])
    return {
        "date": day,
        "rules": rule_name,
        "candidate_count": len(candidates),
        "suppressed_count": suppressed,
        "alerts": top,
        "candidates": sorted(candidates, key=lambda c: -c["gain"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to newest snapshot")
    parser.add_argument("--rules", default="loose", choices=sorted(RULE_SETS))
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="don't record these alerts (use when re-running a past day)",
    )
    args = parser.parse_args()

    result = detect(args.date, args.rules)
    ALERTS.mkdir(parents=True, exist_ok=True)
    out_path = ALERTS / f"{result['date']}.json"
    out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")

    if not args.no_history and result["alerts"]:
        append_history(result["date"], result["alerts"])

    print(f"{result['date']}: {result['candidate_count']} candidates, "
          f"{result['suppressed_count']} suppressed, {len(result['alerts'])} alerted")
    for alert in result["alerts"]:
        flags = []
        if alert["new_high"]:
            flags.append("new high")
        if alert["streak"] >= 3:
            flags.append(f"{alert['streak']}d streak")
        if alert["low_state"] == "cheap":
            flags.append(f"listings from ${alert['low']:.2f} any cond.")
        elif alert["low_state"] == "above":
            flags.append(f"nothing under ${alert['low']:.2f} any cond.")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(
            f"  {alert['game']:<9} ${alert['baseline']:>7.2f} -> ${alert['price']:>7.2f} "
            f"(+{alert['pct']:>5.0%})  {alert['name']} "
            f"({alert['set_abbreviation']} {alert['number']}, {alert['sub_type_name']}){suffix}"
        )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
