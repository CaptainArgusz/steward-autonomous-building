# PROJECT.md — how we win this

The team's shared source of truth. If something here is wrong or out of date, fix it in the
same commit as the change that made it wrong. The jury-facing document is [README.md](README.md);
the original briefs are in [docs/challenge/](docs/challenge/) and
[data/quickstart/CHALLENGE.md](data/quickstart/CHALLENGE.md).

**Track:** Rhomberg — PLATO × CLARA Future, "The Autonomous Building" · proximata Hackathon '26
**Working name:** Steward (we may not name the product after PLATO or CLARA — brief §13)

## 1. What we are building

A system that replays one year of data from PLATO-40, a fictional 40-room self-check-in hotel,
and behaves like its autonomous operator: it detects operational problems, decides what to do,
acts within strict autonomy rules through a mock executor, says how success would be verified,
and turns the year into recommendations for operating (CLARA) and designing (PLATO) the next
building.

Three things come out of it:

1. **`decisions.jsonl`** — one JSON line per decision, produced by one command from a data
   directory. An automated check scores it on a dataset we have not seen.
2. **An interface** that shows, per incident: evidence → risk → decision → autonomy level /
   approval → verification. This is what we demo live.
3. **`insights.md`** — evidence-based recommendations for CLARA and PLATO, plus a target architecture.

"A dashboard alone is not enough; a chat agent without grounded decisions is not enough either."

**Our angle (decided Fri 19:30):** the story the jury should remember is **design feedback for
the next building** — findings about PLATO-40 that are proven with a year of data. Operations
detection (S01–S03, done deeply) is what makes those findings credible. Everything is grounded:
numbers come from the data or `building.json`, and we score ourselves against outcomes the
replay recorded.

## 2. Deadlines (Vienna time)

| When | What |
|---|---|
| Fri 13 Nov 14:00 | Start |
| **Sat 10:00** | **Our pipeline freeze.** Everything that affects `decisions.jsonl` is merged. |
| **Sat 10:30** | **Tag `pre-holdout` and push the tag.** Must exist before the holdout is released. |
| Sat ~11:00 | Holdout released on Discord. Run the pipeline **unchanged**, commit `decisions_holdout.jsonl`. |
| Sat 11:00 | n8n / Adaption workshop (optional) |
| Sat 12:00 | Record the 3-minute demo video |
| **Sat 13:30** | **Submit the form.** Repo public, in the proximata org. |
| **Sat 14:00** | **Code freeze — we plan for this one.** See the warning below. |
| Sat 15:00–16:00 | Track judging: 5 min live demo + 3 min questions |
| Sat 16:00–16:45 | Finals for the 7 track winners: 4 min demo + 2 min questions |

> **The documents disagree on code freeze.** The general rules and the track brief say 15:00;
> the CHALLENGE.md shipped with the dataset (§10) says 14:00 sharp. Late submissions are not
> accepted, so we work to 14:00 until an organiser confirms otherwise on Discord.

## 3. What must exist at submission

Repository root (checked by the jury):

- [ ] `decisions.jsonl` — pipeline output on the **dev** dataset
- [ ] `decisions_holdout.jsonl` — pipeline output on the **holdout** dataset
- [ ] `insights.md` — Level 3 feedback + target architecture
- [ ] git tag `pre-holdout`, created before the holdout release
- [ ] `README.md` with setup, the one run command, environment, architecture, assumptions,
      and a justification for every change made after `pre-holdout`
- [ ] `LICENSE` (MIT) and `.env.example`; no secrets anywhere in history
- [ ] Repo is public and in the proximata GitHub organisation, in our track/team folder

Via the submission form:

- [ ] 3-minute demo video, end to end
- [ ] Written submission: title, one-line pitch, team, problem, solution, tech stack, links
- [ ] Optional: live demo link, pitch deck PDF

## 4. Where the points are

| Points | Category | What it means for us |
|---:|---|---|
| 35 | Technical implementation | One-command pipeline incl. holdout (10) · detection on dev **and** holdout (10) · prioritisation and constraints (8) · robustness to bad data and look-alikes (7) |
| 30 | Autonomous operation | Safe, proportionate autonomy (10) · cross-domain coordination of staff, robots, HVAC, logistics (8) · guest communication and deadlines (6) · energy/comfort trade-offs (6) |
| 15 | Learning loop | Evidence-based feedback to CLARA and PLATO (10) · target architecture (5) |
| 20 | Demo | Scenario demo and visualisation (8) · decision and action trail (7) · limitations and verification (5) |

How the automated check and the jury punish us:

- **Alert spam counts negatively.** Repeated alerts for one incident count as one detection —
  group them with `episode_id` and emit follow-ups only when something changes.
- **False alarms** are judged across normal operation. The year is mostly normal and contains
  deliberate look-alikes. A quiet, correct detector beats a noisy one.
