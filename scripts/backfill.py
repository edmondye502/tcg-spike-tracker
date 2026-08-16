"""Download tcgcsv daily price archives and write one compressed file per day.

Each archive holds every TCGplayer category; we extract only the ones in
config.CATEGORIES, which is ~6x faster than a full extract and ~10x smaller
on disk. Day files are append-only: once written they are never rewritten,
which is what keeps the git repo small.

    python scripts/backfill.py --days 120
    python scripts/backfill.py --start 2026-01-01 --end 2026-03-01
"""

import argparse
import csv
import gzip
import json
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import py7zr
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    ARCHIVE_START,
    ARCHIVE_URL,
    CATEGORIES,
    PRICE_COLUMNS,
    PRICES,
    USER_AGENT,
    day_file,
)


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def download(url: str, dest: Path, session: requests.Session) -> bool:
    """Fetch url to dest. Returns False on 404 (archive missing for that day)."""
    with session.get(url, stream=True, timeout=120) as response:
        if response.status_code == 404:
            return False
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)
    return True


def extract_categories(archive: Path, workdir: Path) -> Path:
    """Extract only our categories. Returns the directory they landed in."""
    with py7zr.SevenZipFile(archive, "r") as sevenzip:
        wanted = {str(category) for category in CATEGORIES}
        targets = [
            name
            for name in sevenzip.getnames()
            if len(parts := name.split("/")) > 1 and parts[1] in wanted
        ]
        if not targets:
            raise RuntimeError(f"no matching categories inside {archive.name}")
        sevenzip.extract(path=workdir, targets=targets)
    return workdir


def rows_from_extract(extracted: Path):
    """Walk the extracted {date}/{category}/{group}/prices tree, yielding rows."""
    for prices_file in extracted.rglob("prices"):
        group_id = prices_file.parent.name
        category_id = prices_file.parent.parent.name
        try:
            payload = json.loads(prices_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"    skipping {category_id}/{group_id}: {error}")
            continue
        if not payload.get("success"):
            continue
        for entry in payload.get("results", []):
            yield [
                category_id,
                group_id,
                entry.get("productId"),
                entry.get("subTypeName"),
                entry.get("lowPrice"),
                entry.get("midPrice"),
                entry.get("highPrice"),
                entry.get("marketPrice"),
                entry.get("directLowPrice"),
            ]


def write_day(day: str, extracted: Path) -> int:
    """Write the day's rows to a gzipped csv. Returns the row count."""
    target = day_file(day)
    written = 0
    # Write to a temp file first so an interrupted run never leaves a partial
    # day file behind — the whole design assumes day files are complete.
    staging = target.with_suffix(".partial")
    with gzip.open(staging, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(PRICE_COLUMNS)
        for row in rows_from_extract(extracted):
            writer.writerow(row)
            written += 1
    if written:
        staging.replace(target)
    else:
        staging.unlink(missing_ok=True)
    return written


def backfill(start: date, end: date, force: bool = False) -> None:
    PRICES.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    total_rows = 0
    fetched = 0
    for day in daterange(start, end):
        stamp = day.isoformat()
        if day_file(stamp).exists() and not force:
            continue

        workdir = Path(tempfile.mkdtemp(prefix=f"tcg-{stamp}-"))
        try:
            archive = workdir / "prices.7z"
            url = ARCHIVE_URL.format(date=stamp)
            if not download(url, archive, session):
                print(f"  {stamp}  no archive (404)")
                continue

            extract_categories(archive, workdir / "ext")
            rows = write_day(stamp, workdir / "ext")
            total_rows += rows
            fetched += 1
            print(f"  {stamp}  {rows:>7,} rows  ({archive.stat().st_size / 1e6:.1f} MB)")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        # tcgcsv is one person's Patreon-funded mirror; don't hammer it.
        time.sleep(0.5)

    print(f"\ndone: {fetched} day files, {total_rows:,} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="number of days back from --end")
    parser.add_argument("--start", type=parse_day, help="first day, YYYY-MM-DD")
    parser.add_argument("--end", type=parse_day, help="last day, YYYY-MM-DD")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download days that already have a file",
    )
    args = parser.parse_args()

    # The archive is published around 20:00 UTC, so "today" may not exist yet.
    end = args.end or datetime.now(timezone.utc).date()
    if args.start:
        start = args.start
    elif args.days:
        start = end - timedelta(days=args.days - 1)
    else:
        parser.error("pass --days or --start")

    floor = parse_day(ARCHIVE_START)
    if start < floor:
        print(f"clamping start to archive floor {ARCHIVE_START}")
        start = floor

    print(f"backfilling {start} .. {end}\n")
    backfill(start, end, force=args.force)


if __name__ == "__main__":
    main()
