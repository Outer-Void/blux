PYTHON ?= python3

.PHONY: install sync check fmt lint types test wheel ci

install:
	$(PYTHON) -m pip install -e sdk/python -e . -r requirements-dev.txt

sync:
	$(PYTHON) packaging/sync_schemas.py

check:
	$(PYTHON) packaging/sync_schemas.py --check

fmt:
	ruff format .

lint:
	ruff format --check .
	ruff check .

types:
	mypy

test:
	$(PYTHON) -m pytest

wheel:
	PYTHON=$(PYTHON) bash packaging/verify_wheel.sh

ci: check lint types test wheel
