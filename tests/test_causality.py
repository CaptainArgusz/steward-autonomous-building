"""No detector may use information from the future.

We run each detector on the full quickstart data and again on a copy that ends at a cut-off
(everything not yet known at the cut-off removed or reset). Every decision taken before the
cut-off must be identical in both runs.
"""

import pandas as pd
import pytest

from steward import audit
from steward.detectors import DETECTORS
from steward.detectors.turnaround import _Replay
from steward.detectors.water_leak import interval_flow

# Column that says when a row became known; default is its own timestamp.
KNOWN_AT = {
    "bookings": "created_utc",
    "work_orders": "created_utc",
    "tariff": "published_utc",
    "weather_forecast": "issued_utc",
}
TABLES = (
    "bookings", "access_log", "room_telemetry", "room_water", "water_main", "energy", "weather",
    "weather_forecast", "tariff", "equipment", "logistics", "work_orders", "guest_requests", "events",
)  # fmt: skip


def truncated(ds, cut):
    short = type(ds)(ds.root)
    for table in TABLES:
        df = getattr(ds, table)
        short.__dict__[table] = df[df[KNOWN_AT.get(table, "timestamp_utc")] <= cut]
    # A roster row exists from the start; only its absence status arrives later.
    roster = ds.staff_roster.copy()
    roster.loc[roster["status_known_utc"] > cut, "status"] = "scheduled"
    short.__dict__["staff_roster"] = roster
    # Work orders created before the cut-off may only be completed after it.
    wo = short.__dict__["work_orders"].copy()
    late = wo["completed_utc"] > cut
    wo.loc[late, "completed_utc"] = pd.NaT
    wo.loc[late & (wo["status"] == "completed"), "status"] = "open"
    short.__dict__["work_orders"] = wo
    return short


@pytest.mark.parametrize(
    "name",
    [
        "room_turnaround",
        "water_leak",
        "heating_comfort",
        "ventilation_maintenance",
        "occupancy_surge",
        "logistics",
    ],
)
@pytest.mark.parametrize("fraction", [0.45, 0.7])
def test_decisions_before_a_cutoff_do_not_change_when_the_future_is_removed(ds, name, fraction):
    cut = (ds.start + (ds.end - ds.start) * fraction).floor("15min")
    detect = DETECTORS[name]
    full = [d.to_json_line() for d in detect(ds) if pd.Timestamp(d.timestamp_utc) <= cut]
    early = [
        d.to_json_line() for d in detect(truncated(ds, cut)) if pd.Timestamp(d.timestamp_utc) <= cut
    ]
    audit.drain()
    assert early == full


def test_guest_language_is_only_taken_from_requests_already_received(ds):
    replay = _Replay(ds)
    request = ds.guest_requests.dropna(subset=["booking_id", "language"]).iloc[0]
    at = request["timestamp_utc"] - pd.Timedelta(seconds=1)
    known = ds.guest_requests[
        (ds.guest_requests["booking_id"] == request["booking_id"])
        & (ds.guest_requests["timestamp_utc"] <= at)
    ]
    expected = str(known.sort_values("timestamp_utc").iloc[-1]["language"]) if len(known) else "en"
    assert replay.language_for(request["booking_id"], at) == expected


def test_unreported_absence_does_not_remove_a_scheduled_resource(ds):
    row = ds.staff_roster.iloc[0]
    roster = ds.staff_roster.copy()
    roster.loc[roster["shift_id"] == row["shift_id"], "status"] = "sick"
    known_at = row["start_utc"] + pd.Timedelta(hours=2)
    roster.loc[roster["shift_id"] == row["shift_id"], "status_known_utc"] = known_at
    short = type(ds)(ds.root)
    short.__dict__["staff_roster"] = roster
    before = row["start_utc"] + pd.Timedelta(hours=1)
    assert short.known_absence(row["resource_id"], before) is None
    assert short.on_shift(row["resource_id"], before)
    assert short.known_absence(row["resource_id"], known_at) == "sick"
    assert not short.on_shift(row["resource_id"], known_at)


def test_final_cancellation_status_does_not_remove_a_booking_from_replay(ds):
    row = ds.bookings.iloc[0]
    bookings = ds.bookings.copy()
    bookings.loc[bookings["booking_id"] == row["booking_id"], "status"] = "cancelled"
    short = type(ds)(ds.root)
    short.__dict__["bookings"] = bookings
    assert row["booking_id"] in set(short.bookings_expected["booking_id"])


def test_overlapping_bookings_are_uncertain_only_after_both_are_known(ds):
    original = ds.bookings.iloc[0]
    at = max(ds.start, original["created_utc"])
    bookings = pd.concat([ds.bookings.iloc[[0]]] * 2, ignore_index=True)
    bookings.loc[1, "booking_id"] = "BK_TEST_REPLACEMENT"
    bookings.loc[1, "created_utc"] = at + pd.Timedelta(hours=1)
    bookings.loc[1, "status"] = "confirmed"
    bookings.loc[0, "status"] = "cancelled"
    short = type(ds)(ds.root)
    short.__dict__["bookings"] = bookings
    replay = _Replay(short)
    day = original["arrival_utc"].tz_convert(ds.local_tz).date()
    assert replay.need_for(original["room_id"], day, at)[1] == original["booking_id"]
    assert replay.need_for(original["room_id"], day, at + pd.Timedelta(hours=1)) == (
        None,
        None,
    )
    assert replay.free_clean_room(day, at + pd.Timedelta(hours=1)) != original["room_id"]


def test_water_flow_before_cutoff_is_unchanged_by_later_meter_reset():
    times = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    meter = pd.Series([1000.0, 1060.0, 1000.0], index=times)
    assert interval_flow(meter).iloc[1] == interval_flow(meter.iloc[:2]).iloc[1] == 60.0
