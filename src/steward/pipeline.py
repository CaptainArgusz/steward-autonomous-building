"""The pipeline: data directory in, decisions out. This is what runs on the holdout."""

from __future__ import annotations

import sys
import time
import traceback
from collections import Counter
from pathlib import Path

from steward import audit, executor
from steward.contract import Decision, write_decisions
from steward.dataset import Dataset
from steward.detectors import DETECTORS


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def run(
    data_dir: str | Path,
    out: str | Path = "decisions.jsonl",
    trail: str | Path | None = None,
    only: list[str] | None = None,
    strict: bool = False,
) -> tuple[list[Decision], list[str]]:
    """Run every detector and write the decisions file. Returns (decisions, failed detectors).

    A detector that crashes is logged and skipped rather than aborting the run: on the
    holdout we get one unchanged run, and partial output beats none. ``strict`` re-raises
    instead and is what tests and CI use.
    """
    ds = Dataset(data_dir)
    ds.__dict__["_disable_weekend_coordination"] = bool(only and "occupancy_surge" not in only)
    audit.drain()  # start clean when run() is called more than once in a process
    _log(f"steward: {ds.profile} dataset at {ds.root} ({ds.start:%Y-%m-%d} to {ds.end:%Y-%m-%d})")

    decisions: list[Decision] = []
    failed: list[str] = []
    for name, detect in DETECTORS.items():
        if only and name not in only:
            continue
        started = time.perf_counter()
        try:
            found = detect(ds)
        except Exception:
            if strict:
                raise
            failed.append(name)
            _log(f"  {name:<24} FAILED\n{traceback.format_exc()}")
            continue
        decisions.extend(found)
        _log(f"  {name:<24} {len(found):>5} decisions  {time.perf_counter() - started:5.1f}s")

    duplicates = len(decisions) - len({d.event_id for d in decisions})
    if duplicates:
        raise ValueError(
            f"{duplicates} duplicate event_id(s) across detectors - IDs must be unique"
        )

    write_decisions(decisions, out)
    _log(f"steward: wrote {len(decisions)} decisions to {out}")
    dismissed = audit.drain()
    if trail:
        trail_rows = executor.execute(decisions, ds)
        executor.write_trail(trail_rows, trail)
        counts = Counter(r["status"] for r in trail_rows)
        _log("steward: action trail " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        dismissed_path = Path(trail).with_name("dismissed.jsonl")
        audit.write(dismissed, dismissed_path)
        _log(f"steward: wrote mock-executor action trail to {trail}")
        _log(f"steward: wrote {len(dismissed)} dismissed look-alikes to {dismissed_path}")
    return decisions, failed
