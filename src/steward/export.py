"""Bundle one dataset's decisions, evidence series and insights into a JSON file for the UI.

The UI never reads raw CSVs. Each incident (episode) carries small, hourly chart series for
the assets it names, covering the days around it. Data after the decision moment is
included on purpose so a reviewer can see what happened next - the UI marks the decision
time, and nothing after that line was available to the detector.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from steward import executor
from steward.contract import Decision, utc_z, validate_file
from steward.dataset import Dataset
from steward.recommendations import evidence_for

PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PAD = pd.Timedelta(hours=48)
MAX_ROOM_CHARTS = 4


def _points(series: pd.Series) -> list[list]:
    """Hourly [iso time, value|None] pairs; gaps stay None so the chart shows them as gaps."""
    return [[utc_z(t), None if pd.isna(v) else round(float(v), 2)] for t, v in series.items()]


def _window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["timestamp_utc"] >= start) & (df["timestamp_utc"] <= end)]


def _hourly(df: pd.DataFrame, col: str, how: str = "mean") -> pd.Series:
    return df.set_index("timestamp_utc")[col].resample("1h").agg(how)


def _flow_l_per_h(df: pd.DataFrame) -> pd.Series:
    """Litres per hour from a cumulative meter; negative steps (meter exchange) become gaps."""
    total = df.drop_duplicates("timestamp_utc").set_index("timestamp_utc")["meter_total_l"]
    step = total.diff()
    return step.where(step >= 0).resample("1h").sum(min_count=1)


def _room_charts(ds: Dataset, room: str, issue: str, start, end) -> list[dict]:
    charts = []
    tel = _window(ds.room_telemetry[ds.room_telemetry["room_id"] == room], start, end)
    if issue == "water_leak":
        water = _window(ds.room_water[ds.room_water["room_id"] == room], start, end)
        charts.append(
            {
                "title": f"{room} water flow",
                "unit": "L/h",
                "series": [{"name": "Flow", "points": _points(_flow_l_per_h(water))}],
            }
        )
    if issue in ("heating_comfort", "water_leak", "room_turnaround") and not tel.empty:
        if issue == "heating_comfort":
            charts.append(
                {
                    "title": f"{room} temperature",
                    "unit": "°C",
                    "series": [
                        {"name": "Room", "points": _points(_hourly(tel, "temperature_c"))},
                        {"name": "Setpoint", "points": _points(_hourly(tel, "setpoint_c"))},
                    ],
                    "band": list(ds.comfort_band_c),
                }
            )
    occ = ds.add_occupancy(tel[["timestamp_utc", "room_id"]]) if not tel.empty else None
    if occ is not None and charts:
        spans, current = [], None
        for t, o in zip(occ["timestamp_utc"], occ["occupied"], strict=True):
            if o and current is None:
                current = t
            elif not o and current is not None:
                spans.append([utc_z(current), utc_z(t)])
                current = None
        if current is not None:
            spans.append([utc_z(current), utc_z(end)])
        for c in charts:
            c["occupied"] = spans
    return charts


def _asset_charts(ds: Dataset, asset: str, start, end) -> list[dict]:
    if asset == "HP1":
        hp = ds.equipment_wide("HP1")
        hp = hp[(hp.index >= start) & (hp.index <= end)]
        weather = _window(ds.weather, start, end)
        charts = []
        if "heat_output_kw" in hp:
            charts.append(
                {
                    "title": "Heat pump HP1 heat output",
                    "unit": "kW",
                    "series": [
                        {
                            "name": "Heat output",
                            "points": _points(hp["heat_output_kw"].resample("1h").mean()),
                        }
                    ],
                }
            )
        charts.append(
            {
                "title": "Outdoor temperature",
                "unit": "°C",
                "series": [{"name": "Outdoor", "points": _points(_hourly(weather, "outdoor_c"))}],
            }
        )
        return charts
    if asset == "AHU1":
        ahu = ds.equipment_wide("AHU1")
        ahu = ahu[(ahu.index >= start) & (ahu.index <= end)]
        return [
            {
                "title": "AHU1 filter differential pressure",
                "unit": "Pa",
                "series": [
                    {
                        "name": "Filter dp",
                        "points": _points(ahu["filter_dp_pa"].resample("1h").mean()),
                    }
                ],
            }
        ]
    if asset == "WM_MAIN":
        main = _flow_l_per_h(_window(ds.water_main, start, end))
        rooms = _window(ds.room_water, start, end)
        total = sum(_flow_l_per_h(g).reindex(main.index) for _, g in rooms.groupby("room_id"))
        return [
            {
                "title": "Main meter vs sum of room meters",
                "unit": "L/h",
                "series": [
                    {"name": "Main meter", "points": _points(main)},
                    {"name": "Sum of rooms", "points": _points(total)},
                ],
            }
        ]
    lg = ds.logistics
    rows = _window(lg[lg["asset_id"] == asset], start, end)
    charts = []
    for metric, g in rows.groupby("metric"):
        unit = "%" if metric == "fill_pct" else "units"
        charts.append(
            {
                "title": f"{asset} {metric}",
                "unit": unit,
                "series": [{"name": metric, "points": _points(_hourly(g, "value", "last"))}],
            }
        )
    return charts


_WINDOW = re.compile(r"(\d{2}):(\d{2})-(\d{2}):(\d{2}) \(~(\d+(?:\.\d+)?) kWh\)")


def _energy_charts(ds: Dataset, episode_id: str, items: list[Decision]) -> list[dict]:
    """Prices of the planned day and the planned battery power, read back from the plan text."""
    try:
        day = pd.Timestamp(episode_id.rsplit(":", 1)[-1]).date()
    except ValueError:
        return []
    tz = ds.local_tz
    start = pd.Timestamp(day).tz_localize(tz).tz_convert("UTC")
    hours = pd.date_range(start, start + pd.Timedelta(days=1), freq="1h", inclusive="left")
    tariff = ds.tariff.drop_duplicates("timestamp_utc", keep="last").set_index("timestamp_utc")
    price = tariff["price_eur_kwh"].reindex(hours)
    plan = pd.Series(0.0, index=hours)
    reason = items[-1].reason
    for part, sign in (("Charge", -1.0), ("discharge", 1.0)):
        i = reason.find(part + " ")
        if i < 0:
            continue
        chunk = reason[i : reason.find(";", i) if ";" in reason[i:] else None]
        for h0, m0, h1, m1, kwh in _WINDOW.findall(chunk):
            a = (
                pd.Timestamp(day)
                .replace(hour=int(h0), minute=int(m0))
                .tz_localize(tz)
                .tz_convert("UTC")
            )
            b_local = pd.Timestamp(day).replace(hour=int(h1), minute=int(m1))
            if (int(h1), int(m1)) <= (int(h0), int(m0)):
                b_local += pd.Timedelta(days=1)
            b = b_local.tz_localize(tz).tz_convert("UTC")
            span = (b - a) / pd.Timedelta(hours=1)
            if span > 0:
                plan[(plan.index >= a) & (plan.index < b)] = sign * float(kwh) / span
    return [
        {
            "title": "Day-ahead electricity price for the planned day",
            "unit": "EUR/kWh",
            "series": [{"name": "Price", "points": _points(price)}],
        },
        {
            "title": "Battery plan (above 0 = discharge to cover load, below 0 = charge)",
            "unit": "kW",
            "with_first": True,
            "series": [{"name": "Planned battery power", "points": _points(plan)}],
        },
    ]


def _room_events(ds: Dataset, rooms: list[str], start, end) -> list[dict]:
    ev = ds.events
    ev = ev[(ev["timestamp_utc"] >= start) & (ev["timestamp_utc"] <= end)]
    ev = ev[ev["room_id"].isin(rooms) & (ev["type"] != "bms_alarm")]
    cols = ["timestamp_utc", "type", "room_id", "booking_id", "resource_id"]
    out = []
    for row in ev[cols].sort_values("timestamp_utc").itertuples(index=False):
        item = {"t": utc_z(row.timestamp_utc), "type": row.type, "room": row.room_id}
        if isinstance(row.resource_id, str):
            item["resource"] = row.resource_id
        if isinstance(row.booking_id, str):
            item["booking"] = row.booking_id
        out.append(item)
    return out


def _time_context(ds: Dataset, d: Decision) -> list[dict]:
    """Records available when this decision was made, for the optional UI detail."""
    at = pd.Timestamp(d.timestamp_utc)
    notes = []
    evidence = " ".join(d.evidence or [])
    bookings = ds.bookings.set_index("booking_id")
    for booking_id in sorted(set(re.findall(r"bookings\.csv:(BK\d+)", evidence))):
        if booking_id in bookings.index:
            created = bookings.at[booking_id, "created_utc"]
            if created <= at:
                notes.append({"label": f"Booking {booking_id} created", "at": utc_z(created)})
    requests = ds.guest_requests.set_index("request_id")
    for request_id in d.guest_request_ids or []:
        if request_id in requests.index:
            received = requests.at[request_id, "timestamp_utc"]
            if received <= at:
                notes.append(
                    {"label": f"Guest request {request_id} received", "at": utc_z(received)}
                )
    if d.resource_id and not d.resource_id.startswith("ONCALL"):
        roster = ds.staff_roster
        shift = roster[
            (roster["resource_id"] == d.resource_id)
            & (roster["start_utc"] <= at)
            & (roster["end_utc"] > at)
        ]
        if not shift.empty:
            row = shift.iloc[0]
            state = row["status"] if row["status_known_utc"] <= at else "scheduled"
            notes.append({"label": f"{d.resource_id} shift", "value": state})
    return notes


def _episodes(ds: Dataset, decisions: list[Decision]) -> list[dict]:
    status = {row["event_id"]: row["status"] for row in executor.execute(decisions, ds)}
    groups: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        groups[d.episode_id or d.event_id].append(d)

    rooms_known = set(ds.room_ids)
    episodes = []
    for episode_id, items in groups.items():
        items.sort(key=lambda d: (d.timestamp_utc, d.event_id))
        start, end = pd.Timestamp(items[0].timestamp_utc), pd.Timestamp(items[-1].timestamp_utc)
        assets = list(dict.fromkeys(a for d in items for a in d.affected_assets))
        rooms = [a for a in assets if a in rooms_known]
        issue = items[0].issue_type
        lo, hi = max(start - PAD, ds.start), min(end + PAD, ds.end)

        charts: list[dict] = (
            _energy_charts(ds, episode_id, items) if issue == "energy_optimization" else []
        )
        for asset in assets:
            if asset not in rooms_known:
                charts += _asset_charts(ds, asset, lo, hi)
        for room in rooms[:MAX_ROOM_CHARTS]:
            charts += _room_charts(ds, room, issue, lo, hi)

        decisions_out = []
        for d in items:
            row = d.model_dump(exclude_none=True)
            row["status"] = status[d.event_id]
            row["time_context"] = _time_context(ds, d)
            decisions_out.append(row)
        episodes.append(
            {
                "id": episode_id,
                "issue_type": issue,
                "start": utc_z(start),
                "end": utc_z(end),
                "priority": min((d.priority for d in items), key=PRIORITY_RANK.get),
                "assets": assets,
                "rooms": rooms,
                "needs_approval": any(d.requires_human_approval for d in items),
                "decisions": decisions_out,
                "charts": charts,
                "events": _room_events(ds, rooms, lo, hi) if issue == "room_turnaround" else [],
            }
        )
    episodes.sort(key=lambda e: (e["start"], e["id"]))
    return episodes


def _days(ds: Dataset) -> dict:
    """Per local day: every room's state in 15-minute steps, plus arrivals, departures and staff.

    Room state codes: o occupied, d vacant and dirty, c being cleaned, r vacant and ready,
    ? no reading. Occupancy is the causal state from check-in/check-out events; the cleaning
    state is the housekeeping_state the room reported. Used by the UI's replay clock only.
    """
    tz = ds.local_tz
    t = ds.room_telemetry[["timestamp_utc", "room_id", "housekeeping_state"]].drop_duplicates(
        ["timestamp_utc", "room_id"], keep="last"
    )
    t = ds.add_occupancy(t)
    hk = t["housekeeping_state"]
    code = np.select(
        [t["occupied"], hk.eq("in_progress"), hk.eq("dirty"), hk.eq("clean")],
        ["o", "c", "d", "r"],
        default="?",
    )
    local = t["timestamp_utc"].dt.tz_convert(tz)
    frame = pd.DataFrame(
        {
            "day": local.dt.strftime("%Y-%m-%d"),
            "slot": local.dt.hour * 4 + local.dt.minute // 15,
            "room": t["room_id"],
            "code": code,
        }
    )
    days: dict[str, dict] = {}
    for (day, room), g in frame.groupby(["day", "room"], sort=True):
        row = ["?"] * 100  # DST days have 92 or 100 quarter hours
        for slot, c in zip(g["slot"], g["code"], strict=True):
            row[int(slot)] = c
        days.setdefault(day, {"rooms": {}})["rooms"][room] = "".join(row[:96]).rstrip("?") or "?"

    lc = ds.lifecycle
    lc_day = lc["timestamp_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    arrivals = (
        lc[lc["type"].isin(["checkin", "checkin_early", "checkin_after_wait"])]
        .groupby(lc_day)
        .size()
    )
    departures = lc[lc["type"].isin(["checkout", "checkout_early"])].groupby(lc_day).size()
    roster = ds.staff_roster
    r_day = roster["start_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    for day, info in days.items():
        info["arrivals"] = int(arrivals.get(day, 0))
        info["departures"] = int(departures.get(day, 0))
        today = roster[r_day == day]
        info["staff"] = [
            {
                "id": r.resource_id,
                "role": r.role,
                "status": r.status,
                "start": utc_z(r.start_utc),
                "end": utc_z(r.end_utc),
                "known": utc_z(r.status_known_utc) if pd.notna(r.status_known_utc) else None,
            }
            for r in today.itertuples()
        ]
    return days


def _task_intervals(
    ds: Dataset,
) -> dict[tuple[str, str, str], list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """Recorded cleaner/robot intervals, keyed by (role, room, resource)."""
    kinds = {
        "cleaner": ("cleaning_started", "cleaning_completed"),
        "robot": ("robot_floor_cleaning_started", "robot_floor_cleaning_completed"),
    }
    out: dict[tuple[str, str, str], list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    events = ds.events.dropna(subset=["room_id", "resource_id"]).sort_values("timestamp_utc")
    for role, (started, completed) in kinds.items():
        selected = events[events["type"].isin([started, completed])]
        for (room, resource), rows in selected.groupby(["room_id", "resource_id"], sort=False):
            open_at: pd.Timestamp | None = None
            for row in rows.itertuples():
                if row.type == started:
                    open_at = row.timestamp_utc
                elif open_at is not None and row.timestamp_utc >= open_at:
                    out[(role, str(room), str(resource))].append((open_at, row.timestamp_utc))
                    open_at = None
    return out


def _local_day(ts: pd.Timestamp, tz) -> str:
    return ts.tz_convert(tz).strftime("%Y-%m-%d")


def _day_end(day: str, tz) -> pd.Timestamp:
    return (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(tz).tz_convert("UTC")


def _headline(reason: str) -> str:
    return reason.split(". ", 1)[0].rstrip(".")


def _effect_kind(issue: str, assets: list[str]) -> str | None:
    if issue == "water_leak":
        return "water"
    if issue == "heating_comfort":
        return "heating"
    if issue in ("room_turnaround", "occupancy_surge"):
        return "turnaround"
    if issue == "ventilation_maintenance" and any(a.startswith("GW_F") for a in assets):
        return "sensor"
    return None


def _effect_floor(ds: Dataset, assets: list[str]) -> tuple[str | None, int | None]:
    room = next((a for a in assets if a in ds.room_ids), None)
    if room:
        floor = next(int(r["floor"]) for r in ds.building["building"]["rooms"] if r["id"] == room)
        return room, floor
    gateway = next((a for a in assets if re.fullmatch(r"GW_F[1-4]", a)), None)
    return (None, int(gateway[-1])) if gateway else (None, None)


def _activity(ds: Dataset, decisions: list[Decision]) -> dict[str, dict[str, list[dict]]]:
    """Compact actor/effect timelines for the Live Day 3D replay."""
    tz = ds.local_tz
    status = {row["event_id"]: row["status"] for row in executor.execute(decisions, ds)}
    intervals = _task_intervals(ds)
    room_floor = {r["id"]: int(r["floor"]) for r in ds.building["building"]["rooms"]}
    cleaning_by_room: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    for (role, room, _resource), pairs in intervals.items():
        if role == "cleaner":
            cleaning_by_room[room].extend(pairs)
    matched_intervals: set[tuple] = set()
    activity: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"actors": [], "effects": []})

    for d in decisions:
        at = pd.Timestamp(d.timestamp_utc)
        assets = list(d.affected_assets)
        room, floor = _effect_floor(ds, assets)
        day = _local_day(at, tz)

        if (
            d.action in ("dispatch_robot", "schedule_cleaning")
            and room
            and d.resource_id
            and status[d.event_id] == "executed_simulated"
        ):
            role = "robot" if d.action == "dispatch_robot" else "cleaner"
            matches = [
                pair
                for pair in intervals.get((role, room, d.resource_id), [])
                if pair[0] >= at and pair[0] < _day_end(day, tz)
            ]
            recorded = min(matches, default=None, key=lambda pair: pair[0])
            if recorded:
                matched_intervals.add((role, room, d.resource_id, recorded[0], recorded[1]))
            task_start = recorded[0] if recorded else at
            task_end = recorded[1] if recorded else at + pd.Timedelta(minutes=30)
            activity[day]["actors"].append(
                {
                    "id": d.event_id,
                    "episode_id": d.episode_id or d.event_id,
                    "resource_id": d.resource_id,
                    "role": role,
                    "room_id": room,
                    "floor": floor,
                    "action": d.action,
                    "status": status[d.event_id],
                    "depart_utc": utc_z(at),
                    "task_start_utc": utc_z(task_start),
                    "task_end_utc": utc_z(task_end),
                    "duration_source": "recorded_events" if recorded else "fallback_30m",
                }
            )

        kind = _effect_kind(d.issue_type, assets)
        if kind and floor is not None:
            is_major = (
                d.priority == "critical"
                or d.requires_human_approval
                or bool(d.requires_guest_consent)
                or (kind in ("water", "heating") and d.priority == "high")
                or kind == "sensor"
            )
            effect_end = _day_end(day, tz)
            if kind == "turnaround" and room:
                completions = [
                    end
                    for start, end in cleaning_by_room.get(room, [])
                    if start >= at and start < effect_end
                ]
                effect_end = min(
                    min(completions) if completions else at + pd.Timedelta(hours=2), effect_end
                )
            activity[day]["effects"].append(
                {
                    "id": d.event_id,
                    "episode_id": d.episode_id or d.event_id,
                    "kind": kind,
                    "room_id": room,
                    "floor": floor,
                    "priority": d.priority,
                    "requires_human_approval": d.requires_human_approval,
                    "action_ids": (
                        [d.event_id]
                        if d.requires_human_approval or d.priority == "critical"
                        else []
                    ),
                    "major": is_major,
                    "start_utc": utc_z(at),
                    "end_utc": utc_z(effect_end),
                    "headline": _headline(d.reason),
                }
            )

    # Recorded work keeps an ordinary day alive even when Steward did not need to intervene.
    # Decision-linked intervals above win; the remaining intervals become neutral replay actors.
    for (role, room, resource), pairs in intervals.items():
        if room not in room_floor:
            continue
        for start, end in pairs:
            if (role, room, resource, start, end) in matched_intervals:
                continue
            day = _local_day(start, tz)
            stamp = start.strftime("%Y%m%dT%H%M")
            activity[day]["actors"].append(
                {
                    "id": f"recorded:{role}:{room}:{resource}:{stamp}",
                    "episode_id": f"recorded:{role}:{room}:{stamp}",
                    "resource_id": resource,
                    "role": role,
                    "room_id": room,
                    "floor": room_floor[room],
                    "action": "recorded_cleaning",
                    "status": "recorded_event",
                    "depart_utc": utc_z(start),
                    "task_start_utc": utc_z(start),
                    "task_end_utc": utc_z(end),
                    "duration_source": "recorded_events",
                }
            )

    for info in activity.values():
        info["actors"].sort(key=lambda x: (x["depart_utc"], x["id"]))
        merged: dict[tuple, dict] = {}
        for effect in info["effects"]:
            key = (
                effect["kind"],
                effect["floor"],
                effect["room_id"],
            )
            current = merged.get(key)
            if current is None:
                current = effect.copy()
                location = effect["room_id"] or f"F{effect['floor']}"
                current["id"] = f"visual:{effect['kind']}:{location}:{effect['start_utc'][:10]}"
                merged[key] = current
                continue
            if PRIORITY_RANK[effect["priority"]] < PRIORITY_RANK[current["priority"]]:
                current["priority"] = effect["priority"]
                current["headline"] = effect["headline"]
            current["major"] = current["major"] or effect["major"]
            current["requires_human_approval"] = (
                current["requires_human_approval"] or effect["requires_human_approval"]
            )
            current["action_ids"] = sorted(set(current["action_ids"]) | set(effect["action_ids"]))
            current["start_utc"] = min(current["start_utc"], effect["start_utc"])
            current["end_utc"] = max(current["end_utc"], effect["end_utc"])
        info["effects"] = sorted(merged.values(), key=lambda x: (x["start_utc"], x["id"]))
    return activity


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def build(
    data_dir: str | Path,
    decisions_path: str | Path,
    dismissed_path: str | Path | None = None,
    insights_path: str | Path | None = None,
    scorecard: dict | None = None,
) -> dict:
    ds = Dataset(data_dir)
    report = validate_file(decisions_path, ds)
    learning = evidence_for(ds)
    return {
        "meta": {
            "profile": ds.profile,
            "start": utc_z(ds.start),
            "end": utc_z(ds.end),
            "local_timezone": str(ds.local_tz),
            "comfort_band_c": list(ds.comfort_band_c),
            "decisions": len(report.decisions),
            "validator_errors": len(report.errors),
            "note": "Synthetic PLATO-40 replay. All executions are simulated by a mock executor.",
        },
        "rooms": [
            {
                "id": r["id"],
                "floor": r["floor"],
                "orientation": r["orientation"],
                "area_m2": r["area_m2"],
                "has_bathtub": r["has_bathtub"],
            }
            for r in ds.building["building"]["rooms"]
        ],
        "episodes": _episodes(ds, report.decisions),
        "dismissed": _jsonl(Path(dismissed_path)) if dismissed_path else [],
        "insights": _jsonl(Path(insights_path))
        if insights_path
        else [item for item in learning["observations"] if "target" in item],
        "learning": learning,
        "scorecard": scorecard,
    }


def build_days(data_dir: str | Path, decisions: list[Decision] | str | Path | None = None) -> dict:
    """Room states and staff per day for the replay clock, kept in a separate file so the
    main bundle keeps its format."""
    ds = Dataset(data_dir)
    days = _days(ds)
    if decisions is not None:
        if not isinstance(decisions, list):
            decisions = validate_file(decisions, ds).decisions
        for day, extra in _activity(ds, decisions).items():
            days.setdefault(day, {"rooms": {}, "staff": [], "arrivals": 0, "departures": 0})
            days[day].update(extra)
    return {"days": days}


def write(bundle: dict, out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
