"""S02 The Hidden Water Leak (Level 1).

Method - continuous flow / minimum night flow, the standard water-loss approach:
people draw water in bursts and always return to zero; a leak never does.

Room rule (room_water.csv, cumulative meters differenced per interval):
  * a run = consecutive valid readings with flow > 0. Missing readings neither extend nor
    break a run (unknown is not zero); a real zero reading ends it.
  * UNATTENDED run (room vacant per the causal occupancy state, and no cleaning in progress)
    lasting VACANT_RUN: nobody is there to draw water -> flag, then request inspection.
  * ANY run lasting OCCUPIED_RUN: no shower, bath or cistern refill runs that long -> flag;
    inspection once it has also run OCCUPIED_INSPECT (through a night) or the guest reports it.
Main rule (water_main.csv minus the sum of all room meters):
  * the unmetered residual (common areas, irrigation, losses) is compared, in the local night
    window only, with its own trailing nightly baseline. A residual that stays above baseline
    for NIGHT_RUN flags a leak outside the rooms.

Look-alikes handled: bathtubs/long showers (end on their own -> dismissed), cleaning
(counts as attended), legionella flushing and metering work (known work orders), meter
exchanges (counter restart masked), implausible spikes and gaps (masked, never zero),
daytime common-area use and irrigation (main rule only looks at the night minimum).

All thresholds are physical arguments, not values fitted to an incident; see constants.
Everything is causal: a decision at t uses readings <= t and work orders created <= t.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from steward import audit
from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "water_leak"

# --- thresholds and where they come from -------------------------------------------------
MIN_FLOW_L = 0.05  # half the 0.1 L meter resolution: anything above is real flow
MAX_FLOW_L_PER_H = 2400.0  # ~0.67 L/s, above a DN15 room supply: reading is implausible
RESET_DROP_L = 50.0  # a cumulative meter never runs backwards: big drop = meter exchanged
MAX_BRIDGE = pd.Timedelta(hours=2)  # longer data gaps end a run: we no longer know
VACANT_RUN = pd.Timedelta(hours=2)  # 2x the ~60 min a full room cleaning takes
VACANT_INSPECT = pd.Timedelta(hours=3)  # still running an hour after the flag
OCCUPIED_RUN = pd.Timedelta(hours=6)  # far beyond any bath or shower; cistern refills in minutes
OCCUPIED_INSPECT = pd.Timedelta(hours=12)  # longer than a night's sleep: not guest behaviour
DISMISS_RUN = pd.Timedelta(hours=2)  # log shorter-lived look-alikes from this length on
EPISODE_GAP = pd.Timedelta(hours=6)  # flow resuming within this is the same leak
LOUD_L_PER_H = 60.0  # 1 L/min: damage-relevant, a tap or pipe rather than a seeping valve

NIGHT_HOURS_LOCAL = (0, 5)  # common areas idle: residual should sit at its minimum
NIGHT_RUN_INTERVALS = 8  # 2 h of consecutive night readings above baseline
NIGHT_INSPECT_INTERVALS = 16  # 4 h: persisted through most of the night window
NIGHT_EXCESS_L_PER_H = 5.0  # ~ one running cistern; well above meter noise in the residual
BASELINE_NIGHTS = 14

_ROOM_RE = re.compile(r"\bR\d{3}\b")


# ------------------------------------------------------------------------------ helpers


def interval_flow(meter: pd.Series) -> pd.Series:
    """Per-interval consumption from a cumulative meter, with artefacts masked as NaN.

    Meter restarts (exchange) and implausible jumps become NaN - unknown, not zero and not
    a leak. Tiny negative jitter is clipped to zero flow.
    """
    step_h = 0.25
    if len(meter) > 1:
        step_h = (meter.index[1] - meter.index[0]) / pd.Timedelta(hours=1)
    flow = meter.diff()
    bad = (flow < -RESET_DROP_L) | (flow > MAX_FLOW_L_PER_H * step_h)
    # A spike reading corrupts the interval before and after it.
    flow = flow.mask(bad | bad.shift(-1, fill_value=False) & (flow.abs() > RESET_DROP_L))
    return flow.clip(lower=0.0)


def _state_matrix(
    ds: Dataset, index: pd.DatetimeIndex, rooms: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(occupied, cleaning) boolean matrices, both causal (state after the last event <= t)."""
    grid = pd.DataFrame({"timestamp_utc": index.repeat(len(rooms)), "room_id": rooms * len(index)})
    occ = ds.add_occupancy(grid)
    occupied = occ.pivot(index="timestamp_utc", columns="room_id", values="occupied")

    ev = ds.events
    cl = ev[ev["type"].isin(["cleaning_started", "cleaning_completed"])]
    cl = cl[["timestamp_utc", "room_id", "type"]].sort_values("timestamp_utc")
    cl = cl.assign(cleaning=cl["type"].eq("cleaning_started")).rename(
        columns={"timestamp_utc": "_t"}
    )
    merged = pd.merge_asof(
        grid.sort_values("timestamp_utc"),
        cl[["_t", "room_id", "cleaning"]],
        left_on="timestamp_utc",
        right_on="_t",
        by="room_id",
    )
    merged["cleaning"] = merged["cleaning"].eq(True)
    cleaning = merged.pivot(index="timestamp_utc", columns="room_id", values="cleaning")
    return occupied.reindex(columns=rooms).astype(bool), cleaning.reindex(columns=rooms).astype(
        bool
    )


