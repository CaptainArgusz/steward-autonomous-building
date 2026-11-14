import pandas as pd

from steward import executor, pipeline
from steward.cli import main
from steward.contract import validate_file
from steward.detectors.logistics import next_regular_pickup

from .conftest import QUICKSTART


def test_pipeline_output_is_valid_and_deterministic(tmp_path, ds):
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    decisions, failed = pipeline.run(QUICKSTART, out=first, strict=True)
    pipeline.run(QUICKSTART, out=second, strict=True)

    assert failed == []
    assert first.read_bytes() == second.read_bytes()
    report = validate_file(first, ds)
    assert report.ok, report.summary()
    assert len(report.decisions) == len(decisions)


def test_single_command_entry_point(tmp_path):
    out = tmp_path / "decisions.jsonl"
    assert main(["run", str(QUICKSTART), "--out", str(out), "--strict"]) == 0
    assert out.is_file()
    assert main(["run", str(tmp_path / "nope"), "--out", str(out)]) == 2


def test_executor_never_executes_what_needs_approval(tmp_path):
    decisions, _ = pipeline.run(QUICKSTART, out=tmp_path / "d.jsonl", strict=True)
    for row in executor.execute(decisions):
        assert row["simulated"] is True
        if row["autonomy"] != "autonomous":
            assert row["status"].startswith("pending_")
        else:  # autonomous: executed, or waiting for an earlier step that still needs approval
            assert row["status"] in ("executed_simulated", "blocked_by_dependency")


def test_next_regular_pickup_follows_local_time_across_dst():
    tz = "Europe/Vienna"
    # Winter (UTC+1): Monday evening -> Tuesday 07:00 local = 06:00 UTC
    assert next_regular_pickup(pd.Timestamp("2026-01-05T20:00Z"), tz) == pd.Timestamp(
        "2026-01-06T06:00Z"
    )
    # Summer (UTC+2): Tuesday just after pickup -> Friday 07:00 local = 05:00 UTC
    assert next_regular_pickup(pd.Timestamp("2026-07-07T05:30Z"), tz) == pd.Timestamp(
        "2026-07-10T05:00Z"
    )
