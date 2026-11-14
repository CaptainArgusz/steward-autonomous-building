"""Level 2: logistics (container, vending, robot floor bins) and the day-ahead battery plan."""

import numpy as np
import pandas as pd
import pytest

from steward.detectors import energy, logistics

from .test_causality import truncated


def test_service_schedules_are_read_from_building_json(ds):
    pickup = logistics._asset(ds, "WASTE_MAIN")["pickup"]
    restock = logistics._asset(ds, "VEND1")["restock"]
    assert logistics.parse_schedule(pickup, logistics.DEFAULT_RESTOCK) == (
        (1, 4),
        (7, 0),
        pd.Timedelta(hours=24),
    )
    assert logistics.parse_schedule(restock, logistics.DEFAULT_PICKUP)[:2] == ((0, 3), (10, 0))
    # unparseable text falls back to the default instead of failing
    assert logistics.parse_schedule("ask the vendor", logistics.DEFAULT_PICKUP)[:2] == (
        logistics.DEFAULT_PICKUP
    )
    assert logistics._carry_minutes(ds) == 20.0


def test_logistics_decisions_are_autonomous_and_one_per_service_cycle(ds):
    decisions = logistics.detect(ds)
    assert decisions, "quickstart should produce at least one logistics decision"
    assert all(not d.requires_human_approval and d.autonomy == "autonomous" for d in decisions)
    assert all(
        d.evidence and d.verification_plan and "Simulated" in d.expected_outcome for d in decisions
    )
    episodes = [d.episode_id for d in decisions]
    assert len(episodes) == len(set(episodes)), "a cycle must not be ordered twice"


def test_robot_carries_bins_only_on_a_known_cleaner_absence_and_when_on_shift(ds):
    robot = [d for d in logistics.detect(ds) if d.action == "dispatch_robot"]
    assert robot, "quickstart has a cleaner absence; a robot bin run is expected"
    roster = ds.staff_roster
    for d in robot:
        at = pd.Timestamp(d.timestamp_utc)
        day_end = at + pd.Timedelta(hours=18)
        absent = roster[
            (roster["role"] == "cleaner")
            & (roster["status"] != "scheduled")
            & (roster["status_known_utc"] <= at)
            & (roster["start_utc"] < day_end)
            & (roster["end_utc"] > at - pd.Timedelta(hours=2))
        ]
        assert not absent.empty
        assert ds.on_shift(d.resource_id, at) and ds.known_absence(d.resource_id, at) is None
        assert any(a.startswith("WASTE_F") for a in d.affected_assets)
        assert d.resource_id in d.affected_assets


def test_an_unknown_absence_does_not_trigger_a_robot_bin_run(ds):
    roster = ds.staff_roster.copy()
    sick = roster["status"] != "scheduled"
    roster.loc[sick, "status_known_utc"] = roster.loc[sick, "end_utc"]  # learned only afterwards
    short = type(ds)(ds.root)
    short.__dict__["staff_roster"] = roster
    assert not [d for d in logistics.detect(short) if d.action == "dispatch_robot"]


SPEC = {"lo": 10.0, "hi": 95.0, "charge_kw": 40.0, "discharge_kw": 40.0, "eta": 0.9**0.5}


def test_battery_plan_respects_limits_and_never_drains_the_battery():
    price = np.array([0.15] * 6 + [0.20] * 10 + [0.30] * 4 + [0.20] * 4)
    net = np.full(24, 30.0)
    path, cost = energy.plan_battery(price, net, 50.0, SPEC)
    assert len(path) == 25 and path[0] == 50.0
    assert path.min() >= SPEC["lo"] and path.max() <= SPEC["hi"]
    assert path[-1] >= path[0]
    step = np.diff(path)
    assert (step <= SPEC["charge_kw"] * SPEC["eta"] + 1e-9).all()
    assert (-step <= SPEC["discharge_kw"] / SPEC["eta"] + 1e-9).all()
    idle = float((price * net).sum())
    assert cost < idle  # buying cheap and using it in the peak pays despite the losses


def test_flat_prices_give_no_saving():
    price = np.full(24, 0.2)
    net = np.full(24, 20.0)
    _, cost = energy.plan_battery(price, net, 50.0, SPEC)
    assert cost == pytest.approx(float((price * net).sum()))


def test_routine_follows_its_profile_within_limits():
    price = np.full(4, 0.2)
    net = np.full(4, 10.0)
    path, cost = energy.routine_cost(price, net, 12.0, np.array([40.0, 40.0, -40.0, 0.0]), SPEC)
    assert path.min() >= SPEC["lo"] - 1e-9 and path.max() <= SPEC["hi"] + 1e-9
    assert cost > 0


@pytest.fixture
def every_day(monkeypatch):
    monkeypatch.setattr(energy, "MIN_SAVING_EUR", -1e9)
    monkeypatch.setattr(energy, "MIN_SAVING_SHARE", -1.0)


def test_energy_plans_are_autonomous_simulated_and_made_after_prices_are_published(ds, every_day):
    decisions = energy.detect(ds)
    assert decisions
    tariff = ds.tariff
    for d in decisions:
        assert d.action == "optimize_energy" and not d.requires_human_approval
        assert "Simulated" in d.expected_outcome and "comfort band" in d.reason
        at = pd.Timestamp(d.timestamp_utc)
        published = pd.Timestamp(d.evidence[0].split("published ")[1].split(":")[0] + ":00:00Z")
        assert published <= at
        assert (tariff[tariff["published_utc"] == published]["published_utc"] <= at).all()


@pytest.mark.parametrize("fraction", [0.45, 0.7])
def test_energy_plans_do_not_use_the_future(ds, every_day, fraction):
    cut = (ds.start + (ds.end - ds.start) * fraction).floor("15min")
    full = [d.to_json_line() for d in energy.detect(ds) if pd.Timestamp(d.timestamp_utc) <= cut]
    early = [
        d.to_json_line()
        for d in energy.detect(truncated(ds, cut))
        if pd.Timestamp(d.timestamp_utc) <= cut
    ]
    assert full and early == full