def _planned_water_work(ds: Dataset) -> pd.DataFrame:
    """Preventive/metering work orders with the rooms they name (flushing, meter exchange)."""
    wo = ds.work_orders
    wo = wo[wo["type"].isin(["preventive", "metering"])].copy()
    rows = []
    for r in wo.itertuples():
        rooms = set(_ROOM_RE.findall(str(r.summary))) | set(_ROOM_RE.findall(str(r.asset_id)))
        for room in rooms:
            rows.append((room, r.work_order_id, r.created_utc, r.scheduled_utc, str(r.summary)))
    return pd.DataFrame(rows, columns=["room_id", "wo", "created", "scheduled", "summary"])


def _work_order_covering(work: pd.DataFrame, room: str, start: pd.Timestamp, at: pd.Timestamp):
    """A planned job naming this room, known at ``at``, scheduled around the flow."""
    w = work[
        (work["room_id"] == room)
        & (work["created"] <= at)
        & (work["scheduled"] >= start - pd.Timedelta(hours=6))
        & (work["scheduled"] <= at + pd.Timedelta(hours=1))
    ]
    return None if w.empty else w.iloc[0]


def _rate(litres: float, span: pd.Timedelta) -> float:
    hours = max(span / pd.Timedelta(hours=1), 0.25)
    return litres / hours


# ---------------------------------------------------------------------------- room rule


