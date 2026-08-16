"""Build a SQLite database from the day files and catalog.

The database is a derived artifact, not a source of truth — it gets rebuilt
from data/prices/*.csv.gz whenever you need it and is gitignored. The day
files are what's version controlled, because git handles new small files well
and rewritten binaries badly.

    python scripts/db.py                 # rebuild from every day file
    python scripts/db.py --days 45       # only the most recent 45
"""

import argparse
import csv
import gzip
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CATALOG, DATA, PRICES  # noqa: E402

DB_PATH = DATA / "spike.db"

SCHEMA = """
DROP TABLE IF EXISTS prices;
CREATE TABLE prices (
    date          TEXT NOT NULL,
    category_id   INTEGER NOT NULL,
    group_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    sub_type_name TEXT NOT NULL,
    low           REAL,
    mid           REAL,
    high          REAL,
    market        REAL,
    direct_low    REAL
);

DROP TABLE IF EXISTS products;
CREATE TABLE products (
    category_id INTEGER NOT NULL,
    group_id    INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    name        TEXT,
    number      TEXT,
    rarity      TEXT,
    image_url   TEXT,
    url         TEXT,
    PRIMARY KEY (product_id)
);

DROP TABLE IF EXISTS groups;
CREATE TABLE groups (
    category_id     INTEGER NOT NULL,
    group_id        INTEGER NOT NULL,
    name            TEXT,
    abbreviation    TEXT,
    published_on    TEXT,
    is_supplemental TEXT,
    PRIMARY KEY (group_id)
);
"""

INDEXES = """
CREATE INDEX idx_prices_key  ON prices (product_id, sub_type_name, date);
CREATE INDEX idx_prices_date ON prices (date);
"""


def to_float(value: str):
    return float(value) if value not in ("", "None", None) else None


def load_prices(connection: sqlite3.Connection, files: list[Path]) -> int:
    total = 0
    for path in files:
        day = path.name.removesuffix(".csv.gz")
        with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader, None)  # header
            rows = (
                (
                    day,
                    int(row[0]),
                    int(row[1]),
                    int(row[2]),
                    row[3],
                    to_float(row[4]),
                    to_float(row[5]),
                    to_float(row[6]),
                    to_float(row[7]),
                    to_float(row[8]),
                )
                for row in reader
                if row and row[2]
            )
            cursor = connection.executemany(
                "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)", rows
            )
            total += cursor.rowcount if cursor.rowcount > 0 else 0
    return total


def load_csv(connection: sqlite3.Connection, path: Path, table: str, columns: int) -> int:
    if not path.exists():
        print(f"  ! missing {path.name} — run scripts/catalog.py")
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        placeholders = ",".join("?" * columns)
        rows = [row[:columns] for row in reader if row]
        connection.executemany(
            f"INSERT OR REPLACE INTO {table} VALUES ({placeholders})", rows
        )
    return len(rows)


def build(days: int | None = None) -> Path:
    files = sorted(PRICES.glob("*.csv.gz"))
    if not files:
        raise SystemExit("no day files — run scripts/backfill.py first")
    if days:
        files = files[-days:]

    started = time.time()
    DB_PATH.unlink(missing_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    # Bulk load, so trade durability for speed — the DB is rebuildable.
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")

    print(f"loading {len(files)} day files ({files[0].stem} .. {files[-1].stem})")
    price_rows = load_prices(connection, files)
    group_rows = load_csv(connection, CATALOG / "groups.csv", "groups", 6)
    product_rows = load_csv(connection, CATALOG / "products.csv", "products", 8)

    connection.executescript(INDEXES)
    connection.commit()
    connection.close()

    size = DB_PATH.stat().st_size / 1e6
    print(
        f"  {price_rows:,} price rows, {product_rows:,} products, {group_rows:,} sets\n"
        f"  {size:.0f} MB in {time.time() - started:.0f}s -> {DB_PATH}"
    )
    return DB_PATH


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit("no database — run scripts/db.py first")
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="only load the most recent N day files")
    args = parser.parse_args()
    build(days=args.days)


if __name__ == "__main__":
    main()
