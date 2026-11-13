"""Causal AHU health and gateway monitoring for S04."""

from __future__ import annotations

import pandas as pd

from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "ventilation_maintenance"
AHU = "AHU1"


def _open_service(ds: Dataset, at: pd.Timestamp) -> bool:
    wo = ds.work_orders
    known = wo[(wo["asset_id"] == AHU) & (wo["created_utc"] <= at) & (wo["status"] != "postponed")]
    return bool((known["completed_utc"].isna() | (known["completed_utc"] > at)).any())


def _ahu(ds: Dataset) -> list[Decision]:
    wide = ds.equipment_wide(AHU).sort_index()
    if not {"filter_dp_pa", "fan_kw", "airflow_m3h"} <= set(wide):
        return []
    dp, fan, flow = (wide[c] for c in ("filter_dp_pa", "fan_kw", "airflow_m3h"))
    valid = dp.between(20, 600) & fan.between(0, 12) & flow.between(100, 6000)
    strained = valid & dp.ge(180) & (fan.ge(3.8) | flow.le(3900))
    sustained = strained.rolling(16, min_periods=16).sum().ge(12)
    result: list[Decision] = []
    healthy = (dp.lt(130) & flow.gt(3900)).rolling(32, min_periods=32).sum().ge(28)
    onset = sustained & ~sustained.shift(fill_value=False)
    armed = True
    last_at = ds.start - pd.Timedelta(days=2)
    last_pressure = 0.0
    last_action = None
    for at in wide.index:
        if healthy.at[at]:
            armed = True
        worsened = at - last_at >= pd.Timedelta(days=2) and dp.at[at] >= last_pressure + 60
        if not onset.at[at] or not (armed or worsened):
            continue
        tech = "TECH_1" if ds.on_shift("TECH_1", at) else None
        if not armed and not (last_action == "dispatch_technician" and tech is None):
            continue  # a worsening trend alone does not justify a repeat service request
        armed = False
        if _open_service(ds, at):
            continue
        last_at, last_pressure = at, float(dp.at[at])
        episode = f"{ISSUE}:{AHU}:{at:%Y%m%dT%H%M}"
        flag = make_event_id(ISSUE, AHU, at, "flag_anomaly")
        evidence = [
            f"equipment.csv:{AHU}:filter_dp_pa={dp.at[at]:.0f}, fan_kw={fan.at[at]:.1f}, airflow_m3h={flow.at[at]:.0f}@{utc_z(at)}",
            "equipment.csv:high filter pressure with fan strain or reduced airflow in at least 12 of the last 16 samples",
        ]
        recent = ds.room_telemetry[
            ds.room_telemetry["timestamp_utc"].between(at - pd.Timedelta(hours=1), at)
            & ds.room_telemetry["co2_ppm"].between(300, 5000)
        ][["timestamp_utc", "room_id", "co2_ppm"]]
        if not recent.empty:
            occupied = (
                ds.add_occupancy(recent)
                .sort_values("timestamp_utc")
                .drop_duplicates("room_id", keep="last")
            )
            high_co2 = occupied[occupied["occupied"] & occupied["co2_ppm"].ge(1200)]
        else:
            high_co2 = recent
        impact = len(high_co2) >= 3
        if impact:
            evidence.append(
                f"room_telemetry.csv:{len(high_co2)} occupied rooms have valid CO2 >= 1200 ppm within the last hour"
            )
        result.append(
            Decision(
                event_id=flag,
                episode_id=episode,
                timestamp_utc=at,
                affected_assets=[AHU],
                issue_type=ISSUE,
                action="flag_anomaly",
                priority="critical" if impact else "high",
                reason="Sustained AHU filter pressure and fan/airflow evidence suggest degradation; a single alarm or missing room sensor was not used as proof."
                + (" Several occupied rooms also have elevated CO2." if impact else ""),
                requires_human_approval=False,
                evidence=evidence,
                expected_outcome="Operations can investigate the suspected filter problem; no physical change is claimed.",
                verification_plan="Confirm filter pressure and airflow on valid readings; escalate if occupied-room air quality worsens.",
                confidence=0.8,
            )
        )
        action = "dispatch_technician" if tech else "order_service"
        last_action = action
        result.append(
            Decision(
                event_id=make_event_id(ISSUE, AHU, at, action),
                episode_id=episode,
                timestamp_utc=at,
                affected_assets=[AHU] + ([tech] if tech else []),
                issue_type=ISSUE,
                action=action,
                priority="critical" if impact else "high",
                requires_human_approval=True,
                resource_id=tech,
                reason=(
                    "Request approval for the available in-house technician to inspect AHU1."
                    if tech
                    else "The in-house technician is unavailable; request approval for prompt external AHU service. No visit time is confirmed."
                ),
                evidence=evidence
                + [
                    f"staff_roster.csv:TECH_1 {'on shift' if tech else 'unavailable'} at {utc_z(at)}"
                ],
                dependencies=[flag],
                expected_outcome="Service is proposed and awaits approval; no visit or repair is assumed.",
                verification_plan="Approved work order and inspection, then sustained lower filter pressure and normal airflow; escalate if air quality deteriorates.",
                confidence=0.75,
            )
        )
    return result


