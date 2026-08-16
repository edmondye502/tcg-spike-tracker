"""Shared spike-detection primitives used by both the replay and the daily run.

Keeping these in one place matters more than usual here: if the replay scores
a rule differently from the way the daily job applies it, the calibration is
meaningless.
"""

import statistics
from array import array
from datetime import datetime, timezone

from db import connect

NAN = float("nan")

BASELINE_WINDOW = 7

# A 7-snapshot baseline is only meaningful if those snapshots are recent.
# Without this guard a gap in the data (failed cron run, tcgcsv outage, an
# interrupted backfill) silently compares today against months-old prices and
# reports the entire catalog as spiking.
MAX_BASELINE_SPAN_DAYS = 10

# Minimum usable prices inside the baseline window.
MIN_BASELINE_POINTS = 4

# Each band is (min_price, max_price, min_pct_gain, min_dollar_gain), applied
# to the pre-spike baseline. Cheap cards need a big multiple to be worth a
# detour; expensive cards are already repriced by dealers so the bar is a
# larger absolute move.
#
# "loose" is the calibrated default: the replay showed that tightening these
# buys ~10 points of hold rate but costs ~85% of the volume, and the largest
# spikes mean-revert hardest (strict went negative at +30d while loose stayed
# positive). Detect loosely, then rank and cap.
RULE_SETS = {
    "loose": [
        (1.00, 5.00, 0.75, 1.50),
        (5.00, 25.00, 0.25, 2.50),
        (25.00, 150.00, 0.15, 8.00),
    ],
    "baseline": [
        (1.00, 5.00, 1.50, 3.00),
        (5.00, 25.00, 0.40, 5.00),
        (25.00, 150.00, 0.25, 15.00),
    ],
    "strict": [
        (1.00, 5.00, 2.50, 5.00),
        (5.00, 25.00, 0.60, 8.00),
        (25.00, 150.00, 0.35, 20.00),
    ],
}


def load_series(max_set_age_days: int | None):
    """Return (dates, market, low, meta) with one float array per card/subtype.

    Restricted to singles (sealed product has no catalog row here) and, by
    default, to sets published recently enough that you could plausibly find
    the card loose in the wild.
    """
    connection = connect()

    where = ["p.market IS NOT NULL"]
    params: list = []
    if max_set_age_days is not None:
        cutoff = datetime.now(timezone.utc).date().toordinal() - max_set_age_days
        # published_on is an ISO timestamp; string compare is safe and cheap.
        where.append("g.published_on >= ?")
        params.append(datetime.fromordinal(cutoff).date().isoformat())

    dates = [
        row[0]
        for row in connection.execute("SELECT DISTINCT date FROM prices ORDER BY date")
    ]
    date_index = {day: i for i, day in enumerate(dates)}
    span = len(dates)

    sql = f"""
        SELECT p.product_id, p.sub_type_name, p.date, p.market, p.low, p.category_id
        FROM prices p
        JOIN products pr ON pr.product_id = p.product_id
        JOIN groups   g  ON g.group_id    = p.group_id
        WHERE {' AND '.join(where)}
    """

    market: dict = {}
    low: dict = {}
    meta: dict = {}
    for product_id, sub_type, day, market_price, low_price, category_id in connection.execute(
        sql, params
    ):
        key = (product_id, sub_type)
        if key not in market:
            market[key] = array("f", [NAN]) * span
            low[key] = array("f", [NAN]) * span
            meta[key] = category_id
        index = date_index[day]
        market[key][index] = market_price
        low[key][index] = low_price if low_price is not None else NAN

    connection.close()
    return dates, market, low, meta


def ordinals_for(dates: list[str]) -> list[int]:
    return [datetime.strptime(day, "%Y-%m-%d").date().toordinal() for day in dates]


def has_clean_window(ordinals: list[int], index: int) -> bool:
    """False when the baseline window reaches back across a gap in the data."""
    if index < BASELINE_WINDOW:
        return False
    return ordinals[index] - ordinals[index - BASELINE_WINDOW] <= MAX_BASELINE_SPAN_DAYS


def baseline_for(series, index: int):
    """Median market price over the snapshots preceding `index`, or None."""
    window = [
        value for value in series[index - BASELINE_WINDOW : index] if value == value
    ]
    if len(window) < MIN_BASELINE_POINTS:
        return None
    return statistics.median(window)


def evaluate(bands, baseline: float, today: float) -> bool:
    """True when the move clears the band covering this baseline price."""
    gain = today - baseline
    if gain <= 0:
        return False
    for low_bound, high_bound, min_pct, min_dollar in bands:
        if low_bound <= baseline < high_bound:
            return (gain / baseline) >= min_pct and gain >= min_dollar
    return False


def rising_streak(series, index: int) -> int:
    """How many consecutive prior snapshots the price rose, ending at `index`.

    A card climbing steadily for days is a different (and steadier) signal
    than a one-day pop, so it's worth surfacing alongside the spike itself.
    """
    streak = 0
    position = index
    while position > 0:
        current, previous = series[position], series[position - 1]
        if current != current or previous != previous or current <= previous:
            break
        streak += 1
        position -= 1
    return streak


def is_new_high(series, index: int) -> bool:
    """True when today's price is the highest in the loaded history."""
    today = series[index]
    return all(
        value != value or value <= today for value in series[:index]
    )
