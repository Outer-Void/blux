PYTHON ?= python3

.PHONY: install sync check fmt lint types test wheel ci training-example

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

training-example:
	rm -rf build/training-example
	$(PYTHON) -m blux_training build training/examples/sources --out build/training-example --version example
	$(PYTHON) -m blux_training validate build/training-example
