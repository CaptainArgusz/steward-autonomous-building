import pandas as pd

from steward.contract import Decision
from steward.export import _activity, build_days

from .conftest import QUICKSTART


def decision(action, room, at, **fields):
    return Decision(
        event_id=fields.pop(
            "event_id", f"test:{room}:{action}:{pd.Timestamp(at).strftime('%H%M')}"
        ),
        episode_id=fields.pop("episode_id", f"test:{room}"),
        timestamp_utc=at,
        affected_assets=[room] + ([fields["resource_id"]] if fields.get("resource_id") else []),
        issue_type=fields.pop("issue_type", "room_turnaround"),
        action=action,
        priority=fields.pop("priority", "high"),
        reason="Visual replay test.",
        requires_human_approval=fields.pop("requires_human_approval", False),
        **fields,
    )


def test_day_bundle_adds_executed_actors_without_changing_room_states(ds):
    shift = ds.staff_roster[
        (ds.staff_roster["role"] == "robot") & (ds.staff_roster["status"] == "scheduled")
    ].iloc[0]
    at = shift.start_utc + pd.Timedelta(minutes=1)
    sample = pd.DataFrame({"timestamp_utc": [at] * len(ds.room_ids), "room_id": ds.room_ids})
    room = ds.add_occupancy(sample).loc[lambda x: ~x["occupied"], "room_id"].iloc[0]
    d = decision("dispatch_robot", room, at, resource_id=shift.resource_id)

    plain = build_days(QUICKSTART)["days"]
    enhanced = build_days(QUICKSTART, [d])["days"]
    day = at.tz_convert(ds.local_tz).strftime("%Y-%m-%d")
    assert plain[day]["rooms"] == enhanced[day]["rooms"]
    actor = next(x for x in enhanced[day]["actors"] if x["id"] == d.event_id)
    assert actor["room_id"] == room
    assert actor["role"] == "robot"
    assert actor["status"] == "executed_simulated"
    assert actor["duration_source"] in ("recorded_events", "fallback_30m")


def test_refused_robot_dispatch_is_not_exported_as_motion(ds):
    occupied = ds.lifecycle[ds.lifecycle["occupied"]].iloc[0]
    robot = ds.staff_roster.loc[ds.staff_roster["role"] == "robot", "resource_id"].iloc[0]
    at = occupied.timestamp_utc + pd.Timedelta(minutes=1)
    d = decision("dispatch_robot", occupied.room_id, at, resource_id=robot)
    day = at.tz_convert(ds.local_tz).strftime("%Y-%m-%d")
    assert not any(x["id"] == d.event_id for x in _activity(ds, [d])[day]["actors"])


def test_spatial_effects_are_grouped_per_incident(ds):
    room = ds.room_ids[0]
    at = ds.start + pd.Timedelta(hours=2)
    first = decision("flag_anomaly", room, at, issue_type="water_leak")
    second = decision(
        "request_inspection",
        room,
        at + pd.Timedelta(hours=1),
        issue_type="water_leak",
        requires_human_approval=True,
        event_id="test:water:inspect",
    )
    day = at.tz_convert(ds.local_tz).strftime("%Y-%m-%d")
    effects = _activity(ds, [first, second])[day]["effects"]
    assert len(effects) == 1
    assert effects[0]["kind"] == "water"
    assert effects[0]["major"] is True
    assert effects[0]["requires_human_approval"] is True
    assert effects[0]["action_ids"] == [second.event_id]