def _room_decisions(ds: Dataset) -> list[Decision]:
    rw = ds.room_water.drop_duplicates(["timestamp_utc", "room_id"], keep="last")
    meters = rw.pivot(index="timestamp_utc", columns="room_id", values="meter_total_l").sort_index()
    rooms = [r for r in ds.room_ids if r in meters.columns]
    occupied, cleaning = _state_matrix(ds, meters.index, rooms)
    work = _planned_water_work(ds)
    requests = ds.guest_requests
    requests = requests[requests["request_type"] == "water_issue"]
    step = meters.index[1] - meters.index[0]
    decisions: list[Decision] = []

    for room in rooms:
        raw = meters[room]
        flow = interval_flow(raw)
        resets = raw.diff() < -RESET_DROP_L
        for at in resets[resets].index:
            known = work[(work["room_id"] == room) & (work["created"] <= at)]
            audit.dismiss(
                ISSUE,
                room,
                at,
                "Meter counter restarted: meter exchange, not consumption"
                + (f" (work order {known.iloc[-1]['wo']})" if not known.empty else ""),
                [f"room_water.csv:{room}:{utc_z(at)} meter_total_l dropped"],
            )

        valid = flow.dropna()
        if valid.empty:
            continue
        times = valid.index
        values = valid.to_numpy()
        positive = values > MIN_FLOW_L
        attended = (occupied[room] | cleaning[room]).reindex(times).to_numpy()
        occ = occupied[room].reindex(times).to_numpy()
        gap_break = np.r_[False, (times[1:] - times[:-1]) > MAX_BRIDGE]

        episode: dict | None = None
        run_start = None  # index into times of the first positive reading of the current run
        unattended_since = None
        run_litres = 0.0

        def close_run(end_i: int, episode, room=room, times=times):
            """Called when a run ends at ``end_i`` (exclusive). Log look-alikes that went away."""
            nonlocal run_start, unattended_since, run_litres
            if run_start is not None and (episode is None or episode["last"] < times[run_start]):
                start, end = times[run_start] - step, times[end_i - 1]
                if end - start >= DISMISS_RUN:
                    tub = bool(ds.rooms.loc[room, "has_bathtub"])
                    audit.dismiss(
                        ISSUE,
                        room,
                        end,
                        f"Flow for {(end - start) / pd.Timedelta(hours=1):.1f} h "
                        f"(~{_rate(run_litres, end - start):.0f} L/h) returned to zero on its own: "
                        "use, not a leak" + (" (room has a bathtub)" if tub else ""),
                        [f"room_water.csv:{room}:{utc_z(start)}..{utc_z(end)}"],
                    )
            run_start, unattended_since, run_litres = None, None, 0.0

        for i in range(len(times)):
            if not positive[i] or gap_break[i]:
                if run_start is not None:
                    close_run(i, episode)
                if not positive[i]:
                    continue
            if run_start is None:
                run_start = i
            run_litres += values[i]
            at = times[i]
            start = times[run_start] - step
            if attended[i]:
                unattended_since = None
            elif unattended_since is None:
                unattended_since = max(start, times[i] - step)

            duration = at - start
            unattended = (
                (at - unattended_since) if unattended_since is not None else pd.Timedelta(0)
            )
            same_run = episode is not None and start <= episode["last"]
            if same_run:
                episode["last"] = at

            trigger = unattended >= VACANT_RUN or duration >= OCCUPIED_RUN
            if not trigger:
                continue
            if episode is not None and not same_run:
                if start - episode["last"] <= EPISODE_GAP:
                    episode["last"] = at  # flow resumed shortly after: same leak
                else:
                    episode = None

            rate = _rate(run_litres, duration)
            recent = values[max(run_start, i - 7) : i + 1]
            rate_now = float(np.mean(recent)) / (step / pd.Timedelta(hours=1))
            loud = rate_now >= LOUD_L_PER_H
            evidence = [
                f"room_water.csv:{room}:{utc_z(start)}..{utc_z(at)} continuous flow, "
                f"mean {rate:.1f} L/h, last 2 h {rate_now:.1f} L/h, never returned to zero",
                f"events.jsonl:{room} occupancy at decision time = "
                f"{'occupied' if occ[i] else 'vacant'}",
            ]

            if episode is None:
                # ---- stage 1: flag (autonomous)
                if not occ[i]:
                    planned = _work_order_covering(work, room, start, at)
                    if planned is not None:
                        audit.dismiss(
                            ISSUE,
                            room,
                            at,
                            f"Flow in vacant room explained by planned work {planned['wo']}: "
                            f"{planned['summary']}",
                            [f"work_orders.csv:{planned['wo']}", evidence[0]],
                        )
                        episode = {"id": None, "last": at, "stage": 9, "start": start}
                        continue
                episode = {
                    "id": f"{ISSUE}:{room}:{start.strftime('%Y%m%dT%H%M')}",
                    "last": at,
                    "stage": 1,
                    "start": start,
                    "inspect_occupied": False,
                    "flag_event": make_event_id(ISSUE, room, at, "flag_anomaly"),
                }
                bms = _bms_night_flow(ds, start, at)
                decisions.append(
                    Decision(
                        event_id=make_event_id(ISSUE, room, at, "flag_anomaly"),
                        episode_id=episode["id"],
                        timestamp_utc=at,
                        affected_assets=[room, ds.rooms.loc[room, "water_meter_id"]],
                        issue_type=ISSUE,
                        action="flag_anomaly",
                        priority="high" if loud else "medium",
                        reason=(
                            f"Water has run without pause in {room} for "
                            f"{duration / pd.Timedelta(hours=1):.1f} h at ~{rate_now:.0f} L/h"
                            + (
                                " while the room is vacant and not being cleaned"
                                if not occ[i]
                                else ", longer than any bath, shower or cistern refill"
                            )
                            + ". Suspected "
                            + (
                                "open tap or burst fitting."
                                if loud
                                else "running toilet / seeping valve."
                            )
                        ),
                        requires_human_approval=False,
                        evidence=evidence + bms,
                        expected_outcome=(
                            "Anomaly logged and watched; no actuation. Simulated estimate of loss "
                            f"if it continues: ~{rate_now * 24:.0f} L/day."
                        ),
                        verification_plan=(
                            f"Leak confirmed if {room} flow still never returns to zero over the next "
                            "hours; cleared if a zero-flow interval appears. Escalates to an "
                            "inspection request if it persists."
                        ),
                        confidence=0.85 if not occ[i] else 0.6,
                    )
                )
                continue

            if episode["stage"] == 9:  # explained by planned work; stay quiet
                continue

            # ---- stage 2: inspection request (approval; consent if occupied)
            reported = requests[
                (requests["room_id"] == room)
                & (requests["timestamp_utc"] <= at)
                & (requests["timestamp_utc"] >= episode["start"])
            ]
            total = at - episode["start"]
            want_inspection = (
                unattended >= VACANT_INSPECT if not occ[i] else total >= OCCUPIED_INSPECT
            ) or not reported.empty
            if episode["stage"] == 1 and want_inspection:
                episode["stage"] = 2
                episode["inspect_occupied"] = bool(occ[i])
                decisions.append(
                    _inspection(
                        ds, room, at, episode, bool(occ[i]), loud, rate_now, evidence, reported
                    )
                )
            elif episode["stage"] == 2 and episode["inspect_occupied"] and not occ[i]:
                # ---- stage 3: guest has left, leak still running -> consent no longer needed
                episode["stage"] = 3
                decisions.append(
                    _inspection(
                        ds, room, at, episode, False, loud, rate_now, evidence, reported, True
                    )
                )
        if run_start is not None:
            close_run(len(times), episode)
    return decisions


