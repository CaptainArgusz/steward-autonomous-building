import json

import numpy as np
import pandas as pd

from steward import audit
from steward.detectors import water_leak
from steward.detectors.water_leak import interval_flow


def _meter(values):
    idx = pd.date_range("2026-03-01", periods=len(values), freq="15min", tz="UTC")
    return pd.Series(values, index=idx, dtype="float64")


def test_meter_exchange_is_masked_not_counted_as_flow():
    flow = interval_flow(_meter([70000.0, 70001.0, 12.0, 12.5, 13.0]))
    assert np.isnan(flow.iloc[2])  # counter restart: unknown, neither zero nor a leak
    assert flow.iloc[1] == 1.0 and flow.iloc[3] == 0.5
    assert flow.max() < 5


def test_missing_reading_stays_missing_and_jitter_is_not_negative_flow():
    flow = interval_flow(_meter([100.0, np.nan, 103.0, 102.9, 103.4]))
    assert flow.iloc[1:3].isna().all()  # no reading -> no claim about that interval
    assert flow.iloc[3] == 0.0


def test_implausible_spike_is_masked():
    flow = interval_flow(_meter([100.0, 100.5, 9100.5, 101.0, 101.2]))
    assert flow.iloc[2:4].isna().all()
    assert flow.dropna().max() < 5


def test_quickstart_finds_the_leaks_once_each_and_stays_quiet_otherwise(ds):
    audit.drain()
    decisions = water_leak.detect(ds)
    dismissed = audit.drain()
    episodes = {d.episode_id for d in decisions}
    assert 1 <= len(episodes) <= 4  # 21 mostly normal days: a handful of incidents at most
    assert len(decisions) <= 3 * len(episodes)  # flag + at most two escalations per leak
    for episode in episodes:
        own = sorted(
            (d for d in decisions if d.episode_id == episode), key=lambda d: d.timestamp_utc
        )
        assert own[0].action == "flag_anomaly" and own[0].autonomy == "autonomous"
        assert all(d.action == "request_inspection" for d in own[1:])
    assert any("exchange" in r["reason"] for r in dismissed)  # WM_R403 exchange not a leak


def test_inspections_need_approval_and_consent_when_occupied(ds):
    audit.drain()
    decisions = water_leak.detect(ds)
    audit.drain()
    inspections = [d for d in decisions if d.action == "request_inspection"]
    assert inspections
    probe = pd.DataFrame(
        {
            "room_id": [d.affected_assets[0] for d in inspections],
            "timestamp_utc": [pd.Timestamp(d.timestamp_utc) for d in inspections],
        }
    )
    occupied = ds.add_occupancy(probe)["occupied"].tolist()
    for d, occ in zip(inspections, occupied, strict=True):
        assert d.requires_human_approval is True
        assert bool(d.requires_guest_consent) is occ
        json.loads(d.to_json_line())


def test_decisions_do_not_depend_on_the_future(ds, tmp_path):
    """Truncating the dataset after a decision must not change that decision."""
    audit.drain()
    full = water_leak.detect(ds)
    audit.drain()
    cut = pd.Timestamp(full[0].timestamp_utc) + pd.Timedelta(minutes=15)

    class Truncated(type(ds)):
        pass

    short = Truncated(ds.root)
    for table in ("room_water", "water_main", "events", "work_orders", "guest_requests"):
        df = getattr(ds, table)
        col = "created_utc" if table == "work_orders" else "timestamp_utc"
        short.__dict__[table] = df[df[col] <= cut]
    early = water_leak.detect(short)
    audit.drain()
    expected = [d.to_json_line() for d in full if pd.Timestamp(d.timestamp_utc) <= cut]
    assert [d.to_json_line() for d in early] == expected