- **Safety violations**: releasing an uncleaned room, assigning absent staff or robots, acting
  without required approval or consent.
- **No points for fabricated execution or savings presented as real.** Label every simulated
  effect as simulated. "Not found" and stated limitations are rewarded.
- Ties go to the safer, more reproducible, better-evidenced solution.

Priority order: Level 1 (S01–S03) is mandatory and must be solid on unseen data before we
spend time on Level 2. Level 3 is 15 points for analysis that needs no new infrastructure —
do not leave it to the last hour.

## 5. Rules we never break

1. **Only use what was known at decision time.** Forecasts (`issued_utc`), tariffs
   (`published_utc`), bookings (`created_utc`) and roster changes (`status_known_utc`) all have
   a time of availability. Using the future is cheating and breaks on the holdout.
2. **Blank means missing, never zero.** Expect gaps, frozen and implausible values, duplicates
   and meter exchanges.
3. **Never release an uncleaned room.** Only human cleaners can certify and release; robots
   cannot, and they never enter an occupied room.
4. **Never schedule absent staff or robots; never invent capacity.**
5. **Never silently trade comfort or safety for energy.** Occupied rooms stay in 20–23 °C.
6. **Autonomy levels** (`requires_human_approval`, `requires_guest_consent`):

   | Level | Examples |
   |---|---|
   | Autonomous | schedule cleaning, dispatch robot, notify guest, setpoints in vacant rooms or within the comfort band, battery/load shifting, order waste pickup or restock, flag anomaly |
   | Human approval | dispatch technician or contractor, block a room, move a booked guest, anything with notable cost |
   | Approval + guest consent | entering an occupied room, water shutoff, safety-relevant interventions |

   Consent is *needed*, never granted. A proposed guest message is not a delivered message.
7. **BMS alarms are a clue, not ground truth** — delayed, incomplete, sometimes false.
8. **No memorising dev.** No hard-coded dates, rooms or thresholds fitted to single incidents.
   Quickstart and dev are independent runs: do not concatenate them.
9. **Say "PLATO-40"** for every finding. Nothing we find is a claim about real PLATO buildings.
10. **No secrets in git.** Keys come from Discord and live in `.env`.

Rules 3, 4 and 6 are partly enforced in code: `Decision` refuses approval-only actions marked
autonomous, and `steward validate` flags unknown assets and resources known to be absent.

## 6. Architecture

```
data dir ──► Dataset ──► detectors (one per problem class) ──► Decision[] ──► decisions.jsonl
             loaders,     replay in time order,                 contract,        │
             UTC, NaN     known-at-time only                    validated        ├─► mock executor ──► action trail
                                                                                 └─► UI (evidence, decision, approval, verification)
year of data + decisions ──► analysis ──► insights.md (CLARA rules, PLATO design, target architecture)
```

| Path | What | Owner |
|---|---|---|
| `src/steward/dataset.py` | Loads a dataset directory; UTC timestamps, missing stays missing; causal occupancy (`add_occupancy`) and `stays` | shared |
| `src/steward/contract.py` | `Decision` model, autonomy rules, file validator | shared — change only with team agreement |
| `src/steward/audit.py` | Log of look-alikes a detector dismissed, and why | shared |
| `src/steward/detectors/turnaround.py` | S01 turnaround conflicts | TBD |
| `src/steward/detectors/water_leak.py` | S02 water leaks | TBD |
| `src/steward/detectors/heating_comfort.py` | S03 energy vs comfort | TBD |
| `src/steward/detectors/maintenance.py` | S04 maintenance cascade | TBD |
| `src/steward/detectors/occupancy_surge.py` | S05 event weekend | TBD |
| `src/steward/detectors/energy.py` | Energy optimisation | TBD |
| `src/steward/detectors/logistics.py` | Waste, vending, robot tasks | TBD |
| `src/steward/executor.py` | Mock executor → action trail | TBD |
| `src/steward/pipeline.py`, `cli.py` | The single command | shared |
| UI (not started) | Demo interface | TBD |
| `insights.md` | Level 3 report | TBD |

The core pipeline is deterministic and needs no network or API key. If we use an LLM (for
example to draft multilingual guest messages), it must be optional with a template fallback,
so the holdout run cannot fail or change because of it.

## 7. Working mode

We are **not splitting into streams for now**: the build happens in one place, and the others
test and review from their own machines. Testers: pull `main`, run `make check`, open the UI,
and report anything that looks wrong as a GitHub issue or directly in the team chat — most
valuable are alerts that should not have fired and incidents the system missed.

Everyone adds findings to `insights.md` as they come across them.

## 8. Milestones

