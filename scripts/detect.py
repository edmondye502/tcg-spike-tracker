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
import sys
from pathlib import Path

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


def load_history() -> dict:
    """Map (product_id, sub_type) -> most recent alert date."""
    if not HISTORY.exists():
        return {}
    seen: dict = {}
    with HISTORY.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (int(row["product_id"]), row["sub_type_name"])
            if key not in seen or row["date"] > seen[key]:
                seen[key] = row["date"]
    return seen


def append_history(date: str, alerts: list[dict]) -> None:
    exists = HISTORY.exists()
    with HISTORY.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(HISTORY_COLUMNS)
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
    history = load_history()
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
        if last_seen and ordinals_for([last_seen])[0] > cutoff:
            suppressed += 1
            continue

        card = catalog.get(product_id)
        if not card:
            continue

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
                "game": CATEGORIES.get(meta[key], str(meta[key])),
                "name": card["name"],
                "set_name": card["set_name"],
                "set_abbreviation": card["abbreviation"],
                "number": card["number"],
                "rarity": card["rarity"],
                "image_url": card["image_url"],
                "url": card["url"],
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

    top = rank(candidates)
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
            flags.append(f"copies from ${alert['low']:.2f}")
        elif alert["low_state"] == "above":
            flags.append(f"nothing under ${alert['low']:.2f}")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(
            f"  {alert['game']:<9} ${alert['baseline']:>7.2f} -> ${alert['price']:>7.2f} "
            f"(+{alert['pct']:>5.0%})  {alert['name']} "
            f"({alert['set_abbreviation']} {alert['number']}, {alert['sub_type_name']}){suffix}"
        )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
