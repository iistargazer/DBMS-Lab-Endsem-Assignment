"""Recover player_profile.csv and purchase.csv from the two PDF printouts.

The exercise pack supplied these two tables as PDFs (3492 and 672 pages) rather
than CSV. Each page carries one block of ~45 rows, and pages alternate between
the main columns ("A" pages) and the trailing columns ("B" pages):

    player_profile : A = device_id .. country_tier       | B = acquisition_channel, first_purchase_date
    purchase       : A = purchase_id .. currency_granted | B = sale_flag

Block A of an A-page pairs positionally with block B of the next B-page. This
script reads the pages in order with `pdftotext -raw`, parses each page with a
regex, and refuses to write a table unless every paired page yielded the same
number of rows on both sides. It is deliberately strict: a silent off-by-one
here would corrupt every downstream number.

Requires poppler's `pdftotext` on PATH (macOS: brew install poppler,
Debian/Ubuntu: apt install poppler-utils, Windows: the msys2/poppler build).

Usage:
    python code/00_extract_pdf_tables.py [--raw-dir data/raw] [--force]
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

PP_MAIN = re.compile(r"^(\d+)\s+(?:(\d+)\s+)?(\d{4}-\d{2}-\d{2})\s+(Android|iOS)\s+(.+?)\s+(\d)$")
PP_TAIL = re.compile(r"^(organic|paid_social|paid_video)(?:\s+(\d{4}-\d{2}-\d{2}))?$")
PU_HEAD = re.compile(r"^(TX\d+)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})$")
PU_TAIL = re.compile(r"^(\S+)\s+(-?[\d.]+)\s+(-?\d+)$")
SALE = re.compile(r"^([01])$")

PP_HEADER = ["device_id", "original_device_id", "install_date", "platform", "country",
             "country_tier", "acquisition_channel", "first_purchase_date"]
PU_HEADER = ["purchase_id", "device_id", "event_ts", "pack_name", "usd_amount",
             "currency_granted", "sale_flag"]


def read_pages(pdf: Path) -> list[list[str]]:
    """Return the non-empty text lines of each page, in page order."""
    out = subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True, check=True)
    return [[line.strip() for line in page.splitlines() if line.strip()]
            for page in out.stdout.decode("utf8", "replace").split("\f")]


def parse_pp_main(lines: list[str]) -> list[tuple]:
    return [m.groups() for m in (PP_MAIN.match(line) for line in lines) if m]


def parse_pp_tail(lines: list[str]) -> list[tuple]:
    return [m.groups() for m in (PP_TAIL.match(line) for line in lines) if m]


def parse_pu_main(lines: list[str]) -> list[tuple]:
    """Under `pdftotext -raw` each purchase wraps onto two lines: the id/device/
    timestamp, then the pack name, amount and currency granted."""
    rows, i = [], 0
    while i < len(lines):
        head = PU_HEAD.match(lines[i])
        if head and i + 1 < len(lines):
            tail = PU_TAIL.match(lines[i + 1])
            if tail:
                pid, device, date, hh, mm, ss = head.groups()
                stamp = f"{date} {int(hh):02d}:{mm}:{ss}"  # the PDF drops the leading zero
                rows.append((pid, device, stamp) + tail.groups())
                i += 2
                continue
        i += 1
    return rows


def parse_sale(lines: list[str]) -> list[tuple]:
    return [m.groups() for m in (SALE.match(line) for line in lines) if m]


def pair_blocks(pdf: Path, main_parser, tail_parser, label: str):
    """Walk the pages, pair each A-page with the following B-page, and yield rows."""
    rows: list[list[str]] = []
    problems: list[str] = []
    pending: tuple[int, list[str]] | None = None

    for idx, lines in enumerate(read_pages(pdf)):
        if not lines:
            continue
        tag = "A" if len(main_parser(lines)) >= len(tail_parser(lines)) else "B"
        if tag == "A":
            if pending:
                problems.append(f"{label}: page {pending[0]} had no trailing block")
            pending = (idx, lines)
            continue
        if not pending:
            problems.append(f"{label}: page {idx} has a trailing block with no leading block")
            continue
        aidx, alines = pending
        a, b = main_parser(alines), tail_parser(lines)
        if len(a) != len(b):
            problems.append(f"{label}: page {aidx} has {len(a)} rows but page {idx} has {len(b)}")
        rows.extend(list(ra) + list(rb) for ra, rb in zip(a, b))
        pending = None

    if pending:
        problems.append(f"{label}: final page {pending[0]} had no trailing block")
    return rows, problems


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def extract(pdf: Path, out_csv: Path, header, main_parser, tail_parser, label) -> int:
    rows, problems = pair_blocks(pdf, main_parser, tail_parser, label)
    if problems:
        print(f"  {label}: {len(problems)} page-pairing problem(s) -- refusing to write")
        for p in problems[:10]:
            print(f"    {p}")
        raise SystemExit(1)
    write_csv(out_csv, header, rows)
    print(f"  {label}: {len(rows):,} rows -> {out_csv}")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default="data/raw", type=Path)
    ap.add_argument("--force", action="store_true", help="rebuild even if the CSV already exists")
    args = ap.parse_args()

    raw = args.raw_dir
    jobs = [
        (raw / "player_profile.pdf", raw / "player_profile.csv", PP_HEADER,
         parse_pp_main, parse_pp_tail, "player_profile"),
        (raw / "purchase.pdf", raw / "purchase.csv", PU_HEADER,
         parse_pu_main, parse_sale, "purchase"),
    ]

    todo = [j for j in jobs if args.force or not j[1].exists()]
    if not todo:
        print("Both recovered CSVs already exist in %s (use --force to rebuild)." % raw)
        return

    if shutil.which("pdftotext") is None:
        print("ERROR: pdftotext not found on PATH; install poppler to re-derive the tables.")
        raise SystemExit(1)

    print(f"Recovering tables from PDF printouts in {raw} ...")
    for pdf, out, header, mp, tp, label in todo:
        if not pdf.exists():
            print(f"ERROR: missing {pdf}")
            raise SystemExit(1)
        extract(pdf, out, header, mp, tp, label)
    print("Done.")


if __name__ == "__main__":
    main()
