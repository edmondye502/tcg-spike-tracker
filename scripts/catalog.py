"""Fetch set (group) and card (product) metadata for the tracked categories.

Prices alone can't tell you what to look for in a binder — that comes from
here: card name, set, collector number, rarity, and the TCGplayer link.

Sealed product (booster boxes, ETBs, tins) shares the price feed with singles
and spikes just as often, so it gets dropped. The discriminator is extendedData:
singles carry a "Number" field, sealed product never does.

    python scripts/catalog.py
"""

import csv
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CATALOG,
    CATEGORIES,
    GROUPS_URL,
    PRODUCTS_URL,
    REQUEST_DELAY,
    USER_AGENT,
)

GROUP_COLUMNS = [
    "category_id",
    "group_id",
    "name",
    "abbreviation",
    "published_on",
    "is_supplemental",
]

PRODUCT_COLUMNS = [
    "category_id",
    "group_id",
    "product_id",
    "name",
    "number",
    "rarity",
    "image_url",
    "url",
]


def extended(product: dict, field: str) -> str:
    """Pull a named value out of a product's extendedData list."""
    for entry in product.get("extendedData") or []:
        if entry.get("name") == field:
            return (entry.get("value") or "").strip()
    return ""


def get(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"{url} returned success=false: {payload.get('errors')}")
    return payload


def fetch_groups(session: requests.Session, category_id: int) -> list[dict]:
    payload = get(session, GROUPS_URL.format(category=category_id))
    return payload.get("results", [])


def fetch_products(session: requests.Session, category_id: int, group_id: int) -> list[dict]:
    payload = get(session, PRODUCTS_URL.format(category=category_id, group=group_id))
    return payload.get("results", [])


def main() -> None:
    CATALOG.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    groups_path = CATALOG / "groups.csv"
    products_path = CATALOG / "products.csv"

    with groups_path.open("w", newline="", encoding="utf-8") as groups_file, \
         products_path.open("w", newline="", encoding="utf-8") as products_file:
        groups_writer = csv.writer(groups_file)
        groups_writer.writerow(GROUP_COLUMNS)
        products_writer = csv.writer(products_file)
        products_writer.writerow(PRODUCT_COLUMNS)

        for category_id, label in CATEGORIES.items():
            groups = fetch_groups(session, category_id)
            print(f"{label}: {len(groups)} sets")
            time.sleep(REQUEST_DELAY)

            singles_total = 0
            for index, group in enumerate(groups, start=1):
                group_id = group["groupId"]
                groups_writer.writerow([
                    category_id,
                    group_id,
                    group.get("name"),
                    group.get("abbreviation"),
                    group.get("publishedOn"),
                    group.get("isSupplemental"),
                ])

                try:
                    products = fetch_products(session, category_id, group_id)
                except (requests.RequestException, RuntimeError) as error:
                    print(f"  ! {group.get('name')}: {error}")
                    continue

                singles = 0
                for product in products:
                    number = extended(product, "Number")
                    if not number:
                        continue  # sealed product, not a card
                    products_writer.writerow([
                        category_id,
                        group_id,
                        product.get("productId"),
                        product.get("name"),
                        number,
                        extended(product, "Rarity"),
                        product.get("imageUrl"),
                        product.get("url"),
                    ])
                    singles += 1

                singles_total += singles
                if index % 25 == 0 or index == len(groups):
                    print(f"  {index}/{len(groups)} sets, {singles_total:,} singles so far")
                time.sleep(REQUEST_DELAY)

            print(f"{label}: {singles_total:,} singles\n")

    print(f"wrote {groups_path}")
    print(f"wrote {products_path}")


if __name__ == "__main__":
    main()
