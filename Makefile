# Everyday commands. `make check` before every push.
DATA    ?= data/quickstart
HOLDOUT ?= data/holdout/PLATO40_v2_holdout

.PHONY: setup run decisions holdout validate test lint format check info

setup:            ## install the pinned environment
	uv sync

run:              ## run the pipeline on $(DATA) into out/ (safe: never touches the deliverables)
	uv run steward run $(DATA) --out out/$(notdir $(DATA))/decisions.jsonl --trail out/$(notdir $(DATA))/action_trail.jsonl

decisions:        ## INTEGRATOR ONLY: regenerate the root deliverable from the dev year
	uv run steward run data/dev --out decisions.jsonl --trail out/dev/action_trail.jsonl

holdout:          ## INTEGRATOR ONLY, after the pre-holdout tag: run unchanged on the holdout
	uv run steward run $(HOLDOUT) --out decisions_holdout.jsonl --trail out/holdout/action_trail.jsonl

validate:         ## validate the committed deliverable against the contract and the dev data
	uv run steward validate decisions.jsonl --data-dir data/dev

info:             ## summarise $(DATA)
	uv run steward info $(DATA)

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

check: lint test run   ## what CI runs