| When | Done means |
|---|---|
| Fri 19:30 | Kick-off: direction, scope, LLM role and UI decided (done) |
| Fri 23:00 | Each Level 1 detector emits first decisions on quickstart; UI shell renders a decisions file |
| Sat 03:00 | S01–S03 run on dev with look-alike handling; first two insights written with evidence |
| Sat 07:00 | Level 2 pieces in; executor and UI complete; `insights.md` drafted |
| Sat 10:00 | Pipeline freeze → `make decisions` → commit → **tag `pre-holdout`** |
| Sat 11:00 | Holdout run committed; from here only UI, docs, video |
| Sat 13:30 | Submitted |

## 9. How we work

**Setup**

```bash
git clone <repo> && cd <repo>
make setup          # uv sync
# put the dev dataset into data/dev/ (see data/README.md)
make check          # lint + tests + pipeline on quickstart
```

**Daily loop**

```bash
make run                              # quickstart → out/quickstart/ (gitignored)
make run DATA=data/dev                # full year → out/dev/
uv run steward run data/dev --only water_leak --out out/leak.jsonl --strict
uv run steward info data/dev
```

**Git**

- `main` always passes `make check`. Work on short-lived branches named `<stream>/<topic>`
  (`a/water-leak-night-baseline`), merge by PR, squash. No review required — CI green is the
  bar — but ask for a look on anything touching `contract.py`, `dataset.py` or `pipeline.py`.
- Merge small and often. Rebase on `main` before opening the PR. Each detector lives in its own
  file, so conflicts should be rare.
- **Never commit `decisions.jsonl` or `decisions_holdout.jsonl` from a feature branch.** They
  are generated files; only the integrator regenerates them on `main` (`make decisions`).
  Feature work writes to `out/`.
- After the `pre-holdout` tag, every commit that changes pipeline behaviour needs a line in
  README → "Changes after `pre-holdout`". The jury compares both states.

**Definition of done for a detector**

- Runs on quickstart and dev with `--strict`, zero validator errors.
- One `episode_id` per incident; follow-up decisions only when the situation changes.
- Every decision carries `evidence`, `verification_plan` and a `confidence`.
- You have looked at what it fires on during normal weeks and can explain each alert.
- No date, room or constant that only makes sense for one incident in dev.

## 10. Data cheat-sheet

Full reference: [data/quickstart/README.md](data/quickstart/README.md). Contract:
[data/quickstart/DECISION_CONTRACT.md](data/quickstart/DECISION_CONTRACT.md).

- Quickstart = 21 days in January, every scenario at least once. Dev = all of 2026.
- Timestamps are UTC; operations run on Europe/Vienna (check-in from 15:00, check-out until
  11:00, housekeeping 07:00–15:30, technician Mon–Fri 08:00–16:30). Mind DST — use `ds.local_tz`.
- Water meters are cumulative and can restart after a meter exchange (see `work_orders.csv`).
  The main meter also covers common areas, irrigation and small losses.
- `presence` is motion: sleeping guests show 0, cleaners show 1.
- Bookings hold *booked* times; actual check-in/out moments are in `events.jsonl`.
- Access log has entries only, no exits.
- There is no cooling. Heat pump is heating + hot water; `defrost` is a normal status.
- `ONCALL_1` has no roster rows: 15:30–22:00 local, ~45 min to site, one room at a time.
- Waste pickup Tue + Fri 07:00 local; vending restock Mon + Thu 10:00 local; extras need ~24 h.
- No request is ever answered in the data, and no action changes the replay.
- There is no ground truth. We validate by reasoning about evidence, not by a score.

## 11. Open decisions and questions

Decided (Fri 19:30):

- [x] **Main story:** design feedback for the next building.
- [x] **Scope:** S01–S03 done deeply, plus insights. Level 2 only if time is left.
- [x] **LLM role:** explains, never decides. Detection and decisions stay deterministic; an LLM
      may write incident summaries and guest messages from structured evidence. Template
      fallback first; outputs cached in the repo so a run never needs a key. Provider open —
      we will use a free API key if we can get one.
- [x] **UI:** a React web app built around a floor map of the 40 rooms.

Still open:

- [ ] **Project name.** "Steward" is a placeholder; it is also the Python package name, so
      rename early or not at all.
- [ ] **Alert budget and guest-request policy.** Dev has ~1,600 guest requests; decide which
      ones get a `notify_guest` decision without it becoming alert spam (ask a mentor).

Ask the organisers on Discord (early — these affect the plan):

- [ ] Is code freeze 14:00 or 15:00?
- [ ] How does our repo get into the proximata org "in the track folder, in the team folder" —
      fork, transfer, or a folder in a shared repo? How does the `pre-holdout` tag carry over?
- [ ] How is the holdout delivered — is it the encrypted `PLATO40_v2_holdout_encrypted.7z` we
      already have, with the password posted at ~11:00?
