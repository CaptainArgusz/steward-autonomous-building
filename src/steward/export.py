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

import pandas as pd

from steward import executor
from steward.contract import Decision, utc_z, validate_file
from steward.dataset import Dataset

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
    status = {row["event_id"]: row["status"] for row in executor.execute(decisions)}
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

        charts: list[dict] = []
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
        "insights": _jsonl(Path(insights_path)) if insights_path else [],
        "scorecard": scorecard,
    }


def write(bundle: dict, out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
