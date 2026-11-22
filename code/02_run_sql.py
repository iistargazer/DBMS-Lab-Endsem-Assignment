"""Run every query file in sql/ and write each result block to outputs/tables/.

A query file contains named blocks:

    -- name: some_result
    SELECT ... ;

Each block is executed separately and written to
outputs/tables/<file_stem>__<name>.csv, so every number in the deck can be
traced back to a named query and a committed output file.

Usage:
    python code/02_run_sql.py [--only q1a] [--db data/warehouse.duckdb]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
OUT_DIR = ROOT / "outputs" / "tables"
BLOCK = re.compile(r"^--\s*name:\s*(\S+)\s*$", re.MULTILINE)


def parse_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(BLOCK.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sql = text[m.end():end].strip().rstrip(";").strip()
        if sql:
            blocks.append((m.group(1), sql))
    return blocks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default=None, help="run only query files whose name contains this")
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse.duckdb"))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"ERROR: {db} not found -- run code/01_build_warehouse.py first")

    files = sorted(p for p in SQL_DIR.glob("q*.sql"))
    if args.only:
        files = [p for p in files if args.only in p.name]
    if not files:
        sys.exit("ERROR: no matching query files in sql/")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db), read_only=True)

    total_blocks = 0
    print(f"Running {len(files)} query file(s) -> {OUT_DIR.relative_to(ROOT)}/\n")
    print(f"{'block':<52}{'rows':>8}  output")
    for path in files:
        blocks = parse_blocks(path.read_text(encoding="utf8"))
        if not blocks:
            print(f"  WARNING: no named blocks in {path.name}")
            continue
        for name, sql in blocks:
            try:
                df = con.execute(sql).df()
            except Exception as exc:  # surface the failing block, not just the file
                print(f"  FAILED: {path.name} :: {name}")
                print(f"    {exc}")
                raise SystemExit(1)
            out = OUT_DIR / f"{path.stem}__{name}.csv"
            df.to_csv(out, index=False)
            total_blocks += 1
            print(f"{path.stem[:46] + '__' + name:<52}{len(df):>8}  {out.name}")

    print(f"\n{total_blocks} result blocks written.")


if __name__ == "__main__":
    main()