def _inspection(
    ds, room, at, episode, occupied, loud, rate_now, evidence, reported, vacated=False
) -> Decision:
    hours = (at - episode["start"]) / pd.Timedelta(hours=1)
    request_ids = [str(x) for x in reported["request_id"]] if not reported.empty else None
    if request_ids:
        evidence = evidence + [f"guest_requests.csv:{rid} water_issue" for rid in request_ids]
    if vacated:
        reason = (
            f"{room} is now vacant and the flow (~{rate_now:.0f} L/h, {hours:.0f} h so far) continues: "
            "inspect now, no entry into an occupied room needed."
        )
    elif occupied:
        reason = (
            f"Continuous flow in occupied {room} for {hours:.0f} h (~{rate_now:.0f} L/h)"
            + (", also reported by the guest" if request_ids else "")
            + ". Inspection needs staff approval and the guest's consent to enter."
        )
    else:
        reason = (
            f"Continuous flow in vacant {room} for {hours:.1f} h (~{rate_now:.0f} L/h) persists "
            "after the flag. Send someone to inspect; no shutoff without authorisation."
        )
    return Decision(
        event_id=make_event_id(ISSUE, room, at, "request_inspection"),
        episode_id=episode["id"],
        timestamp_utc=at,
        affected_assets=[room, ds.rooms.loc[room, "water_meter_id"]],
        issue_type=ISSUE,
        action="request_inspection",
        priority="critical" if loud else "medium",
        reason=reason,
        requires_human_approval=True,
        requires_guest_consent=occupied,
        evidence=evidence,
        guest_request_ids=request_ids,
        dependencies=[episode["flag_event"]],
        expected_outcome=(
            "Inspector confirms or rules out a leak (e.g. toilet fill valve, tap, fitting). "
            "No water shutoff and no room entry happen autonomously."
        ),
        verification_plan=(
            f"After an authorised repair, {room} shows zero-flow intervals again; escalate if "
            "the rate rises or the flow is still continuous 24 h after inspection."
        ),
        confidence=0.9 if (not occupied or request_ids) else 0.8,
    )


