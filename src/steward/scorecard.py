"""Score a decisions file against outcomes the replay itself recorded.

There is no ground truth in the data, but the replay does record things that went wrong:
a guest waited for a room, a container was full, a guest complained about the cold, the
heat pump reported a fault, the BMS raised a night-flow alarm. For each such outcome we ask
whether one of our decisions named the same asset beforehand, and how long before.

This measures *being early on recorded outcomes*. It cannot measure incidents that left no
trace in the replay, and a decision without a matching outcome is not automatically a false
alarm (the leak may be real but never logged) - read those by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from steward.contract import Decision
from steward.dataset import Dataset

HP_NORMAL = {"running", "standby", "defrost"}


@dataclass(frozen=True)
class Probe:
    key: str
    label: str
    issue_type: str
    lookback: pd.Timedelta  # how long before the outcome a decision still counts
    grace: pd.Timedelta  # how long after it still counts (0 = must be strictly earlier)


PROBES = (
    Probe(
        "guest_waited",
        "Guest arrived to a room that was not ready",
        "room_turnaround",
        pd.Timedelta(hours=36),
        pd.Timedelta(0),
    ),
    Probe(
        "container_full",
        "Waste container reached 100 %",
        "logistics",
        pd.Timedelta(hours=96),
        pd.Timedelta(0),
    ),
    Probe(
        "vending_empty",
        "Vending machine ran out of a product",
        "logistics",
        pd.Timedelta(hours=96),
        pd.Timedelta(0),
    ),
    Probe(
        "cold_complaint",
        "Guest reported a cold room",
        "heating_comfort",
        pd.Timedelta(hours=48),
        pd.Timedelta(0),
    ),
    Probe(
        "heat_pump_fault",
        "Heat pump reported a fault status",
        "heating_comfort",
        pd.Timedelta(hours=48),
        pd.Timedelta(hours=2),
    ),
    Probe(
        "night_flow_alarm",
        "BMS night-flow alarm (itself delayed and unreliable)",
        "water_leak",
        pd.Timedelta(days=14),
        pd.Timedelta(0),
    ),
    Probe(
        "water_complaint",
        "Guest reported a water issue",
        "water_leak",
        pd.Timedelta(days=14),
        pd.Timedelta(0),
    ),
)


def _outcomes(ds: Dataset) -> dict[str, list[tuple[pd.Timestamp, str | None]]]:
    """Recorded outcomes as (time, asset or None when building-wide)."""
    ev, gr = ds.events, ds.guest_requests
    out: dict[str, list] = {}
    waits = ev[ev["type"] == "arrival_waiting_room"]
    out["guest_waited"] = list(zip(waits["timestamp_utc"], waits["room_id"], strict=True))
    full = ev[(ev["type"] == "waste_bin_full")]
    out["container_full"] = list(zip(full["timestamp_utc"], full["asset_id"], strict=True))
    empty = ev[ev["type"] == "vending_out_of_stock"]
    out["vending_empty"] = list(zip(empty["timestamp_utc"], empty["asset_id"], strict=True))
    cold = gr[gr["request_type"] == "temperature_cold"]
    out["cold_complaint"] = [(t, None) for t in cold["timestamp_utc"]]
    wet = gr[gr["request_type"] == "water_issue"]
    out["water_complaint"] = list(zip(wet["timestamp_utc"], wet["room_id"], strict=True))
    night = ev[
        (ev["type"] == "bms_alarm")
        & (ev["alarm_code"] == "WATER_NIGHT_FLOW")
        & (ev["state"] == "raised")
    ]
    # The BMS raises this on the main meter, whichever room is leaking: match on time only.
    out["night_flow_alarm"] = [(t, None) for t in night["timestamp_utc"]]

    hp = ds.equipment_wide("HP1")
    faults: list = []
    if "status" in hp:
        bad = hp["status"].notna() & ~hp["status"].isin(HP_NORMAL)
        starts = bad & ~bad.shift(fill_value=False)
        last = None
        for t in hp.index[starts]:
            if last is None or t - last > pd.Timedelta(hours=24):  # one outcome per fault day
                faults.append((t, None))
            last = t
    out["heat_pump_fault"] = faults
    return out


def score(ds: Dataset, decisions: list[Decision]) -> dict:
    frame = pd.DataFrame(
        {
            "t": [pd.Timestamp(d.timestamp_utc) for d in decisions],
            "issue": [d.issue_type for d in decisions],
            "assets": [set(d.affected_assets) for d in decisions],
            "episode": [d.episode_id or d.event_id for d in decisions],
        }
    )
    outcomes = _outcomes(ds)
    matched_episodes: set[str] = set()
    matched_labels: dict[str, str] = {}  # episode -> first recorded outcome it preceded
    rows = []
    for probe in PROBES:
        ours = frame[frame["issue"] == probe.issue_type] if len(frame) else frame
        caught, leads = 0, []
        for when, asset in outcomes[probe.key]:
            hit = (
                ours[
                    (ours["t"] >= when - probe.lookback)
                    & (
                        (ours["t"] < when)
                        | (ours["t"] <= when + probe.grace) & (probe.grace > pd.Timedelta(0))
                    )
                ]
                if len(ours)
                else ours
            )
            if asset is not None and len(hit):
                hit = hit[hit["assets"].map(lambda s, a=asset: a in s)]
            if len(hit):
                caught += 1
                leads.append((when - hit["t"].min()) / pd.Timedelta(hours=1))
                matched_episodes.update(hit["episode"])
                for ep in hit["episode"]:
                    matched_labels.setdefault(ep, probe.label)
        total = len(outcomes[probe.key])
        rows.append(
            {
                "key": probe.key,
                "label": probe.label,
                "issue_type": probe.issue_type,
                "recorded": total,
                "flagged_before": caught,
                "median_lead_h": round(float(pd.Series(leads).median()), 1) if leads else None,
            }
        )

    weeks = max(1, round((ds.end - ds.start) / pd.Timedelta(days=7)))
    by_issue = []
    if len(frame):
        for issue, g in frame.groupby("issue"):
            episodes = set(g["episode"])
            by_issue.append(
                {
                    "issue_type": issue,
                    "decisions": len(g),
                    "incidents": len(episodes),
                    "incidents_with_recorded_outcome": len(episodes & matched_episodes),
                    "incidents_per_week": round(len(episodes) / weeks, 2),
                }
            )
    return {
        "outcomes": rows,
        "by_issue": by_issue,
        "weeks": weeks,
        "matched": matched_labels,
        "caveat": (
            "Outcomes are what the replay happened to record; they are not ground truth. "
            "An incident without a recorded outcome is not necessarily a false alarm."
        ),
    }


def render(card: dict) -> str:
    lines = [
        "Recorded outcome                                        recorded  flagged before  median lead"
    ]
    for r in card["outcomes"]:
        lead = f"{r['median_lead_h']} h" if r["median_lead_h"] is not None else "-"
        lines.append(
            f"  {r['label']:<52} {r['recorded']:>8}  {r['flagged_before']:>14}  {lead:>11}"
        )
    lines.append(f"Our incidents over {card['weeks']} weeks")
    for r in card["by_issue"]:
        lines.append(
            f"  {r['issue_type']:<24} {r['incidents']:>4} incidents ({r['incidents_per_week']}/week), "
            f"{r['decisions']} decisions, {r['incidents_with_recorded_outcome']} matched a recorded outcome"
        )
    lines.append(card["caveat"])
    return "\n".join(lines)
