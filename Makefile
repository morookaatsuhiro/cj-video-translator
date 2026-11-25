PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
EXTRAS ?= media,asr,demo,dev,eval

.PHONY: setup lint format test check smoke demo demo-html demo-package mock-live
setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install -e '.[${EXTRAS}]'
lint:
	$(BIN)/ruff check src scripts tests
	$(BIN)/ruff format --check src scripts tests
format:
	$(BIN)/ruff check --fix src scripts tests
	$(BIN)/ruff format src scripts tests
test:
	$(BIN)/pytest -m 'not gpu and not slow and not network'
check: lint test
	@test "$$(wc -c < AGENTS.md)" -lt 32768
smoke:
	$(BIN)/python scripts/synthetic.py
	$(BIN)/cjvmt run --input tests/fixtures/synthetic_dialogue.mp4 --direction ja2zh --backend mock --limit 2 --path-file .cache/smoke-run
	$(BIN)/cjvmt compare --run "$$(cat .cache/smoke-run)"
	$(BIN)/cjvmt validate --run "$$(cat .cache/smoke-run)"
	$(BIN)/cjvmt report --run "$$(cat .cache/smoke-run)"
	$(MAKE) demo-html
demo:
	$(BIN)/cjvmt demo --mode replay
demo-html:
	$(BIN)/cjvmt demo-html
demo-package:
	$(BIN)/python scripts/package_demo.py
mock-live:
	$(BIN)/cjvmt realtime --input file_sim --backend mock
