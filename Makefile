# One entry point for the checks CI runs. `make check` before every push.
PYTHON := .venv/bin/python

.PHONY: venv test lint diagram check install

venv: ## Home Assistant 2026.9.4 test environment, built with uv
	uv venv -q --allow-existing --python 3.14 .venv
	VIRTUAL_ENV=.venv uv pip install -q -r requirements_test.txt

test: ## The pytest suite
	$(PYTHON) -m pytest -q

lint: ## ruff
	$(PYTHON) -m ruff check .

diagram: ## Redraw docs/assets/architecture-*.svg and the README tables
	python3 tools/gen_diagram.py
	python3 tools/gen_docs.py

check: lint test ## Everything CI checks
	python3 tools/gen_diagram.py --check
	python3 tools/gen_docs.py --check

install: ## Copy the committed integration to Home Assistant (see .env.example)
	scripts/install-on-ha.sh
