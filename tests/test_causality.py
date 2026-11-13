"""No detector may use information from the future.

We run each detector on the full quickstart data and again on a copy that ends at a cut-off
(everything not yet known at the cut-off removed or reset). Every decision taken before the
cut-off must be identical in both runs.
"""

import pandas as pd
import pytest

from steward import audit
from steward.detectors import DETECTORS

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


@pytest.mark.parametrize("name", ["room_turnaround", "water_leak", "heating_comfort", "logistics"])
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
