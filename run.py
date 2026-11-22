"""Cross-platform task runner. Mirrors the Makefile for machines without GNU make.

Targets:
    setup       create .venv and install requirements (also runs implicitly)
    extract     re-derive player_profile.csv / purchase.csv from the PDF printouts
    data        build data/parquet/*.parquet and data/warehouse.duckdb
    tables      run every sql/q*.sql block into outputs/tables/
    models      re-run the bootstrap intervals and the churn model
    ltv         re-run the optional 90-day value model
    deck        rebuild deck/DBMS_EndSem_Deck.pptx from the CSVs only
    all         data + tables + models + ltv + deck  (the default pipeline)
    everything  extract + data + tables + models + ltv + deck
    clean       remove the warehouse database and generated tables

Usage:
    python run.py all
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_deps() -> None:
    try:
        import duckdb  # noqa: F401
        import pandas  # noqa: F401
        return
    except ImportError:
        pass
    py = venv_python()
    if not py.exists():
        print("Creating .venv ...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("Installing requirements ...")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-r",
                    str(ROOT / "requirements.txt")], check=True)
    os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])


def run(script: str, *extra: str) -> None:
    cmd = [sys.executable, str(ROOT / "code" / script), *extra]
    print(f"\n$ {' '.join(Path(c).name if i == 1 else c for i, c in enumerate(cmd))}")
    subprocess.run(cmd, check=True, cwd=ROOT)


def task_setup() -> None:
    ensure_deps()
    print("Environment ready.")


def task_extract() -> None:
    run("00_extract_pdf_tables.py")


def task_data() -> None:
    run("01_build_warehouse.py")


def task_tables() -> None:
    run("02_run_sql.py")


def task_models() -> None:
    run("03_channel_ci.py")
    run("04_churn_model.py")


def task_ltv() -> None:
    run("05_ltv_model.py")


def task_deck() -> None:
    run("06_build_deck.py")


def task_clean() -> None:
    for target in (ROOT / "data" / "warehouse.duckdb", ROOT / "outputs" / "tables"):
        if target.is_dir():
            for child in target.iterdir():
                child.unlink()
            print(f"cleared {target.relative_to(ROOT)}/")
        elif target.exists():
            target.unlink()
            print(f"removed {target.relative_to(ROOT)}")


TASKS = {
    "setup": task_setup,
    "extract": task_extract,
    "data": task_data,
    "tables": task_tables,
    "models": task_models,
    "ltv": task_ltv,
    "deck": task_deck,
    "clean": task_clean,
}


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    ensure_deps()
    if target == "all":
        task_data()
        task_tables()
        task_models()
        task_ltv()
        task_deck()
    elif target == "everything":
        task_extract()
        task_data()
        task_tables()
        task_models()
        task_ltv()
        task_deck()
    elif target in TASKS:
        TASKS[target]()
    else:
        sys.exit(f"Unknown target {target!r}. Choose from: all, everything, {', '.join(TASKS)}")


if __name__ == "__main__":
    main()