def _gateways(ds: Dataset) -> list[Decision]:
    room_floor = ds.rooms["floor"]
    t = ds.room_telemetry[
        ["timestamp_utc", "room_id", "temperature_c", "co2_ppm", "humidity_pct"]
    ].copy()
    t["floor"] = t["room_id"].map(room_floor)
    t["missing"] = t[["temperature_c", "co2_ppm", "humidity_pct"]].isna().all(axis=1)
    counts = t.groupby(["floor", "timestamp_utc"], sort=True)["missing"].sum()
    result: list[Decision] = []
    for floor, group in counts.groupby(level=0):
        gap = group.droplevel(0).ge(8)
        sustained = gap.rolling(4, min_periods=4).sum().eq(4)
        gateway = f"GW_F{int(floor)}"
        for at in gap.index[sustained & ~sustained.shift(fill_value=False)]:
            episode = f"{ISSUE}:{gateway}:{at:%Y%m%dT%H%M}"
            flag = make_event_id(ISSUE, gateway, at, "flag_anomaly")
            evidence = [
                f"room_telemetry.csv:{gateway}:at least 8 of 10 rooms lost temperature, CO2 and humidity for four readings ending {utc_z(at)}"
            ]
            result.append(
                Decision(
                    event_id=flag,
                    episode_id=episode,
                    timestamp_utc=at,
                    affected_assets=[gateway],
                    issue_type=ISSUE,
                    action="flag_anomaly",
                    priority="medium",
                    requires_human_approval=False,
                    reason=f"Floor {int(floor)} sensors disappeared together, consistent with a gateway/data-path fault. Missing readings do not establish AHU degradation or safe air quality.",
                    evidence=evidence,
                    expected_outcome="The floor-wide blind spot is visible; repair is not assumed.",
                    verification_plan="Room readings resume; inspect gateway power/network if the gap persists and check air quality independently if needed.",
                    confidence=0.85,
                )
            )
            result.append(
                Decision(
                    event_id=make_event_id(ISSUE, gateway, at, "request_inspection"),
                    episode_id=episode,
                    timestamp_utc=at,
                    affected_assets=[gateway],
                    issue_type=ISSUE,
                    action="request_inspection",
                    priority="medium",
                    requires_human_approval=False,
                    reason=f"Inspect {gateway}; do not order AHU filter service from missing room data alone.",
                    evidence=evidence,
                    dependencies=[flag],
                    expected_outcome="Inspection requested, not confirmed completed.",
                    verification_plan="Gateway readings return or an inspection confirms the fault; escalate if the outage continues.",
                    confidence=0.8,
                )
            )
    return result


def detect(ds: Dataset) -> list[Decision]:
    return sorted(_ahu(ds) + _gateways(ds), key=lambda d: (d.timestamp_utc, d.event_id))
