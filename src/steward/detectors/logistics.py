"""Cleaning & logistics (Level 2): waste container, floor bins, vending machine.

REFERENCE IMPLEMENTATION - a deliberately simple baseline that shows the pattern every
detector should follow: replay in time order, use only what was known at that moment,
emit ONE decision per incident (``episode_id``), cite evidence, state how to verify.

Implemented: extra pickup for the central container WASTE_MAIN when it is projected to
overflow before the next regular pickup.
Not implemented yet: floor bins WASTE_F1-F4 (robots can carry them), VEND1 restock.

Known weakness: the trailing fill rate over-predicts. On the dev year it orders 12 extra
pickups; 2 precede a cycle that really reached 100 % (of 3 such cycles), 10 are false alarms.
Project from departures already booked at decision time before trusting it.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ASSET = "WASTE_MAIN"
PICKUP_WEEKDAYS = (1, 4)  # Tue, Fri (building.json: municipal service 07:00 local)
PICKUP_HOUR_LOCAL = 7
LEAD_TIME = pd.Timedelta(hours=24)  # an extra pickup needs ~24 h notice
MIN_FILL_PCT = 60.0  # do not speculate on a half-empty container
PICKUP_DROP_PCT = 20.0  # a drop this large between readings means it was emptied


def next_regular_pickup(at: pd.Timestamp, tz) -> pd.Timestamp:
    """First scheduled municipal pickup strictly after ``at`` (UTC in, UTC out)."""
    local = at.tz_convert(tz)
    for offset in range(8):
        day = (local + timedelta(days=offset)).normalize()
        pickup = day.replace(hour=PICKUP_HOUR_LOCAL)
        if day.weekday() in PICKUP_WEEKDAYS and pickup > local:
            return pickup.tz_convert("UTC")
    raise AssertionError("unreachable: a pickup weekday occurs within any 8 days")


def detect(ds: Dataset) -> list[Decision]:
    lg = ds.logistics
    fill = (
        lg[(lg["asset_id"] == ASSET) & (lg["metric"] == "fill_pct")]
        .dropna(subset=["value"])  # missing reading = unknown, not empty
        .drop_duplicates("timestamp_utc")
        .sort_values("timestamp_utc")
    )

    decisions: list[Decision] = []
    cycle_start: pd.Timestamp | None = None
    cycle_min = prev = None
    ordered_this_cycle = False

    for at, value in zip(fill["timestamp_utc"], fill["value"], strict=True):
        if prev is None or prev - value >= PICKUP_DROP_PCT:  # first reading or just emptied
            cycle_start, cycle_min, ordered_this_cycle = at, value, False
        prev = value
        cycle_min = min(cycle_min, value)
        if ordered_this_cycle or value < MIN_FILL_PCT:
            continue

        pickup = next_regular_pickup(at, ds.local_tz)
        wait = pickup - at
        if wait <= LEAD_TIME:
            continue  # the regular pickup arrives before an extra one could

        days_in_cycle = max((at - cycle_start) / pd.Timedelta(days=1), 1.0)
        rise_per_day = (value - cycle_min) / days_in_cycle
        projected = value + rise_per_day * (wait / pd.Timedelta(days=1))
        if projected < 100.0:
            continue

        ordered_this_cycle = True
        decisions.append(
            Decision(
                event_id=make_event_id("logistics", ASSET, at, "order_service"),
                episode_id=f"logistics:{ASSET}:{cycle_start.strftime('%Y%m%d')}",
                timestamp_utc=at,
                affected_assets=[ASSET],
                issue_type="logistics",
                action="order_service",
                priority="high" if value >= 90 else "medium",
                reason=(
                    f"Central waste container at {value:.0f} % and filling ~{rise_per_day:.0f} %/day; "
                    f"projected {projected:.0f} % by the next regular pickup in {wait / pd.Timedelta(hours=1):.0f} h. "
                    "Order an extra pickup (~24 h lead time)."
                ),
                requires_human_approval=False,
                evidence=[
                    f"logistics.csv:{ASSET}:fill_pct={value:.1f}@{utc_z(at)}",
                    f"logistics.csv:{ASSET}:cycle since {utc_z(cycle_start)} (min {cycle_min:.1f} %)",
                    f"building.json:{ASSET}.pickup next regular {utc_z(pickup)}",
                ],
                expected_outcome="Simulated: extra pickup within ~24 h keeps the container below 100 %.",
                verification_plan=(
                    "A waste_pickup event and a fill_pct drop appear before the regular pickup; "
                    "escalate to a human if fill_pct exceeds 95 % before then."
                ),
                confidence=round(min(0.9, 0.5 + (projected - 100.0) / 100.0), 2),
            )
        )
    return decisions
