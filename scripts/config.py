"""Shared configuration for the spike tracker."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRICES = DATA / "prices"
CATALOG = DATA / "catalog"

# TCGplayer categoryId -> short name used in filenames and alerts.
CATEGORIES = {
    3: "pokemon",
    68: "onepiece",
}

ARCHIVE_URL = "https://tcgcsv.com/archive/tcgplayer/prices-{date}.ppmd.7z"
GROUPS_URL = "https://tcgcsv.com/tcgplayer/{category}/groups"
PRODUCTS_URL = "https://tcgcsv.com/tcgplayer/{category}/{group}/products"
PRICES_URL = "https://tcgcsv.com/tcgplayer/{category}/{group}/prices"

# Earliest day tcgcsv has archived.
ARCHIVE_START = "2024-02-08"

# Columns in each daily price file. Date lives in the filename.
PRICE_COLUMNS = [
    "category_id",
    "group_id",
    "product_id",
    "sub_type_name",
    "low",
    "mid",
    "high",
    "market",
    "direct_low",
]

# tcgcsv blocks generic user agents and asks for "Name/X.Y.Z" — see
# https://tcgcsv.com/docs#usage-guidelines
USER_AGENT = "WheeinSpikeTracker/0.1.0"

# The guidelines ask for ~100ms between requests in a sync loop.
REQUEST_DELAY = 0.15


# --- Alert shaping -------------------------------------------------------

DAILY_LIMIT = 10

# Cheap cards are the whole point (a $1 card is still in someone's binder at
# $1; a $90 card was repriced weeks ago) but they lose every dollar-ranked
# contest. Hold slots back for them. Unused reserved slots fall through to
# the general pool, so a day with no cheap spikes still fills all ten.
RESERVED_CHEAP_SLOTS = 4
CHEAP_PRICE_CEILING = 10.00

# Don't re-alert the same card/subtype for this many days.
SUPPRESS_DAYS = 7

# Rough all-in selling cost: ~10.25% commission plus payment processing on
# TCGplayer, a little more on eBay. Used only to estimate net per copy, so
# the alert says what you'd actually clear rather than the gross move.
MARKETPLACE_FEE_RATE = 0.1275
SHIPPING_COST = 1.00

# Only consider sets published within this many days — vintage rarely spikes
# and is hard to find loose in the wild anyway.
MAX_SET_AGE_DAYS = 1460


def day_file(date: str) -> Path:
    """Path to the compressed price snapshot for a given YYYY-MM-DD."""
    return PRICES / f"{date}.csv.gz"
