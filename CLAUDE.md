# Instructions for AI coding assistants

Read [PROJECT.md](PROJECT.md) first: it has the goal, deadlines, scoring and the rules below in
full. This is a 24-hour hackathon; prefer small working changes over frameworks.

## Commands

```bash
make setup                      # uv sync
make check                      # ruff + pytest + pipeline on data/quickstart — run before every push
make run DATA=data/dev          # writes to out/, never to the root deliverables
uv run steward run data/dev --only <detector> --out out/x.jsonl --strict
```

## Rules that are easy to break

- A detector is `detect(ds: Dataset) -> list[Decision]` in `src/steward/detectors/<name>.py`.
  Build decisions with `steward.contract.Decision` and `make_event_id`; never write JSON by hand.
- A decision at time t may only use data known at t. Filter on `created_utc`, `published_utc`,
  `issued_utc`, `status_known_utc` where a table has them. Never look ahead in the replay.
- Blank is missing, not zero. Do not `fillna(0)` sensor or meter data.
- Timestamps are UTC; operating hours are Europe/Vienna — convert with `ds.local_tz`.
- One `episode_id` per incident. Alert spam and false alarms cost points; when unsure, stay quiet.
- No hard-coded dates, room numbers or constants fitted to a single incident: the pipeline is
  judged on an unseen dataset.
- Label simulated effects as simulated. Never claim a message was delivered or a saving measured.
- Do not edit `decisions.jsonl` / `decisions_holdout.jsonl` by hand or commit them from a
  feature branch. Do not change `contract.py` semantics without the team agreeing.
- Never commit secrets or the dev/holdout datasets.
- Findings always refer to "PLATO-40", never to real PLATO buildings.
