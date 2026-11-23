"""Build the analysis warehouse: raw CSVs -> parquet -> duckdb with shared views.

The parquet files are committed to the repo, so a fresh clone can build the
warehouse without the multi-megabyte source CSVs. Pass --from-raw to rebuild
them (e.g. after re-running the PDF recovery step).

Usage:
    python code/01_build_warehouse.py [--from-raw]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PARQUET = ROOT / "data" / "parquet"
DB = ROOT / "data" / "warehouse.duckdb"

# Explicit types: activity_date stays the raw YYYYMMDD integer it is in the source,
# and is exposed as a real DATE by the shared `v_pd` view.
SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "player_profile": [
        ("device_id", "BIGINT"), ("original_device_id", "BIGINT"), ("install_date", "DATE"),
        ("platform", "VARCHAR"), ("country", "VARCHAR"), ("country_tier", "INTEGER"),
        ("acquisition_channel", "VARCHAR"), ("first_purchase_date", "DATE"),
    ],
    "player_day": [
        ("device_id", "BIGINT"), ("activity_date", "BIGINT"), ("player_level", "INTEGER"),
        ("session_count", "INTEGER"), ("playtime_minutes", "DOUBLE"), ("app_version", "VARCHAR"),
    ],
    "purchase": [
        ("purchase_id", "VARCHAR"), ("device_id", "BIGINT"), ("event_ts", "TIMESTAMP"),
        ("pack_name", "VARCHAR"), ("usd_amount", "DOUBLE"), ("currency_granted", "BIGINT"),
        ("sale_flag", "INTEGER"),
    ],
    "currency_spend": [
        ("device_id", "BIGINT"), ("event_ts", "TIMESTAMP"), ("spend_type", "VARCHAR"),
        ("currency_type", "VARCHAR"), ("currency_value", "BIGINT"), ("spend_category", "VARCHAR"),
        ("player_level", "INTEGER"), ("premium_balance", "BIGINT"),
        ("social_balance", "BIGINT"), ("grind_balance", "BIGINT"),
    ],
    "ad_view": [
        ("device_id", "BIGINT"), ("event_ts", "TIMESTAMP"), ("status", "VARCHAR"),
        ("placement", "VARCHAR"), ("reward_type", "VARCHAR"), ("reward_amount", "BIGINT"),
        ("ad_daily_count", "INTEGER"), ("ad_daily_cap", "INTEGER"),
    ],
}


def build_parquet(con: duckdb.DuckDBPyConnection, from_raw: bool) -> None:
    PARQUET.mkdir(parents=True, exist_ok=True)
    for table, cols in SCHEMAS.items():
        target = PARQUET / f"{table}.parquet"
        source = RAW / f"{table}.csv"
        if target.exists() and not from_raw:
            print(f"  {table:<16} parquet present, skipping")
            continue
        if not source.exists():
            print(f"  {table:<16} ERROR: {source} missing (run 00_extract_pdf_tables.py "
                  f"for player_profile/purchase, or restore the source CSVs)")
            raise SystemExit(1)
        spec = "{" + ", ".join(f"'{name}': '{kind}'" for name, kind in cols) + "}"
        con.execute(
            f"COPY (SELECT * FROM read_csv(?, header=true, columns={spec})) "
            f"TO '{target.as_posix()}' (FORMAT PARQUET)",
            [str(source)],
        )
        print(f"  {table:<16} built parquet from csv")


def build_db(con: duckdb.DuckDBPyConnection) -> None:
    for table in SCHEMAS:
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT * FROM read_parquet('{(PARQUET / f'{table}.parquet').as_posix()}')"
        )
    views = ROOT / "sql" / "00_views.sql"
    con.execute(views.read_text(encoding="utf8"))
    print(f"  shared views loaded from {views.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-raw", action="store_true", help="rebuild parquet from the raw CSVs")
    args = ap.parse_args()

    print("Building warehouse ...")
    con = duckdb.connect()
    build_parquet(con, args.from_raw)

    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    file_con = duckdb.connect(str(DB))
    build_db(file_con)

    print(f"\n{'table':<16}{'rows':>12}")
    for table in SCHEMAS:
        n = file_con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"{table:<16}{n:>12,}")
    print(f"\nwarehouse -> {DB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
