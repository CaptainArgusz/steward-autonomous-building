"""The mock executor refuses unsafe actions and respects dependencies."""

import pandas as pd

from steward import executor
from steward.contract import Decision, make_event_id


def _d(ds, action, room, at, **kw):
    at = pd.Timestamp(at)
    return Decision(
        event_id=make_event_id("test", room, at, action),
        timestamp_utc=at,
        affected_assets=[room] + ([kw["resource_id"]] if kw.get("resource_id") else []),
        issue_type="test",
        action=action,
        priority="medium",
        reason="test",
        requires_human_approval=kw.pop("approval", False),
        **kw,
    )


def _status(trail, d):
    return next(r for r in trail if r["event_id"] == d.event_id)


def test_release_without_completed_cleaning_is_refused(ds):
    lc = ds.lifecycle
    out = lc[~lc["occupied"]].iloc[0]
    cleaned = ds.events[
        (ds.events["type"] == "cleaning_completed") & (ds.events["room_id"] == out.room_id)
    ]
    done = cleaned[cleaned["timestamp_utc"] > out.timestamp_utc]["timestamp_utc"].min()
    early = _d(ds, "release_room", out.room_id, out.timestamp_utc + pd.Timedelta(minutes=1))
    later = _d(ds, "release_room", out.room_id, done + pd.Timedelta(minutes=1))
    trail = executor.execute([early, later], ds)
    assert _status(trail, early)["status"] == "refused_safety_check"
    assert "cleaning" in _status(trail, early)["reason"]
    assert _status(trail, later)["status"] == "executed_simulated"


def test_robot_never_sent_into_an_occupied_room(ds):
    lc = ds.lifecycle
    stay = lc[lc["occupied"]].iloc[0]
    robot = ds.staff_roster.loc[ds.staff_roster["role"] == "robot", "resource_id"].iloc[0]
    d = _d(
        ds,
        "dispatch_robot",
        stay.room_id,
        stay.timestamp_utc + pd.Timedelta(minutes=5),
        resource_id=robot,
    )
    trail = executor.execute([d], ds)
    assert _status(trail, d)["status"] == "refused_safety_check"


def test_dependent_action_waits_for_a_pending_approval(ds):
    at = ds.start + pd.Timedelta(hours=30)
    approve = _d(ds, "reassign_room", "R101", at, approval=True)
    tell = _d(ds, "notify_guest", "R102", at, dependencies=[approve.event_id])
    trail = executor.execute([tell, approve], ds)  # given out of order on purpose
    assert [r["event_id"] for r in trail] == [approve.event_id, tell.event_id]
    assert _status(trail, approve)["status"] == "pending_human_approval"
    assert _status(trail, tell)["status"] == "blocked_by_dependency"


def test_known_absent_staff_is_not_used(ds):
    r = ds.staff_roster
    sick = r[(r["status"] != "scheduled")].iloc[0]
    at = max(sick.start_utc, sick.status_known_utc) + pd.Timedelta(minutes=1)
    d = _d(ds, "schedule_cleaning", "R101", at, resource_id=sick.resource_id)
    trail = executor.execute([d], ds)
    assert _status(trail, d)["status"] == "refused_safety_check"


def test_pipeline_output_passes_every_safety_check(ds):
    from steward.detectors import DETECTORS

    decisions = [d for detect in DETECTORS.values() for d in detect(ds)]
    trail = executor.execute(decisions, ds)
    assert not [r for r in trail if r["status"] == "refused_safety_check"]