- [ ] Will the jury re-run our pipeline themselves, and on which machine?

## 12. Status (Fri 20:30)

Dev year: 113 decisions in 57 incidents, 314 look-alikes dismissed, 0 validator errors,
pipeline runs in ~12 s. `uv run steward score decisions.jsonl --data-dir data/dev` prints the
scorecard below.

| Area | State |
|---|---|
| S01 checkout conflict | First version. 38 incidents; a decision named the room before the guest arrived in 15 of 24 recorded waits (median lead 1.2 h). Cleaner jobs must fit shifts; robot and on-call capacity are now reserved. Overlapping bookings with unknown cancellation time do not trigger an automatic arrival promise. |
| S02 water leak | First version. 5 incidents (2 loud, 2 subtle, 1 visible only in the main-vs-rooms gap), each hours to days before the BMS alarm, complaint or work order. 97 baths/showers dismissed. |
| S03 heating vs comfort | First version. 2 incidents (heat-pump efficiency loss), opened ~5 h before rooms were affected. 26 of 29 cold complaints dismissed with a reason (24 are rooms still warming up after check-in). |
| Logistics | Weak baseline, unchanged: 12 extra pickups ordered, 2 of 3 real overflows preceded, 10 false alarms. Fix or remove before the freeze. |
| S04, S05, energy | Not implemented yet (advanced scenarios now in planned scope) |
| Insights | 12 findings in `insights.md`, regenerated by `steward insights data/dev`; 7 hypotheses listed as checked and not found. Target architecture section still to write. |
| UI | React floor map, incident list with evidence charts, design findings on the map, dismissed log, scorecard. `make ui`. |
| LLM explanations | Not started; templates only |
| Mock executor | Minimal: autonomous = executed (simulated), the rest pending approval |
| Holdout tooling | Archive in `data/holdout/` (checksum verified). Everyone needs a 7z tool: `brew install sevenzip`. |

Known issues to review:

- S03 needs about two days of heating-weather history before its efficiency baseline works.
- S02's main-meter rule only looks at night, so a pipe leak starting in the morning is caught late.
- Nobody but the author has read every alert yet. Testers: please do.

Data-quality and causality handling: blanks stay missing; implausible and frozen readings,
meter restarts and duplicates are masked where they are used; BMS alarms never trigger a
decision. `tests/test_causality.py` cuts the data at a point in time and checks that every
earlier decision is unchanged, for all detectors. The final cancelled status is not used in
replay planning because the data has no cancellation timestamp. Multiple known bookings for
one arrival room remain uncertain rather than triggering an automatic promise.

Capacity correction (13 November): the earlier planner could start a 60-minute cleaning
job with only 15 minutes left in a shift, dispatch a robot already busy on a recorded job,
describe one robot as pre-cleaning several rooms at once, and give two rooms the same
on-call cleaner completion time. The planner now requires full jobs to fit shifts,
checks recorded robot work and reserves each proposed robot dispatch, and gives the
single on-call cleaner sequential slots that fit its hours. Unobserved robot passes no
longer shorten cleaning estimates. The strict dev replay has 113 decisions in 57 incidents
with no validation errors or warnings; none of its three robot dispatches overlaps a
recorded robot job. Controlled tests cover shift end, busy robots, on-call queuing, and
robot-assisted cleaning duration. Guest reply deadlines and executor prerequisite checks
remain separate outstanding work.

## 13. Decision log

| When | Decision | Why |
|---|---|---|
| Fri 18:45 | Python 3.12 + uv + pandas; package `steward`; argparse CLI | Fastest shared ground for a CSV-heavy problem; lockfile gives identical environments |
| Fri 18:45 | Quickstart data committed, dev and holdout gitignored | Tests and CI run on a clean clone; the dev set has an 80 MB file and every team already has the download |
| Fri 18:45 | A crashing detector is skipped, not fatal (`--strict` to fail fast) | We get one unchanged run on the holdout; partial output beats none |
| Fri 18:45 | Deterministic `event_id`s and sorted output | Re-runs are byte-identical, so diffs of `decisions.jsonl` are meaningful |

| Fri 19:30 | Story = design feedback; scope = S01–S03 deep + insights | Rhomberg builds buildings; fewest teams will lead with it; Level 1 is mandatory |
| Fri 19:30 | LLM explains, never decides | Reproducible holdout run; no points for ungrounded output |
| Fri 19:30 | React floor-map UI | Team choice; shows findings by floor and orientation directly |
| Fri 19:30 | Detectors log dismissed look-alikes via `steward.audit` | Look-alike handling is otherwise invisible in `decisions.jsonl` |
| Fri 21:00 | Cancelled bookings count as expected arrivals; overlapping known bookings remain uncertain | `status` is a final outcome with no timestamp; using it to time a cancellation would be hindsight |
