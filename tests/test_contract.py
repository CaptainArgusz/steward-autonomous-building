import json

import pandas as pd
import pytest
from pydantic import ValidationError

from steward.contract import Decision, validate_file

from .conftest import QUICKSTART


def write(tmp_path, *rows):
    path = tmp_path / "decisions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_official_sample_passes_the_contract():
    report = validate_file(QUICKSTART / "decisions_sample.jsonl")
    assert report.ok, report.summary()
    assert len(report.decisions) == 4


def test_autonomy_is_derived_from_the_flags(valid):
    assert Decision(**valid).autonomy == "autonomous"
    assert Decision(**{**valid, "requires_human_approval": True}).autonomy == "human_approval"
    both = {**valid, "requires_human_approval": True, "requires_guest_consent": True}
    assert Decision(**both).autonomy == "approval_and_consent"


def test_timestamps_are_written_as_utc_z(valid):
    vienna = pd.Timestamp("2026-07-01 10:00", tz="Europe/Vienna")
    assert Decision(**{**valid, "timestamp_utc": vienna}).timestamp_utc == "2026-07-01T08:00:00Z"
    with pytest.raises(ValidationError):
        Decision(**{**valid, "timestamp_utc": "2026-07-01 08:00"})


@pytest.mark.parametrize(
    "change",
    [
        {"action": "dispatch_technician"},  # approval-only action marked autonomous
        {"action": "block_room"},
        {"requires_guest_consent": True},  # consent without approval
        {"autonomy": "human_approval"},  # contradicts requires_human_approval=false
        {"priority": "urgent"},
        {"affected_assets": []},
        {"confidence": 1.5},
    ],
)
def test_unsafe_or_malformed_decisions_are_rejected(valid, change):
    with pytest.raises(ValidationError):
        Decision(**{**valid, **change})


def test_file_checks_catch_duplicates_and_unknown_assets(tmp_path, valid, ds):
    path = write(tmp_path, valid, valid, {**valid, "event_id": "t-2", "affected_assets": ["R999"]})
    errors = "\n".join(validate_file(path, ds).errors)
    assert "duplicate event_id" in errors
    assert "R999" in errors


def test_assigning_a_known_absent_resource_is_an_error(tmp_path, valid, ds):
    roster = ds.staff_roster
    sick = roster[
        (roster["status"] == "sick") & (roster["status_known_utc"] <= roster["start_utc"])
    ].iloc[0]
    decision = {**valid, "timestamp_utc": sick["start_utc"], "resource_id": sick["resource_id"]}
    path = tmp_path / "d.jsonl"
    path.write_text(Decision(**decision).to_json_line() + "\n", encoding="utf-8")
    assert any("known to be sick" in e for e in validate_file(path, ds).errors)
