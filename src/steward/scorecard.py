"""Score a decisions file against outcomes the replay itself recorded.

There is no ground truth in the data, but the replay does record things that went wrong:
a guest waited for a room, a container was full, a guest complained about the cold, the
heat pump reported a fault, the BMS raised a night-flow alarm. For each such outcome we ask
whether one of our decisions named the same asset beforehand, and how long before.

This measures *being early on recorded outcomes*. It cannot measure incidents that left no
trace in the replay, and a decision without a matching outcome is not automatically a false
alarm (the leak may be real but never logged) - read those by hand.

An outcome that was not flagged beforehand is not always a miss. Each one is put in exactly
one of four groups, in this order: flagged beforehand; examined when it happened and
dismissed with a logged reason (the look-alike log); acted on only after it happened; not
seen at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from steward.contract import Decision
from steward.dataset import Dataset

HP_NORMAL = {"running", "standby", "defrost"}
DISMISS_WINDOW = pd.Timedelta(hours=2)  # a dismissal this soon after the outcome is about it
RESPONSE_WINDOW = pd.Timedelta(hours=6)  # a decision this soon after the outcome reacted to it


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


def _outcomes(ds: Dataset) -> dict[str, list[tuple]]:
    """Recorded outcomes as (time, asset or None when building-wide, record id or None)."""
    ev, gr = ds.events, ds.guest_requests
    out: dict[str, list] = {}
    waits = ev[ev["type"] == "arrival_waiting_room"]
    out["guest_waited"] = [
        (t, a, None) for t, a in zip(waits["timestamp_utc"], waits["room_id"], strict=True)
    ]
    full = ev[(ev["type"] == "waste_bin_full")]
    out["container_full"] = [
        (t, a, None) for t, a in zip(full["timestamp_utc"], full["asset_id"], strict=True)
    ]
    empty = ev[ev["type"] == "vending_out_of_stock"]
    out["vending_empty"] = [
        (t, a, None) for t, a in zip(empty["timestamp_utc"], empty["asset_id"], strict=True)
    ]
    cold = gr[gr["request_type"] == "temperature_cold"]
    out["cold_complaint"] = [
        (t, None, r) for t, r in zip(cold["timestamp_utc"], cold["request_id"], strict=True)
    ]
    wet = gr[gr["request_type"] == "water_issue"]
    out["water_complaint"] = list(
        zip(wet["timestamp_utc"], wet["room_id"], wet["request_id"], strict=True)
    )
    night = ev[
        (ev["type"] == "bms_alarm")
        & (ev["alarm_code"] == "WATER_NIGHT_FLOW")
        & (ev["state"] == "raised")
    ]
    # The BMS raises this on the main meter, whichever room is leaking: match on time only.
    out["night_flow_alarm"] = [(t, None, None) for t in night["timestamp_utc"]]

    hp = ds.equipment_wide("HP1")
    faults: list = []
    if "status" in hp:
        bad = hp["status"].notna() & ~hp["status"].isin(HP_NORMAL)
        starts = bad & ~bad.shift(fill_value=False)
        last = None
        for t in hp.index[starts]:
            if last is None or t - last > pd.Timedelta(hours=24):  # one outcome per fault day
                faults.append((t, None, None))
            last = t
    out["heat_pump_fault"] = faults
    return out


def _dismissal(dismissed: list[dict], issue: str, when, asset, ref) -> dict | None:
    """The look-alike record that examined this outcome, if there is one."""
    for d in dismissed:
        if d.get("issue_type") != issue:
            continue
        if ref is not None and any(str(e).endswith(f":{ref}") for e in d.get("evidence") or []):
            return d
        # only a judgement made when the outcome happened counts; an earlier "no problem
        # expected" that was followed by the outcome is a wrong call, not an explanation
        near = pd.Timedelta(0) <= pd.Timestamp(d["timestamp_utc"]) - when <= DISMISS_WINDOW
        if ref is None and near and (asset is None or d.get("asset") == asset):
            return d
    return None


def score(ds: Dataset, decisions: list[Decision], dismissed: list[dict] | None = None) -> dict:
    dismissed = dismissed or []
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
        explained, after, example = 0, 0, None
        for when, asset, ref in outcomes[probe.key]:
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
                continue
            why = _dismissal(dismissed, probe.issue_type, when, asset, ref)
            if why is not None:
                explained += 1
                example = example or why.get("reason")
                continue
            late = (
                ours[(ours["t"] >= when) & (ours["t"] <= when + RESPONSE_WINDOW)]
                if len(ours)
                else ours
            )
            if asset is not None and len(late):
                late = late[late["assets"].map(lambda s, a=asset: a in s)]
            after += 1 if len(late) else 0
        total = len(outcomes[probe.key])
        rows.append(
            {
                "key": probe.key,
                "label": probe.label,
                "issue_type": probe.issue_type,
                "recorded": total,
                "flagged_before": caught,
                "dismissed_with_reason": explained,
                "dismissed_example": example,
                "acted_after": after,
                "not_seen": total - caught - explained - after,
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
            f"   dismissed with a reason {r['dismissed_with_reason']}, acted after {r['acted_after']},"
            f" not seen {r['not_seen']}"
        )
    lines.append(f"Our incidents over {card['weeks']} weeks")
    for r in card["by_issue"]:
        lines.append(
            f"  {r['issue_type']:<24} {r['incidents']:>4} incidents ({r['incidents_per_week']}/week), "
            f"{r['decisions']} decisions, {r['incidents_with_recorded_outcome']} matched a recorded outcome"
        )
    lines.append(card["caveat"])
    return "\n".join(lines)
