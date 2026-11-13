import pandas as pd
import pytest

from steward.detectors.turnaround import (
    Cleaner,
    Task,
    detect,
    parse_requested_time,
    plan_queue,
    render_message,
)


def ts(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-03-02T{hhmm}:00Z")


@pytest.fixture(scope="module")
def decisions(ds):
    return detect(ds)


def test_requested_time_is_parsed_in_every_request_language():
    assert parse_requested_time("Wir kommen schon gegen 13:00 an, wäre das möglich?") == (13, 0)
    assert parse_requested_time("Llegamos a las 12:15, ¿es posible?") == (12, 15)
    assert parse_requested_time("Could we check out at 13:00 instead of 11?") == (13, 0)
    assert parse_requested_time("Any chance of an early check-in?") is None


def test_edf_serves_the_earliest_guest_first_and_fifo_does_not():
    tasks = [
        Task("R101", ts("08:00"), None, None),
        Task("R102", ts("08:00"), ts("15:00"), None),
        Task("R103", ts("08:30"), ts("11:30"), None),
    ]
    crew = [Cleaner("CLEANER_1", ts("09:00"), ts("14:30"))]
    edf = plan_queue(tasks, crew, 60, 45, "edf")
    fifo = plan_queue(tasks, crew, 60, 45, "fifo")
    assert edf["R103"].end == ts("10:00")
    assert fifo["R103"].end == ts("12:00")  # would miss the 11:30 guest
    assert crew[0].free_at == ts("09:00")  # planning never mutates the caller's crew


def test_robot_preclean_shortens_the_human_cleaning():
    crew = [Cleaner("CLEANER_1", ts("09:00"), ts("14:30"))]
    with_robot = plan_queue([Task("R101", ts("08:00"), None, ts("08:30"))], crew, 60, 45)
    without = plan_queue([Task("R101", ts("08:00"), None, ts("09:30"))], crew, 60, 45)
    assert with_robot["R101"].end == ts("09:45")
    assert without["R101"].end == ts("10:00")


def test_capacity_is_never_invented():
    tasks = [Task(f"R10{i}", ts("08:00"), ts("15:00"), None) for i in range(1, 6)]
    crew = [Cleaner("CLEANER_1", ts("12:00"), ts("14:00"))]
    slots = plan_queue(tasks, crew, 60, 45)
    done = [s for s in slots.values() if s.end is not None]
    assert len(done) == 2  # two 60-min cleanings start before the shift ends at 14:00
    assert sum(s.end is None for s in slots.values()) == 3
    assert plan_queue(tasks, [], 60, 45)["R101"].end is None


def test_messages_fall_back_to_english():
    text, lang = render_message("eta", "de", eta="12:15")
    assert lang == "de" and "12:15" in text
    assert render_message("eta", "pt", eta="12:15")[1] == "en"


def test_quickstart_conflicts_are_few_grouped_and_safe(ds, decisions):
    assert 0 < len(decisions) < 60  # 21 days: incidents, not routine turnovers
    assert all(d.episode_id and d.evidence and d.verification_plan for d in decisions)
    assert not any(d.action == "release_room" for d in decisions)
    # On-call and reassignment cost money or move a guest: never autonomous.
    for d in decisions:
        if d.resource_id == "ONCALL_1" or d.action in ("reassign_room", "request_human_approval"):
            assert d.requires_human_approval
    for d in decisions:
        if d.action == "notify_guest":
            assert d.proposed_message and d.message_language


def test_no_absent_or_off_shift_staff_is_assigned(ds, decisions):
    for d in decisions:
        if d.resource_id and not d.resource_id.startswith("ONCALL"):
            at = pd.Timestamp(d.timestamp_utc)
            assert ds.known_absence(d.resource_id, at) is None
            assert ds.on_shift(d.resource_id, at)


def test_early_checkin_conflict_is_raised_before_the_guest_arrives(ds, decisions):
    """Quickstart: a guest asked for 11:30, the previous guest left late, and they waited."""
    waits = ds.events[ds.events["type"] == "arrival_waiting_room"]
    asked = set(ds.guest_requests.query("request_type == 'early_checkin'")["booking_id"])
    first = waits[waits["booking_id"].isin(asked)].iloc[0]
    earlier = [
        d
        for d in decisions
        if first["room_id"] in d.affected_assets
        and pd.Timestamp(d.timestamp_utc) < first["timestamp_utc"]
        and pd.Timestamp(d.timestamp_utc) > first["timestamp_utc"] - pd.Timedelta(hours=12)
    ]
    assert {"schedule_cleaning", "notify_guest"} <= {d.action for d in earlier}
