# Steward — an autonomous operator for the PLATO-40 building

proximata Hackathon '26 · Rhomberg track · "PLATO × CLARA Future — The Autonomous Building"

Steward replays a year of operating data from **PLATO-40**, a fictional 40-room self-check-in
hotel, and acts as its operator: it detects operational problems, decides on prioritised
actions within explicit autonomy rules, records them through a mock executor, and states how
each action would be verified.

> PLATO-40 is a synthetic reference property. Nothing here is a statement about real PLATO
> buildings or about CLARA. All effects of actions are simulated; no savings are measured.

**Status: work in progress.** The pipeline, the decision contract and its validator, the data
loaders and the mock executor work end to end. Detection currently covers one baseline rule
(central waste container). See [PROJECT.md](PROJECT.md) §12 for the current state of every part.

## Run it

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and all dependencies).

```bash
uv sync
uv run steward run <data_dir> --out decisions.jsonl
```

That one command takes a dataset directory (the folder containing `building.json`, the CSV
files and `events.jsonl`) and writes `decisions.jsonl`. It then validates the file against the
decision contract and prints a summary. It needs no network access and no API key.

```bash
uv run steward run data/quickstart --out out/quickstart/decisions.jsonl   # 21-day set, included
uv run steward run data/dev        --out decisions.jsonl                  # full year → root deliverable
uv run steward run data/holdout/<extracted folder> --out decisions_holdout.jsonl
```

The quickstart dataset is included in this repository. The dev and holdout datasets are not;
[data/README.md](data/README.md) says where to put them.

Other commands:

```bash
uv run steward validate decisions.jsonl --data-dir data/dev   # contract + building checks
uv run steward info data/dev                                  # tables, row counts, missing cells
make check                                                    # lint, tests, quickstart run
```

## Deliverables

| File | Content |
|---|---|
| [decisions.jsonl](decisions.jsonl) | Pipeline output on the dev dataset, one decision per line |
| `decisions_holdout.jsonl` | Pipeline output on the holdout dataset (added after its release) |
| [insights.md](insights.md) | Feedback for CLARA and PLATO, and the target architecture |
| tag `pre-holdout` | The code state before the holdout dataset was released |

## Changes after `pre-holdout`

None yet. Every change to pipeline behaviour after the tag is listed here with its reason.

## How it works

```
data dir ─► Dataset ─► detectors ─► Decision[] ─► decisions.jsonl ─► mock executor ─► action trail
```

- **`steward.dataset`** loads one dataset directory. Timestamps become timezone-aware UTC;
  blank fields stay missing and are never read as zero.
- **`steward.detectors`** holds one module per problem class. A detector replays the data in
  time order and may only use information that was available at the decision's timestamp
  (forecast issue time, tariff publication time, booking creation time, when a roster change
  became known).
- **`steward.contract`** defines the `Decision` model after `DECISION_CONTRACT.md`. It rejects
  decisions that break the autonomy rules — for example `dispatch_technician` or `block_room`
  without `requires_human_approval`, or guest consent without approval — and derives the
  `autonomy` level from the two flags.
- **`steward.pipeline`** runs all detectors, sorts the result and writes it. Output is
  deterministic: the same data gives a byte-identical file.
- **`steward.executor`** is a mock executor. Autonomous decisions are logged as
  `executed_simulated`; everything else stays `pending_human_approval` (and guest consent).

Only actions from the challenge vocabulary are used.

## Environment

No environment variables are required. [.env.example](.env.example) lists optional ones.

## Assumptions and limitations

- The data is an offline replay: no action changes later observations, so we cannot observe
  whether an action worked. Each decision instead carries a `verification_plan`.
- There is no ground truth in the participant data. Detection quality is argued from evidence.
- A detector that raises an error is logged and skipped so that a run still produces output;
  the command then exits with a non-zero status. `--strict` aborts instead.

## Repository layout

```
src/steward/        pipeline code
tests/              tests (run on data/quickstart)
data/quickstart/    21-day dataset, as distributed (CC BY 4.0)
docs/challenge/     the track brief and general rules
PROJECT.md          team working agreement, plan and status
```

## Team

TBD

## Licence

Code: [MIT](LICENSE). Data: PLATO-40 synthetic dataset, proximata Hackathon '26 · Rhomberg
track, CC BY 4.0. PLATO and CLARA are names of Rhomberg products and are used here only to
refer to the challenge.