def _bms_night_flow(ds: Dataset, start: pd.Timestamp, at: pd.Timestamp) -> list[str]:
    ev = ds.events
    hit = ev[
        (ev["type"] == "bms_alarm")
        & (ev["alarm_code"] == "WATER_NIGHT_FLOW")
        & (ev["state"] == "raised")
        & (ev["timestamp_utc"] >= start)
        & (ev["timestamp_utc"] <= at)
    ]
    return [
        f"events.jsonl:{r.event_id} bms_alarm WATER_NIGHT_FLOW (clue only)"
        for r in hit.itertuples()
    ]


# ---------------------------------------------------------------------------- main rule


def _main_decisions(ds: Dataset) -> list[Decision]:
    rw = ds.room_water.drop_duplicates(["timestamp_utc", "room_id"], keep="last")
    meters = rw.pivot(index="timestamp_utc", columns="room_id", values="meter_total_l").sort_index()
    main = ds.water_main.drop_duplicates("timestamp_utc", keep="last").set_index("timestamp_utc")
    main_raw = main["meter_total_l"].sort_index().reindex(meters.index)

    # Raw differences (not clipped) so meter jitter cancels instead of biasing the residual;
    # any room without a valid reading makes the residual unknown for that interval.
    room_diff = meters.diff()
    room_diff = room_diff.mask(room_diff < -RESET_DROP_L)
    main_diff = main_raw.diff()
    main_diff = main_diff.mask(main_diff < -RESET_DROP_L)
    step_h = (meters.index[1] - meters.index[0]) / pd.Timedelta(hours=1)
    residual = (main_diff - room_diff.sum(axis=1, skipna=False)) / step_h  # L/h

    local = residual.index.tz_convert(ds.local_tz)
    night = (local.hour >= NIGHT_HOURS_LOCAL[0]) & (local.hour < NIGHT_HOURS_LOCAL[1])
    night_date = pd.Series(local.date, index=residual.index)

    decisions: list[Decision] = []
    history: list[float] = []  # median night residual of past normal nights
    episode: dict | None = None

    for _date, idx in residual[night].groupby(night_date[night]).groups.items():
        vals = residual.loc[idx].dropna()
        if len(vals) < NIGHT_RUN_INTERVALS:
            continue  # too little data this night to say anything
        baseline = float(np.median(history[-BASELINE_NIGHTS:])) if len(history) >= 3 else 0.0
        baseline = max(baseline, 0.0)
        limit = baseline + NIGHT_EXCESS_L_PER_H
        streak, fired_tonight = 0, False
        for at, v in vals.items():
            streak = streak + 1 if v > limit else 0
            if streak < NIGHT_RUN_INTERVALS:
                continue
            fired_tonight = True
            window = vals.loc[:at].iloc[-streak:]
            excess = float(window.mean() - baseline)
            evidence = [
                f"water_main.csv:{utc_z(window.index[0])}..{utc_z(at)} main minus sum of 40 room "
                f"meters = {window.mean():.1f} L/h (min {window.min():.1f}) in the night window",
                f"trailing night baseline {baseline:.1f} L/h over {min(len(history), BASELINE_NIGHTS)} nights",
            ]
            if episode is None:
                episode = {
                    "id": f"{ISSUE}:WM_MAIN:{window.index[0].strftime('%Y%m%dT%H%M')}",
                    "stage": 1,
                    "nights": 0,
                }
                decisions.append(
                    Decision(
                        event_id=make_event_id(ISSUE, "WM_MAIN", at, "flag_anomaly"),
                        episode_id=episode["id"],
                        timestamp_utc=at,
                        affected_assets=["WM_MAIN"],
                        issue_type=ISSUE,
                        action="flag_anomaly",
                        priority="medium",
                        reason=(
                            f"Unmetered night flow: the main meter exceeds the sum of all room meters "
                            f"by ~{excess:.0f} L/h above its usual night level for "
                            f"{streak * step_h:.0f} h. No room meter shows it - suspect a leak in "
                            "common pipework (risers, technical room, irrigation)."
                        ),
                        requires_human_approval=False,
                        evidence=evidence + _bms_night_flow(ds, window.index[0], at),
                        expected_outcome=(
                            "Anomaly logged; no actuation. Simulated estimate of loss if it "
                            f"continues: ~{excess * 24:.0f} L/day."
                        ),
                        verification_plan=(
                            "Confirmed if the night residual stays above baseline until morning and "
                            "the next night; cleared if it falls back to baseline."
                        ),
                        confidence=0.6,
                    )
                )
            elif episode["stage"] == 1 and (
                streak >= NIGHT_INSPECT_INTERVALS or episode["nights"] >= 1
            ):
                episode["stage"] = 2
                decisions.append(
                    Decision(
                        event_id=make_event_id(ISSUE, "WM_MAIN", at, "request_inspection"),
                        episode_id=episode["id"],
                        timestamp_utc=at,
                        affected_assets=["WM_MAIN"],
                        issue_type=ISSUE,
                        action="request_inspection",
                        priority="high",
                        reason=(
                            f"Unmetered flow of ~{excess:.0f} L/h above baseline has persisted "
                            f"{streak * step_h:.0f} h tonight (night {episode['nights'] + 1} of this episode) "
                            "with all rooms accounted for. "
                            "Request inspection of common pipework and technical room; no shutoff "
                            "without authorisation."
                        ),
                        requires_human_approval=True,
                        requires_guest_consent=False,
                        evidence=evidence,
                        expected_outcome="Technician or plumber locates the leak outside the guest rooms.",
                        verification_plan=(
                            "After repair the night residual (main minus rooms, 00-05 local) returns "
                            "to its baseline; escalate if the residual grows."
                        ),
                        confidence=0.8,
                    )
                )
        if fired_tonight:
            if episode is not None:
                episode["nights"] += 1
        else:
            if episode is not None:
                episode = None
            night_median = float(vals.median())
            if night_median > limit:
                # Elevated on average but not continuously: bursts of use, not a leak.
                audit.dismiss(
                    ISSUE,
                    "WM_MAIN",
                    vals.index[-1],
                    f"Night residual median {night_median:.1f} L/h above baseline {baseline:.1f} "
                    "but it returned to baseline in between: intermittent common-area use",
                    [f"water_main.csv:{utc_z(vals.index[0])}..{utc_z(vals.index[-1])}"],
                )
            else:
                history.append(night_median)
    return decisions


def detect(ds: Dataset) -> list[Decision]:
    return _room_decisions(ds) + _main_decisions(ds)
