"""Replay candidate spike rules over historical data.

Answers the two questions you can't answer by guessing:

  volume  — how many alerts a day would this rule set have produced?
  hold    — of the cards it flagged, how many were still up a week later?

Because the data is historical we know what happened next, so a rule can be
scored before a single dollar is spent.

    python scripts/replay.py
    python scripts/replay.py --rules strict --max-set-age 1095

Note: ~70% of consecutive-day market prices are byte-identical, because
TCGplayer only moves market price when a sale happens. A naive "still above
the alert price" hold rate mostly measures cards that never repriced, so
stale ones are reported separately below.
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis import (  # noqa: E402
    BASELINE_WINDOW,
    RULE_SETS,
    baseline_for,
    evaluate,
    has_clean_window,
    load_series,
    ordinals_for,
)
from config import CATEGORIES  # noqa: E402

HOLD_HORIZONS = (7, 30)
MAX_HORIZON_SLACK_DAYS = 4


def forward_price(series, ordinals: list[int], index: int, horizon: int):
    """Price roughly `horizon` calendar days after `index`, or None.

    Index arithmetic would be wrong here: with a gap in the data, seven
    snapshots ahead can be months ahead. Match on the calendar instead and
    accept the nearest snapshot within a few days.
    """
    target = ordinals[index] + horizon
    best = None
    best_distance = MAX_HORIZON_SLACK_DAYS + 1
    for ahead in range(index + 1, len(ordinals)):
        if ordinals[ahead] > target + MAX_HORIZON_SLACK_DAYS:
            break
        distance = abs(ordinals[ahead] - target)
        if distance < best_distance and series[ahead] == series[ahead]:
            best, best_distance = series[ahead], distance
    return best


def replay(rule_name: str, max_set_age_days: int | None):
    bands = RULE_SETS[rule_name]
    dates, market, low, meta = load_series(max_set_age_days)
    span = len(dates)
    ordinals = ordinals_for(dates)

    scorable = [i for i in range(BASELINE_WINDOW, span) if has_clean_window(ordinals, i)]
    skipped = (span - BASELINE_WINDOW) - len(scorable)

    print(f"rule set '{rule_name}' over {span} days, {len(market):,} card/subtype series")
    print(f"  {dates[0]} .. {dates[-1]}")
    if skipped:
        print(f"  skipping {skipped} day(s) whose baseline window spans a gap")
    print()

    per_day = defaultdict(int)
    per_game = defaultdict(int)
    alerts = []

    for key, series in market.items():
        for index in scorable:
            today = series[index]
            if today != today:  # NaN
                continue
            baseline = baseline_for(series, index)
            if baseline is None or baseline < 1.0:
                continue
            if not evaluate(bands, baseline, today):
                continue

            per_day[dates[index]] += 1
            per_game[meta[key]] += 1
            alerts.append(
                {
                    "date": dates[index],
                    "baseline": baseline,
                    "price": today,
                    "forward": {
                        horizon: forward_price(series, ordinals, index, horizon)
                        for horizon in HOLD_HORIZONS
                    },
                }
            )

    return [dates[i] for i in scorable], alerts, per_day, per_game


def summarize(evaluable, alerts, per_day, per_game) -> None:
    counts = [per_day.get(day, 0) for day in evaluable]

    print(f"total alerts: {len(alerts):,} over {len(evaluable)} days")
    if not alerts:
        print("  (nothing fired — loosen the bands)")
        return

    counts_sorted = sorted(counts)
    print("alerts per day")
    print(f"  mean   {statistics.mean(counts):6.1f}")
    print(f"  median {statistics.median(counts):6.1f}")
    print(f"  p90    {counts_sorted[int(len(counts_sorted) * 0.9)]:6.0f}")
    print(f"  max    {max(counts):6.0f}")
    print(f"  quiet days (0 alerts): {sum(1 for c in counts if c == 0)}")

    print("\nby game")
    for category_id, count in sorted(per_game.items(), key=lambda kv: -kv[1]):
        label = CATEGORIES.get(category_id, str(category_id))
        print(f"  {label:<10} {count:>7,}  ({count / len(alerts):.0%})")

    print("\nhold rate (did the spike stick?)")
    for horizon in HOLD_HORIZONS:
        scored = [a for a in alerts if a["forward"][horizon] is not None]
        if not scored:
            print(f"  +{horizon}d: no forward data yet")
            continue
        stale = [a for a in scored if a["forward"][horizon] == a["price"]]
        moved = [a for a in scored if a["forward"][horizon] != a["price"]]
        print(
            f"  +{horizon:>2}d  n={len(scored):>5,}   "
            f"never repriced: {len(stale) / len(scored):>4.0%}"
        )
        if not moved:
            continue
        held = sum(1 for a in moved if a["forward"][horizon] >= a["price"])
        # "Reverted" = gave back at least 80% of the move.
        reverted = sum(
            1
            for a in moved
            if a["forward"][horizon] <= a["baseline"] + 0.2 * (a["price"] - a["baseline"])
        )
        above = sum(1 for a in moved if a["forward"][horizon] >= a["baseline"] * 1.25)
        returns = [(a["forward"][horizon] - a["price"]) / a["price"] for a in moved]
        print(
            f"        of the {len(moved):,} that did reprice: "
            f"kept climbing {held / len(moved):>4.0%}   "
            f"reverted {reverted / len(moved):>4.0%}   "
            f"still >25% over baseline {above / len(moved):>4.0%}   "
            f"median move {statistics.median(returns):>+6.1%}"
        )

    print("\nprice band mix (baseline price at alert time)")
    for low_bound, high_bound in [(1, 5), (5, 25), (25, 150), (150, 10**9)]:
        subset = [a for a in alerts if low_bound <= a["baseline"] < high_bound]
        if not subset:
            continue
        gains = [a["price"] - a["baseline"] for a in subset]
        label = f"${low_bound}-${high_bound}" if high_bound < 10**9 else f"${low_bound}+"
        print(
            f"  {label:<10} {len(subset):>7,} ({len(subset) / len(alerts):>4.0%})  "
            f"median gain ${statistics.median(gains):.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", default="loose", choices=sorted(RULE_SETS))
    parser.add_argument(
        "--max-set-age",
        type=int,
        default=1460,
        help="only sets published within N days (default 1460 = 4 years); 0 disables",
    )
    args = parser.parse_args()

    evaluable, alerts, per_day, per_game = replay(args.rules, args.max_set_age or None)
    summarize(evaluable, alerts, per_day, per_game)


if __name__ == "__main__":
    main()
