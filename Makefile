# DBMS Lab end-semester analysis -- reproduce every output from the raw data.
#
#   make all         build parquet + warehouse, then run all queries and models
#   make extract     re-derive player_profile.csv / purchase.csv from the PDFs
#   make tables      re-run the queries only
#   make models      re-run the bootstrap intervals and the churn model only
#   make clean       remove generated tables and the warehouse database
#
# On Windows, override the interpreter if `python` is not on PATH:
#   make PYTHON=py all

PYTHON := $(shell command -v python 2>/dev/null || command -v python3)

ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.PHONY: all everything setup extract data tables models clean

all: tables models

setup: .venv/.installed

.venv/.installed: requirements.txt
	@test -x $(PY) || $(PYTHON) -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	touch .venv/.installed

data: setup
	$(PY) code/01_build_warehouse.py

tables: data
	$(PY) code/02_run_sql.py

models: data
	$(PY) code/03_channel_ci.py
	$(PY) code/04_churn_model.py

extract: setup
	$(PY) code/00_extract_pdf_tables.py

everything: setup extract data tables models

clean:
	rm -f data/warehouse.duckdb
	rm -f outputs/tables/*.csv
