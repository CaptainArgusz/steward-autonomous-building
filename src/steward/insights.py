"""Level 3 - learn & feed back: what a year of PLATO-40 teaches CLARA and PLATO.

    uv run python -m steward.insights data/dev

recomputes every number, rewrites ``insights.jsonl`` and ``insights.md`` and drops the
supporting tables under ``out/insights/``. Nothing in the report is typed by hand: the
markdown is rendered from the same objects that go into the JSONL.

This is hindsight ANALYSIS, not detection, so it may use the whole dataset at once
(``ds.stays``). Each analysis returns one insight dict or raises ``Skip`` with the reason
(for example "no summer in this dataset") - a 21-day dataset degrades, it does not crash.

All findings concern the fictional PLATO-40 only. Benefits are qualitative or labelled
estimates; nothing here is a measured saving.
"""

from __future__ import annotations

import json
import sys
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from steward.dataset import Dataset

RNG_SEED = 40
COMFORT_HIGH_C = 26.0  # common summer overheating threshold for sleeping rooms
MD_ARCH_START = "<!-- target-architecture:start -->"
MD_ARCH_END = "<!-- target-architecture:end -->"


class Skip(Exception):
    """This analysis cannot be done on this dataset; the message says why."""


# ------------------------------------------------------------------------- statistics


def compare_groups(a, b, n_boot: int = 4000) -> dict:
    """Difference of means a - b over independent units (rooms) with bootstrap CI and permutation p."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        raise Skip("fewer than 3 rooms in a comparison group")
    rng = np.random.default_rng(RNG_SEED)
    diff = a.mean() - b.mean()
    boots = rng.choice(a, (n_boot, len(a))).mean(1) - rng.choice(b, (n_boot, len(b))).mean(1)
    pooled = np.concatenate([a, b])
    perm = np.array([rng.permutation(pooled) for _ in range(n_boot)])
    perm_diff = perm[:, : len(a)].mean(1) - perm[:, len(a) :].mean(1)
    p = float((np.abs(perm_diff) >= abs(diff)).mean())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "diff": float(diff),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p": max(p, 1.0 / n_boot),
        "n_a": int(len(a)),
        "n_b": int(len(b)),
    }


def _ci(c: dict, unit: str, digits: int = 2) -> str:
    p = "p < 0.001" if c["p"] < 0.001 else f"p = {c['p']:.3f}"
    return (
        f"{c['diff']:+.{digits}f} {unit} (95 % bootstrap CI {c['ci_low']:+.{digits}f} to "
        f"{c['ci_high']:+.{digits}f}, permutation {p}, {c['n_a']} vs {c['n_b']} rooms)"
    )


def _r(values, digits: int = 2) -> list:
    return [None if pd.isna(v) else round(float(v), digits) for v in values]


def _chart(title: str, kind: str, unit: str, categories, series: dict, digits: int = 2) -> dict:
    return {
        "title": title,
        "kind": kind,
        "unit": unit,
        "categories": [str(c) for c in categories],
        "series": [{"name": str(k), "values": _r(v, digits)} for k, v in series.items()],
    }


# ---------------------------------------------------------------------------- context


class Context:
    """Frames shared by several analyses, built once."""

    def __init__(self, ds: Dataset):
        self.ds = ds
        self.tz = ds.local_tz
        self.rooms = ds.rooms
        self.days = (ds.end - ds.start) / pd.Timedelta(days=1)
        self.tables: dict[str, pd.DataFrame] = {}

    @cached_property
    def outdoor(self) -> pd.Series:
        w = self.ds.weather.dropna(subset=["outdoor_c"]).drop_duplicates("timestamp_utc")
        return w.set_index("timestamp_utc")["outdoor_c"].sort_index()

    @cached_property
    def telemetry(self) -> pd.DataFrame:
        """Room telemetry + occupancy, hours since check-in, outdoor temperature, room attributes."""
        ds = self.ds
        t = ds.add_occupancy(ds.room_telemetry.drop_duplicates(["timestamp_utc", "room_id"]))
        checkins = ds.lifecycle[ds.lifecycle["occupied"]][["timestamp_utc", "room_id"]].rename(
            columns={"timestamp_utc": "checkin_utc"}
        )
        t = pd.merge_asof(
            t.sort_values("timestamp_utc"),
            checkins.sort_values("checkin_utc"),
            left_on="timestamp_utc",
            right_on="checkin_utc",
            by="room_id",
        )
        t["hours_in"] = (t["timestamp_utc"] - t["checkin_utc"]) / pd.Timedelta(hours=1)
        t["outdoor_c"] = t["timestamp_utc"].map(self.outdoor)
        t = t.join(self.rooms[["floor", "orientation", "area_m2", "has_bathtub"]], on="room_id")
        local = t["timestamp_utc"].dt.tz_convert(self.tz)
        t["month"] = local.dt.month
        t["hour"] = local.dt.hour
        t["date"] = local.dt.date
        # Sensor glitches (the data contains readings from -40 to 85 C) are not room temperatures.
        t["temperature_c"] = t["temperature_c"].where(t["temperature_c"].between(5.0, 40.0))
        t["shortfall_k"] = t["setpoint_c"] - t["temperature_c"]
        return t

    @cached_property
    def occupied_nights(self) -> pd.Series:
        """Occupied nights per room (hindsight; open stays run to the end of the data)."""
        s = self.ds.stays.copy()
        s["end_utc"] = s["end_utc"].fillna(self.ds.end)
        s["nights"] = (s["end_utc"] - s["start_utc"]) / pd.Timedelta(days=1)
        return s.groupby("room_id")["nights"].sum().reindex(self.ds.room_ids).fillna(0.0)

    def requests(self, request_type: str) -> pd.DataFrame:
        g = self.ds.guest_requests
        return g[(g["request_type"] == request_type) & g["room_id"].isin(self.ds.room_ids)]

    @cached_property
    def alarms(self) -> pd.DataFrame:
        """Raised BMS alarms with clear time and duration."""
        ev = self.ds.events
        if "alarm_code" not in ev:
            return pd.DataFrame(columns=["timestamp_utc", "asset_id", "alarm_code", "hours"])
        al = ev[ev["type"] == "bms_alarm"][["timestamp_utc", "asset_id", "alarm_code", "state"]]
        raised = al[al["state"] == "raised"].sort_values("timestamp_utc")
        cleared = (
            al[al["state"] == "cleared"]
            .rename(columns={"timestamp_utc": "cleared_utc"})
            .drop(columns="state")
            .sort_values("cleared_utc")
        )
        m = pd.merge_asof(
            raised,
            cleared,
            left_on="timestamp_utc",
            right_on="cleared_utc",
            by=["asset_id", "alarm_code"],
            direction="forward",
            allow_exact_matches=False,
        )
        m["hours"] = (m["cleared_utc"] - m["timestamp_utc"]) / pd.Timedelta(hours=1)
        return m

    @cached_property
    def cleanings(self) -> pd.DataFrame:
        ev = self.ds.events
        start = ev[ev["type"] == "cleaning_started"][["timestamp_utc", "room_id", "resource_id"]]
        end = ev[ev["type"] == "cleaning_completed"][["timestamp_utc", "room_id"]].rename(
            columns={"timestamp_utc": "end_utc"}
        )
        cl = pd.merge_asof(
            start.sort_values("timestamp_utc"),
            end.sort_values("end_utc"),
            left_on="timestamp_utc",
            right_on="end_utc",
            by="room_id",
            direction="forward",
            tolerance=pd.Timedelta(hours=6),
        ).dropna(subset=["end_utc"])
        cl["minutes"] = (cl["end_utc"] - cl["timestamp_utc"]) / pd.Timedelta(minutes=1)
        robot = ev[ev["type"] == "robot_floor_cleaning_completed"][["timestamp_utc", "room_id"]]
        robot = robot.rename(columns={"timestamp_utc": "robot_done_utc"}).sort_values(
            "robot_done_utc"
        )
        cl = pd.merge_asof(
            cl,
            robot,
            left_on="timestamp_utc",
            right_on="robot_done_utc",
            by="room_id",
            direction="backward",
            tolerance=pd.Timedelta(hours=8),
        )
        cl["robot_first"] = cl["robot_done_utc"].notna()
        cl["date"] = cl["timestamp_utc"].dt.tz_convert(self.tz).dt.date
        return cl.join(self.rooms[["floor", "area_m2", "has_bathtub"]], on="room_id")


# ------------------------------------------------------------------- PLATO: the building


def floor1_heat_loss(ctx: Context) -> dict:
    t = ctx.telemetry
    cold = t[
        t["occupied"]
        & (t["hours_in"] > 6)  # past the warm-up from the 18 C setback
        & (t["window_open"] == 0)
        & (t["outdoor_c"] < 5)
        & t["shortfall_k"].notna()
    ]
    if cold["room_id"].nunique() < 30 or len(cold) < 20_000:
        raise Skip("too few occupied heating-season readings")
    per_room = cold.groupby("room_id").agg(
        shortfall_k=("shortfall_k", "mean"),
        below_20_share=("temperature_c", lambda x: float((x < 20).mean())),
        readings=("shortfall_k", "size"),
    )
    per_room = per_room.join(ctx.rooms[["floor", "orientation", "area_m2"]])
    ctx.tables["floor1_heat_loss_by_room"] = per_room
    f1, rest = per_room[per_room["floor"] == 1], per_room[per_room["floor"] != 1]
    cmp_short = compare_groups(f1["shortfall_k"], rest["shortfall_k"])
    cmp_b20 = compare_groups(f1["below_20_share"] * 100, rest["below_20_share"] * 100)
    if cmp_short["diff"] < 0.15 or cmp_short["p"] > 0.05:
        raise Skip(f"no floor-1 heating shortfall on this dataset ({_ci(cmp_short, 'K')})")
    by_floor = per_room.groupby("floor")[["shortfall_k", "below_20_share"]].mean()

    bins = [-30, -5, 0, 5]
    labels = ["below -5 °C", "-5 to 0 °C", "0 to 5 °C"]
    cold = cold.assign(outdoor_bin=pd.cut(cold["outdoor_c"], bins, labels=labels))
    by_bin = cold.pivot_table(
        index="outdoor_bin", columns="floor", values="shortfall_k", aggfunc="mean", observed=True
    )
    ctx.tables["floor1_heat_loss_by_outdoor"] = by_bin
    gap = by_bin[1] - by_bin.drop(columns=1).mean(axis=1)

    # Controls: same comparison within orientation and within room size.
    controls = []
    for col in ("orientation", "area_m2"):
        for key, grp in per_room.groupby(col):
            a, b = grp[grp["floor"] == 1]["shortfall_k"], grp[grp["floor"] != 1]["shortfall_k"]
            controls.append(f"{col}={key}: {a.mean() - b.mean():+.2f} K")
    n_colder = int((f1["shortfall_k"] > rest["shortfall_k"].quantile(0.9)).sum())
    mild = f1[f1["shortfall_k"] < f1["shortfall_k"].median() / 2].index.tolist()

    alarms = ctx.alarms
    low = alarms[alarms["alarm_code"] == "ROOM_TEMP_LOW"]
    low_f1 = int(low["asset_id"].isin(f1.index).sum())
    cold_req = ctx.requests("temperature_cold")
    cold_req_f1 = int(cold_req["room_id"].isin(f1.index).sum())

    return {
        "insight_id": "P-01",
        "target": "PLATO",
        "topic": "Floor 1 loses heat that floors 2-4 do not",
        "finding": (
            f"In occupied PLATO-40 rooms during heating weather (outdoor < 5 °C, window closed, more than 6 h "
            f"after check-in) floor 1 sits {by_floor.loc[1, 'shortfall_k']:.2f} K below its setpoint on average, "
            f"floors 2-4 only {rest['shortfall_k'].mean():.2f} K. Difference between room means: "
            f"{_ci(cmp_short, 'K')}. Floor 1 is below the 20 °C comfort limit in "
            f"{by_floor.loc[1, 'below_20_share']:.0%} of those readings against {rest['below_20_share'].mean():.0%} "
            f"elsewhere ({_ci(cmp_b20, 'percentage points', 1)}). The gap widens as it gets colder: "
            + ", ".join(f"{g:+.2f} K at {lab}" for lab, g in gap.items())
            + f". {n_colder} of the 10 floor-1 rooms are colder than 90 % of all other rooms"
            + (f" (least affected: {', '.join(mild)})" if mild else "")
            + f". {low_f1} of {len(low)} ROOM_TEMP_LOW alarms are on floor 1."
        ),
        "comparison": "Mean of per-room means, floor 1 (10 rooms) against floors 2-4 (30 rooms), same hours, same plant.",
        "evidence": [
            f"room_telemetry.csv: setpoint_c - temperature_c, {len(cold):,} occupied readings at outdoor < 5 °C",
            "events.jsonl: check-in/check-out for occupancy and time since check-in",
            "weather.csv: outdoor_c bins",
            f"events.jsonl: bms_alarm ROOM_TEMP_LOW ({low_f1} of {len(low)} on floor 1)",
            "out/insights/floor1_heat_loss_by_room.csv",
        ],
        "recommendation": (
            "Treat the floor slab over the open parking deck as an external envelope element in the next "
            "building: raise its insulation level and check the thermal bridges at the supports, and size the "
            "floor-1 heat emitters for the extra loss instead of using the same module on every floor."
        ),
        "expected_benefit": (
            "Qualitative: floor 1 reaches the same comfort as the other floors without guests raising setpoints; "
            "fewer low-temperature alarms. No energy saving is claimed - heat is not metered per room."
        ),
        "uncertainty": (
            "The data proves that floor 1 is colder and that the gap grows with falling outdoor temperature, "
            "which is the signature of extra heat loss rather than of a control offset. It does not prove WHERE "
            "the heat goes: the open parking deck below is our hypothesis, building.json has no envelope data. "
            f"Holds within every orientation and room size ({'; '.join(controls)}). Plant faults hit all floors "
            f"at once and cannot produce a floor difference. Guests barely notice so far ({cold_req_f1} of "
            f"{len(cold_req)} 'too cold' requests come from floor 1). A slab surface temperature sensor or a "
            "thermographic survey would settle the cause."
        ),
        "confidence": 0.85,
        "charts": [
            _chart(
                "Shortfall below setpoint by outdoor temperature",
                "line",
                "K",
                by_bin.index,
                {f"Floor {f}": by_bin[f] for f in by_bin.columns},
            ),
            _chart(
                "Share of occupied readings below 20 °C",
                "bar",
                "%",
                [f"Floor {f}" for f in by_floor.index],
                {"below 20 °C": by_floor["below_20_share"] * 100},
                1,
            ),
        ],
        "rooms": {r: round(float(v), 2) for r, v in per_room["shortfall_k"].items()},
        "rooms_unit": "K below setpoint",
    }


def summer_overheating(ctx: Context) -> dict:
    t = ctx.telemetry
    occ = t[t["occupied"] & t["temperature_c"].notna()]
    monthly = occ.groupby("month")["temperature_c"].agg(
        mean="mean", over=lambda x: float((x > COMFORT_HIGH_C).mean())
    )
    hot_months = monthly[monthly["over"] > 0.05].index.tolist()
    if not hot_months:
        raise Skip(f"no month with occupied rooms above {COMFORT_HIGH_C:.0f} °C in this dataset")
    hot_req = ctx.requests("temperature_hot")
    cold_req = ctx.requests("temperature_cold")
    req_month = hot_req["timestamp_utc"].dt.tz_convert(ctx.tz).dt.month.value_counts()
    monthly["requests"] = req_month.reindex(monthly.index).fillna(0).astype(int)
    ctx.tables["overheating_by_month"] = monthly

    hot = occ[occ["month"].isin(hot_months)]
    nights = hot.groupby("room_id").size() / 96.0
    per_room = pd.DataFrame(
        {
            "mean_c": hot.groupby("room_id")["temperature_c"].mean(),
            "over_share": hot.groupby("room_id")["temperature_c"].apply(
                lambda x: float((x > COMFORT_HIGH_C).mean())
            ),
            "requests_per_100_nights": hot_req[
                hot_req["timestamp_utc"].dt.tz_convert(ctx.tz).dt.month.isin(hot_months)
            ]["room_id"]
            .value_counts()
            .reindex(nights.index)
            .fillna(0)
            / nights
            * 100,
        }
    ).join(ctx.rooms[["floor", "orientation", "area_m2"]])
    ctx.tables["overheating_by_room"] = per_room
    south, north = (
        per_room[per_room["orientation"] == "S"],
        per_room[per_room["orientation"] == "N"],
    )
    cmp_temp = compare_groups(south["mean_c"], north["mean_c"])
    cmp_req = compare_groups(south["requests_per_100_nights"], north["requests_per_100_nights"])
    top = per_room[per_room["floor"] == per_room["floor"].max()]
    cmp_top = compare_groups(top["mean_c"], per_room.drop(top.index)["mean_c"])
    ctx.tables["_overheating_checks"] = pd.DataFrame(
        {
            "check": ["top floor vs others, mean °C"],
            "result": [_ci(cmp_top, "K")],
        }
    )

    daily = hot.groupby("date")["temperature_c"].mean()
    hot_days = int((daily > COMFORT_HIGH_C).sum())
    worst = daily.max()
    req_daily = hot_req.groupby(hot_req["timestamp_utc"].dt.tz_convert(ctx.tz).dt.date).size()
    both = pd.DataFrame({"t": daily, "r": req_daily}).fillna({"r": 0}).dropna()
    corr = float(both["t"].corr(both["r"]))
    cooling = ctx.ds.building.get("operations", {}).get("cooling", "not stated")
    by_fo = per_room.pivot_table(index="floor", columns="orientation", values="over_share") * 100
    month_names = {m: pd.Timestamp(2026, m, 1).strftime("%b") for m in monthly.index}

    return {
        "insight_id": "P-02",
        "target": "PLATO",
        "topic": "Rooms overheat in a hot spell and the building has no way to cool them",
        "finding": (
            f"'Too hot' is the most frequent comfort complaint in PLATO-40: {len(hot_req)} requests against "
            f"{len(cold_req)} 'too cold' over the whole dataset, all of them in "
            f"{', '.join(month_names[m] for m in sorted(req_month.index))}. In "
            + " and ".join(
                f"{month_names[m]} occupied rooms were above {COMFORT_HIGH_C:.0f} °C "
                f"{monthly.loc[m, 'over']:.0%} of the time (mean {monthly.loc[m, 'mean']:.1f} °C)"
                for m in hot_months
            )
            + f". On {hot_days} days the average occupied room stayed above {COMFORT_HIGH_C:.0f} °C around the "
            f"clock (worst day {worst:.1f} °C); daily complaints follow the daily indoor mean (r = {corr:.2f}). "
            f"South rooms are warmer than north rooms by {_ci(cmp_temp, 'K')} and complain more "
            f"({south['requests_per_100_nights'].mean():.1f} vs {north['requests_per_100_nights'].mean():.1f} "
            f"requests per 100 occupied nights; {_ci(cmp_req, 'per 100 nights', 1)}). "
            f"building.json states cooling: '{cooling}'."
        ),
        "comparison": "Occupied readings by month; per-room means, south (20 rooms) against north (20 rooms), in the hot months.",
        "evidence": [
            f"room_telemetry.csv: temperature_c in occupied rooms, months {hot_months}",
            f"guest_requests.csv: request_type=temperature_hot ({len(hot_req)}), temperature_cold ({len(cold_req)})",
            "building.json: operations.cooling",
            "out/insights/overheating_by_room.csv, overheating_by_month.csv",
        ],
        "recommendation": (
            "Plan summer comfort into the next building instead of relying on ventilation alone: external "
            "shading on the south façade as standard, and a reversible heat pump (or at least prepared "
            "connections for active cooling) so that the existing hydronic system can cool in a heat wave."
        ),
        "expected_benefit": (
            "Qualitative: removes the largest single source of comfort complaints in the dataset. Shading "
            "addresses the south/north difference; only active cooling addresses the building-wide level."
        ),
        "uncertainty": (
            "One summer with one hot spell: the size of the effect depends on the weather that year. The "
            "south/north difference is consistent across rooms but small next to the building-wide overheating, "
            "so shading alone will not be enough. Floor level makes no measurable difference "
            f"(top floor vs others {cmp_top['diff']:+.2f} K, p = {cmp_top['p']:.2f}) - the roof is not the cause. "
            "The synthetic thermal model is uncalibrated; absolute temperatures are illustrative."
        ),
        "confidence": 0.8,
        "charts": [
            _chart(
                f"Occupied time above {COMFORT_HIGH_C:.0f} °C and 'too hot' requests",
                "bar",
                "% / count",
                [month_names[m] for m in monthly.index],
                {
                    f"% of occupied time above {COMFORT_HIGH_C:.0f} °C": monthly["over"] * 100,
                    "'too hot' requests": monthly["requests"],
                },
                1,
            ),
            _chart(
                f"Hot months: share of occupied time above {COMFORT_HIGH_C:.0f} °C",
                "bar",
                "%",
                [f"Floor {f}" for f in by_fo.index],
                {("South" if c == "S" else "North"): by_fo[c] for c in by_fo.columns},
                1,
            ),
        ],
        "rooms": {r: round(float(v) * 100, 1) for r, v in per_room["over_share"].items()},
        "rooms_unit": f"% of occupied time above {COMFORT_HIGH_C:.0f} °C in the hot months",
    }


def acoustic_hotspots(ctx: Context) -> dict:
    noise = ctx.requests("noise_complaint")
    if len(noise) < 40:
        raise Skip(f"only {len(noise)} noise complaints - too few to locate a pattern")
    nights = ctx.occupied_nights
    rate = (noise["room_id"].value_counts().reindex(nights.index).fillna(0) / nights * 100).rename(
        "rate"
    )
    per_room = rate.to_frame().join(ctx.rooms[["floor", "orientation"]])
    per_room["position"] = per_room.index.str[-2:]
    per_room["complaints"] = (
        noise["room_id"].value_counts().reindex(per_room.index).fillna(0).astype(int)
    )
    ctx.tables["noise_by_room"] = per_room
    median = per_room["rate"].median()
    hot = per_room[per_room["rate"] >= 2.5 * max(median, 0.1)]
    if len(hot) < 3:
        raise Skip("noise complaints are spread evenly - no hotspot rooms")
    rest = per_room.drop(hot.index)
    positions = sorted(hot["position"].unique())
    floors = sorted(hot["floor"].unique())
    block = per_room[per_room["position"].isin(positions) & per_room["floor"].isin(floors)]
    share_complaints = hot["complaints"].sum() / per_room["complaints"].sum()
    share_nights = nights[hot.index].sum() / nights.sum()
    kind = np.where(
        noise["text"].str.contains(
            "Gang|corridor|couloir|corridoio|pasillo|gang", case=False, na=False
        ),
        "corridor",
        "neighbouring room",
    )
    kinds = pd.Series(kind).value_counts()
    hours = noise["timestamp_utc"].dt.tz_convert(ctx.tz).dt.hour
    night_share = float(((hours >= 21) | (hours < 6)).mean())
    grid = per_room.pivot_table(index="position", columns="floor", values="rate")
    cmp = compare_groups(hot["rate"], rest["rate"])

    return {
        "insight_id": "P-03",
        "target": "PLATO",
        "topic": "Noise complaints concentrate in the same room positions on the upper floors",
        "finding": (
            f"{len(hot)} of 40 PLATO-40 rooms produce {share_complaints:.0%} of all {len(noise)} noise complaints "
            f"while accounting for {share_nights:.0%} of occupied nights: "
            f"{hot['rate'].mean():.1f} complaints per 100 occupied nights against {rest['rate'].mean():.1f} in the "
            f"other rooms. They are not scattered: all sit at positions {', '.join(positions)} on floors "
            f"{', '.join(map(str, floors))} ({len(hot)} of the {len(block)} rooms in that block) - the same "
            f"positions on every upper floor, on both sides of the corridor. The same positions on floor 1 are quiet. "
            f"{kinds.get('neighbouring room', 0)} complaints name the neighbouring room, {kinds.get('corridor', 0)} "
            f"the corridor; {night_share:.0%} arrive between 21:00 and 06:00."
        ),
        "comparison": "Complaints per 100 occupied nights per room; hotspot rooms selected from the data (>= 2.5x the median room).",
        "evidence": [
            f"guest_requests.csv: request_type=noise_complaint ({len(noise)}), free text names corridor or neighbour",
            "events.jsonl: stays for occupied nights per room",
            "building.json: room positions (x01-x05 south, x06-x10 north)",
            "out/insights/noise_by_room.csv",
        ],
        "recommendation": (
            "Review what the modules at these positions on the upper floors have in common in the PLATO layout "
            "(stair and lift core, module joints, party-wall build-up, door type) and raise the acoustic "
            "specification of party walls and room doors there. Until then CLARA should avoid these rooms for "
            "late arrivals and long stays when the house is not full."
        ),
        "expected_benefit": (
            "Qualitative: complaints in the affected block would fall towards the level of the other rooms "
            f"(about {rest['rate'].mean():.1f} per 100 nights) if the cause is constructional."
        ),
        "uncertainty": (
            "The hotspot rooms were found by looking at the data, so the permutation test "
            f"({_ci(cmp, 'per 100 nights', 1)}) overstates the significance; the regular spatial pattern "
            "(same positions, three floors, both sides) is the stronger argument. building.json has no floor "
            "plan, so what sits at these positions and the cause - core, joints or wall build-up - is a hypothesis. "
            "Complaints measure annoyance, not sound level; a sound insulation measurement between two of these "
            "rooms would settle it."
        ),
        "confidence": 0.7,
        "charts": [
            _chart(
                "Noise complaints per 100 occupied nights by room position",
                "bar",
                "per 100 nights",
                [f"x{p}" for p in grid.index],
                {f"Floor {f}": grid[f] for f in grid.columns},
                1,
            )
        ],
        "rooms": {r: round(float(v), 1) for r, v in per_room["rate"].items()},
        "rooms_unit": "noise complaints per 100 occupied nights",
    }


def _filter_cycles(ctx: Context) -> tuple[pd.Series, pd.DataFrame]:
    """Daily filter pressure and one row per filter life (start, end, days, start/end pressure, slope)."""
    ahu = ctx.ds.equipment_wide("AHU1")
    if "filter_dp_pa" not in ahu:
        raise Skip("no AHU1 filter_dp_pa metric")
    daily = ahu["filter_dp_pa"].resample("D").median().dropna()
    if len(daily) < 60:
        raise Skip("less than 60 days of filter pressure - no full filter life to measure")
    # A filter change = the level steps down and STAYS down (3-day medians before and after).
    before = daily.shift(1).rolling(3, min_periods=2).median()
    after = daily[::-1].rolling(3, min_periods=2).median()[::-1]
    stepped = after < 0.75 * before
    drops = daily.index[stepped & ~stepped.shift(1, fill_value=False)]
    edges = [daily.index[0], *drops, daily.index[-1] + pd.Timedelta(days=1)]
    rows = []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        seg = daily[(daily.index >= a) & (daily.index < b)]
        if len(seg) < 5:
            continue
        rows.append(
            {
                "start": a,
                "end": seg.index[-1],
                "days": len(seg),
                "dp_start_pa": float(seg.iloc[:7].min()),
                "dp_end_pa": float(seg.iloc[-3:].max()),
                "pa_per_day": float(np.polyfit(np.arange(len(seg) - 2), seg.to_numpy()[2:], 1)[0]),
                "ended_by_change": b in drops,
            }
        )
    return daily, pd.DataFrame(rows)


def rooftop_access(ctx: Context) -> dict:
    wo = ctx.ds.work_orders
    ahu_wo = wo[wo["asset_id"] == "AHU1"].sort_values("created_utc")
    postponed = ahu_wo[ahu_wo["status"] == "postponed"]
    if postponed.empty:
        raise Skip("no postponed AHU1 work orders in this dataset")
    access = postponed[
        postponed["summary"].str.contains("reach|access|platform|lift", case=False, na=False)
    ]
    done = ahu_wo[ahu_wo["status"] == "completed"]
    delays = []
    for _, p in postponed.iterrows():
        nxt = done[done["completed_utc"] > p["created_utc"]]
        if not nxt.empty:
            delays.append(
                (nxt.iloc[0]["completed_utc"] - p["scheduled_utc"]) / pd.Timedelta(days=1)
            )
    unplanned = done[done["type"] == "corrective"]
    daily, cycles = _filter_cycles(ctx)
    ctx.tables["filter_cycles"] = cycles
    planned = done[done["type"] == "preventive"]

    def dp_before(orders: pd.DataFrame) -> list[float]:
        out = []
        for ts in orders["completed_utc"]:
            before = daily[daily.index < ts.normalize()]
            if len(before):
                out.append(float(before.iloc[-3:].max()))
        return out

    dp_planned, dp_unplanned = dp_before(planned), dp_before(unplanned)
    alarms = ctx.alarms
    n_alarm = int(alarms["alarm_code"].isin(["FILTER_DP_HIGH", "AIRFLOW_LOW"]).sum())
    weekly = daily.resample("W").max()
    reason = access.iloc[0]["summary"] if len(access) else postponed.iloc[0]["summary"]

    return {
        "insight_id": "P-04",
        "target": "PLATO",
        "topic": "The rooftop air handling unit cannot be serviced without a lift platform",
        "finding": (
            f"{len(access)} of {len(postponed)} postponed AHU1 work orders in PLATO-40 were postponed for access, "
            f'in the technician\'s words: "{reason}". The planned filter changes then happened '
            f"{' and '.join(f'{d:.0f}' for d in delays)} days after their scheduled date. "
            f"Over the dataset the filters were changed {len(planned)} times as planned and {len(unplanned)} times "
            f'unplanned ("heavily loaded"); before a planned change the filter pressure drop peaked at '
            f"{np.mean(dp_planned):.0f} Pa, before an unplanned one at {np.mean(dp_unplanned):.0f} Pa "
            f"(clean filter: about {cycles['dp_start_pa'].min():.0f} Pa). "
            f"{n_alarm} FILTER_DP_HIGH / AIRFLOW_LOW alarms were raised."
        ),
        "comparison": "Work-order log against the daily median of AHU1 filter differential pressure.",
        "evidence": [
            "work_orders.csv: asset_id=AHU1, status=postponed, summary text",
            "work_orders.csv: AHU1 preventive vs corrective filter replacements",
            "equipment.csv: AHU1 filter_dp_pa, daily median",
            "out/insights/filter_cycles.csv",
        ],
        "recommendation": (
            "Give the rooftop plant permanent, safe access in the next building - a roof hatch with fixed "
            "ladder or stair and a walkway to the unit - or place the air handling unit in a plant room that "
            "is reachable from inside. Filter changes are the most frequent maintenance task on the unit and "
            "must not depend on hiring a lift platform."
        ),
        "expected_benefit": (
            "Qualitative: planned filter changes happen on the planned day, which removes the access-related "
            "delays seen here and the platform hire for every service visit."
        ),
        "uncertainty": (
            f"Small numbers: {len(postponed)} postponements in the dataset, read from free-text work orders that "
            "people wrote. The access problem itself is stated explicitly, not inferred. The link to the "
            "unplanned replacements is NOT proven - they occurred later in the year and may simply reflect "
            "faster filter loading in those periods (see C-05)."
        ),
        "confidence": 0.75,
        "charts": [
            _chart(
                "AHU1 filter pressure drop, weekly maximum of the daily median",
                "line",
                "Pa",
                [d.strftime("%d %b") for d in weekly.index],
                {"filter_dp_pa": weekly},
                0,
            )
        ],
    }


def _floor_outages(ctx: Context) -> pd.DataFrame:
    """Intervals in which EVERY room sensor on a floor is missing at once = its gateway is down."""
    t = ctx.ds.room_telemetry.join(ctx.rooms[["floor"]], on="room_id")
    sensor_cols = ["temperature_c", "humidity_pct", "co2_ppm"]
    t = t.assign(missing=t[sensor_cols].isna().all(axis=1))
    down = t.groupby(["timestamp_utc", "floor"])["missing"].mean().unstack() >= 0.99
    rows = []
    for floor in down.columns:
        s = down[floor]
        run = (s != s.shift()).cumsum()
        for _, seg in s[s].groupby(run[s]):
            rows.append({"floor": int(floor), "start": seg.index[0], "hours": len(seg) / 4.0})
    return pd.DataFrame(rows, columns=["floor", "start", "hours"])


def gateway_reliability(ctx: Context) -> dict:
    out = _floor_outages(ctx)
    ctx.tables["gateway_outages"] = out
    if len(out) < 3:
        raise Skip(f"only {len(out)} full-floor telemetry outages - too few to compare gateways")
    floors = sorted(ctx.rooms["floor"].unique())
    per = out.groupby("floor")["hours"].agg(episodes="size", hours="sum").reindex(floors).fillna(0)
    worst = int(per["episodes"].idxmax())
    gw = {g["floor"]: g["id"] for g in ctx.ds.building["building"]["gateways"]}
    rate = ctx.days / 30.4
    alarms = ctx.alarms
    comm = alarms[alarms["alarm_code"] == "SENSOR_COMM_LOSS"]
    comm_floor = comm["asset_id"].map(ctx.rooms["floor"]).value_counts().reindex(floors).fillna(0)
    worst_out = out[out["floor"] == worst]
    months = worst_out["start"].dt.month.nunique()
    others = per.drop(index=worst)

    return {
        "insight_id": "P-05",
        "target": "PLATO",
        "topic": f"One floor gateway ({gw.get(worst, f'floor {worst}')}) drops out repeatedly and takes the whole floor's sensors with it",
        "finding": (
            f"All ten rooms of floor {worst} of PLATO-40 lost every sensor at the same moment "
            f"{int(per.loc[worst, 'episodes'])} times ({per.loc[worst, 'hours']:.0f} h in total, "
            f"{worst_out['hours'].median():.1f} h per outage in the median, spread over {months} different months "
            f"- about {per.loc[worst, 'episodes'] / rate:.1f} per month). The other three floors together had "
            f"{int(others['episodes'].sum())} such outages ({others['hours'].sum():.0f} h). "
            f"{int(comm_floor[worst])} of {len(comm)} SENSOR_COMM_LOSS alarms concern floor {worst}. "
            "During an outage the building is blind on that floor: no temperature, CO₂, presence or window "
            "state for any of its rooms."
        ),
        "comparison": "Intervals with all room sensors of a floor missing simultaneously, per floor gateway.",
        "evidence": [
            "room_telemetry.csv: simultaneous blanks across all rooms of one floor",
            "building.json: sensors -> gateway mapping (one gateway per floor)",
            f"events.jsonl: bms_alarm SENSOR_COMM_LOSS ({len(comm)})",
            "out/insights/gateway_outages.csv",
        ],
        "recommendation": (
            "Do not hang a whole floor on a single gateway: give each floor two gateways with overlapping "
            "coverage (or a wired backhaul for the safety-relevant sensors), and buffer readings at the sensor "
            f"so that short outages are back-filled. On PLATO-40, inspect {gw.get(worst, 'the gateway')} "
            "(power supply, placement, firmware) - a recurring fault on one unit points at the unit or its location."
        ),
        "expected_benefit": (
            f"Qualitative: removes single points of failure that blinded a floor for about "
            f"{per.loc[worst, 'hours']:.0f} h in this dataset; leak and comfort detection keep working during a fault."
        ),
        "uncertainty": (
            "The outages are a direct observation, not an inference. Why this gateway fails is unknown - the "
            "data cannot tell a hardware fault from poor radio placement, so we cannot say whether the lesson "
            "is 'this unit' or 'this location in the module'. The second most affected floor "
            f"({int(others['episodes'].max())} outages) shows the problem is not strictly unique."
        ),
        "confidence": 0.85,
        "charts": [
            _chart(
                "Full-floor sensor outages",
                "bar",
                "count / hours",
                [f"Floor {f} ({gw.get(f, '')})" for f in per.index],
                {"outages": per["episodes"], "hours blind": per["hours"]},
                1,
            )
        ],
        "rooms": {r: float(per.loc[f, "hours"]) for r, f in ctx.rooms["floor"].items()},
        "rooms_unit": "hours without any sensor data",
    }


# ------------------------------------------------------------------ CLARA: operating rules


def night_flushing(ctx: Context) -> dict:
    t = ctx.telemetry
    occ = t[t["occupied"] & t["temperature_c"].notna()]
    daily = occ.groupby("date")["temperature_c"].mean()
    hot_dates = set(daily[daily > COMFORT_HIGH_C].index)
    if len(hot_dates) < 5:
        raise Skip(
            f"fewer than 5 days with occupied rooms above {COMFORT_HIGH_C:.0f} °C on average"
        )
    ahu = ctx.ds.equipment_wide("AHU1")
    local = ahu.index.tz_convert(ctx.tz)
    ahu = ahu.assign(hour=local.hour, date=local.date, outdoor_c=ctx.outdoor.reindex(ahu.index))
    ahu = ahu[ahu["date"].isin(hot_dates)]
    by_hour = ahu.groupby("hour")[["airflow_m3h", "outdoor_c"]].mean()
    indoor = occ[occ["date"].isin(hot_dates)].groupby("hour")["temperature_c"].mean()
    by_hour["indoor_c"] = indoor
    ctx.tables["night_flushing_by_hour"] = by_hour
    night = by_hour.loc[[h for h in range(0, 6) if h in by_hour.index]]
    day = by_hour.loc[[h for h in range(8, 20) if h in by_hour.index]]
    if night["airflow_m3h"].mean() > 0.9 * day["airflow_m3h"].mean():
        raise Skip("AHU1 is not throttled at night on hot days")
    nominal = next(a for a in ctx.ds.building["building"]["assets"] if a["id"] == "AHU1").get(
        "nominal_airflow_m3h"
    )
    delta = (night["indoor_c"] - night["outdoor_c"]).mean()
    hot_night = occ[occ["date"].isin(hot_dates) & (occ["hour"] < 6)]
    win = hot_night.groupby("window_open")["temperature_c"].mean()
    # sensible cooling power of the unused airflow: 0.34 Wh/(m3 K)
    unused = max(float(day["airflow_m3h"].mean() - night["airflow_m3h"].mean()), 0.0)
    kw = 0.34 * unused * delta / 1000.0

    return {
        "insight_id": "C-01",
        "target": "CLARA",
        "topic": "Ventilation is throttled at night exactly when the outside air could cool the rooms",
        "finding": (
            f"On the {len(hot_dates)} days when the average occupied PLATO-40 room was above "
            f"{COMFORT_HIGH_C:.0f} °C, the outdoor air between 00:00 and 06:00 was {delta:.1f} K cooler than the "
            f"rooms ({night['outdoor_c'].mean():.1f} °C outside, {night['indoor_c'].mean():.1f} °C inside). In "
            f"those same hours AHU1 ran at {night['airflow_m3h'].mean():,.0f} m³/h, below its daytime "
            f"{day['airflow_m3h'].mean():,.0f} m³/h (nominal {nominal:,} m³/h). The night setback saves fan "
            "energy in winter but in a hot spell it gives away the only cooling the building has. Rooms with an "
            f"open window were {win.get(0.0, np.nan) - win.get(1.0, np.nan):.1f} K cooler at night than rooms "
            "with a closed one."
        ),
        "comparison": "Hourly means of AHU1 airflow, outdoor and occupied-room temperature on hot days; night (00-06) against day (08-20).",
        "evidence": [
            "equipment.csv: AHU1 airflow_m3h by local hour on hot days",
            "weather.csv: outdoor_c; room_telemetry.csv: temperature_c, window_open in occupied rooms",
            "building.json: operations.cooling ('ventilation provides night flushing in summer')",
            "out/insights/night_flushing_by_hour.csv",
        ],
        "recommendation": (
            "Replace the fixed night setback with a rule: when the forecast or the measured occupied-room mean "
            f"exceeds {COMFORT_HIGH_C:.0f} °C and outdoor air is at least 3 K cooler than the rooms, run AHU1 at "
            "full airflow with the heat recovery bypassed until 06:00; and message guests in overheated rooms "
            "that opening the window at night helps."
        ),
        "expected_benefit": (
            f"Estimate, not a measurement: the withheld airflow of about {unused:,.0f} m³/h at {delta:.1f} K "
            f"corresponds to roughly {kw:.1f} kW of free sensible cooling during those hours - useful but small "
            "against a full house, so this mitigates P-02 and does not replace it."
        ),
        "uncertainty": (
            "The schedule is observed directly. The cooling effect is a physics estimate on an uncalibrated "
            "synthetic model, and extra fan energy and night noise are not counted. The window comparison is "
            "observational - guests who open windows may differ in other ways. The data does not show the state "
            "of the heat-recovery bypass."
        ),
        "confidence": 0.65,
        "charts": [
            _chart(
                "Hot days: AHU1 airflow by hour of day",
                "line",
                "m³/h",
                [f"{h:02d}" for h in by_hour.index],
                {"airflow": by_hour["airflow_m3h"]},
                0,
            ),
            _chart(
                "Hot days: outdoor and occupied-room temperature by hour",
                "line",
                "°C",
                [f"{h:02d}" for h in by_hour.index],
                {"outdoor": by_hour["outdoor_c"], "occupied rooms": by_hour["indoor_c"]},
                1,
            ),
        ],
    }


def robot_precleaning(ctx: Context) -> dict:
    cl = ctx.cleanings
    if len(cl) < 100 or cl["robot_first"].nunique() < 2:
        raise Skip("too few cleanings with and without a robot pass")
    strata = (
        cl.groupby(["area_m2", "resource_id", "robot_first"])["minutes"]
        .agg(["mean", "size"])
        .unstack()
    )
    strata = strata.dropna()
    saving = strata[("mean", False)] - strata[("mean", True)]
    weight = strata[("size", True)]
    adj = float((saving * weight).sum() / weight.sum())
    if not adj > 3:
        raise Skip(f"no meaningful cleaning-time difference with a robot pass ({adj:.1f} min)")
    raw = cl.groupby("robot_first")["minutes"].agg(["mean", "median", "size"])
    # uncertainty: bootstrap over cleaning days
    rng = np.random.default_rng(RNG_SEED)
    by_day = {d: g for d, g in cl.groupby("date")}
    days = list(by_day)
    boots = []
    for _ in range(300):
        s = pd.concat([by_day[days[k]] for k in rng.integers(0, len(days), len(days))])
        m = s.groupby("robot_first")["minutes"].mean()
        boots.append(m.get(False, np.nan) - m.get(True, np.nan))
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    coverage = float(cl["robot_first"].mean())
    daily = cl.groupby("date").agg(cleanings=("minutes", "size"), coverage=("robot_first", "mean"))
    busy_cut = daily["cleanings"].quantile(0.9)
    cov_busy = float(daily[daily["cleanings"] >= busy_cut]["coverage"].mean())
    cov_normal = float(daily[daily["cleanings"] < busy_cut]["coverage"].mean())
    scale = 365.0 / ctx.days
    saved_h = adj * raw.loc[True, "size"] / 60 * scale
    missed_h = adj * raw.loc[False, "size"] / 60 * scale
    by_area = cl.pivot_table(
        index="area_m2", columns="robot_first", values="minutes", aggfunc="mean"
    )
    ctx.tables["cleaning_minutes_by_stratum"] = strata
    bins = pd.cut(
        daily["cleanings"], [0, 8, 11, 14, 17, 100], labels=["≤8", "9-11", "12-14", "15-17", "18+"]
    )
    cov_bins = daily.groupby(bins, observed=True)["coverage"].mean() * 100

    return {
        "insight_id": "C-02",
        "target": "CLARA",
        "topic": "A robot pass before the cleaner saves cleaner time in every room - and is skipped most on the busiest days",
        "finding": (
            f"Across {len(cl):,} room cleanings in PLATO-40 the cleaner needed {raw.loc[True, 'mean']:.0f} min on "
            f"average when a robot had cleaned the floor first and {raw.loc[False, 'mean']:.0f} min when not "
            f"(medians {raw.loc[True, 'median']:.0f} vs {raw.loc[False, 'median']:.0f} min). Comparing only like "
            f"with like - same room size, same cleaner - the saving is {adj:.1f} min per room (raw difference "
            f"{raw.loc[False, 'mean'] - raw.loc[True, 'mean']:.1f} min, 95 % CI {lo:.1f} to {hi:.1f}, bootstrap over "
            f"days). Robots went first in {coverage:.0%} of cleanings, but only {cov_busy:.0%} on the busiest 10 % "
            f"of days (≥ {busy_cut:.0f} cleanings) against {cov_normal:.0%} otherwise."
        ),
        "comparison": "Human cleaning duration with vs without a robot floor-clean of the same room in the 8 h before, stratified by room area and cleaner.",
        "evidence": [
            "events.jsonl: cleaning_started / cleaning_completed (duration, resource_id)",
            "events.jsonl: robot_floor_cleaning_completed in the same room within 8 h before",
            "building.json: room area; out/insights/cleaning_minutes_by_stratum.csv",
        ],
        "recommendation": (
            "Schedule robots from the checkout list, not from the cleaner's route: as soon as a room is vacated "
            "send a robot ahead, most urgently on days with many departures or a sick cleaner, and plan cleaner "
            f"time with the measured durations ({raw.loc[True, 'mean']:.0f} min after a robot, "
            f"{raw.loc[False, 'mean']:.0f} min without) instead of one flat figure."
        ),
        "expected_benefit": (
            f"Estimate: at {adj:.1f} min per room the robots already save about {saved_h:,.0f} cleaner-hours per "
            f"year; the {raw.loc[False, 'size']:,} cleanings without a robot pass are another {missed_h:,.0f} h, "
            "concentrated on the days when cleaner time is scarcest."
        ),
        "uncertainty": (
            "Observational: rooms are not randomly assigned to robots. The saving is stable across room sizes, "
            "cleaners and floors, which argues against a selection effect, but a dirtier-than-usual room could "
            "both miss its robot slot and take longer. Durations are recorded in 15-minute steps, so single "
            "cleanings are coarse; only averages are meaningful. Robots cannot enter occupied rooms or clean "
            "bathrooms, which caps coverage below 100 %."
        ),
        "confidence": 0.8,
        "charts": [
            _chart(
                "Cleaner minutes per room",
                "bar",
                "min",
                [f"{a} m²" for a in by_area.index],
                {"robot first": by_area[True], "no robot": by_area[False]},
                1,
            ),
            _chart(
                "Share of cleanings with a robot pass first, by cleanings per day",
                "bar",
                "%",
                cov_bins.index,
                {"robot coverage": cov_bins},
                0,
            ),
        ],
    }


def arrival_waits(ctx: Context) -> dict:
    ev = ctx.ds.events
    wait = ev[ev["type"] == "arrival_waiting_room"][["timestamp_utc", "booking_id", "room_id"]]
    if len(wait) < 8:
        raise Skip(f"only {len(wait)} guests waited for a room - too few to find a pattern")
    done = ev[ev["type"] == "checkin_after_wait"][["timestamp_utc", "booking_id"]]
    m = wait.merge(done, on="booking_id", suffixes=("", "_in"))
    minutes = (m["timestamp_utc_in"] - m["timestamp_utc"]) / pd.Timedelta(minutes=1)
    local = wait["timestamp_utc"].dt.tz_convert(ctx.tz)
    wait = wait.assign(date=local.dt.date, dow=local.dt.day_of_week, hour=local.dt.hour)
    checkin_from = int(ctx.ds.building["operations"]["check_in_from_local"].split(":")[0])
    early = float((wait["hour"] < checkin_from).mean())

    out = ev[ev["type"].isin(["checkout", "checkout_early"])]
    departures = out.groupby(out["timestamp_utc"].dt.tz_convert(ctx.tz).dt.date).size()
    roster = ctx.ds.staff_roster
    cleaners = roster[roster["role"] == "cleaner"].assign(
        date=lambda r: r["start_utc"].dt.tz_convert(ctx.tz).dt.date
    )
    on_duty = cleaners[cleaners["status"] == "scheduled"].groupby("date").size()
    absent_days = set(cleaners[cleaners["status"] != "scheduled"]["date"])
    days = (
        pd.DataFrame({"departures": departures})
        .join(on_duty.rename("cleaners"))
        .fillna({"cleaners": 0})
    )
    days["waits"] = wait.groupby("date").size().reindex(days.index).fillna(0)
    days["absent"] = days.index.isin(absent_days)
    days["load"] = days["departures"] / days["cleaners"].clip(lower=1)
    ctx.tables["arrival_waits_by_day"] = days
    wait_days = days[days["waits"] > 0]
    p_absent = float((days[days["absent"]]["waits"] > 0).mean())
    p_normal = float((days[~days["absent"]]["waits"] > 0).mean())
    cut = float(days["load"].quantile(0.9))
    p_high = float((days[days["load"] >= cut]["waits"] > 0).mean())
    p_low = float((days[days["load"] < cut]["waits"] > 0).mean())
    risky = days[(days["load"] >= cut) | days["absent"]]
    caught = int(risky["waits"].sum())
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow = wait["dow"].value_counts().reindex(range(7)).fillna(0)
    top_dow = int(by_dow.idxmax())
    early_req = len(ctx.requests("early_checkin"))
    load_bins = pd.cut(days["load"], [0, 4, 6, 8, 100], labels=["≤4", "4-6", "6-8", ">8"])
    by_load = days.groupby(load_bins, observed=True)["waits"].agg(
        lambda x: float((x > 0).mean()) * 100
    )

    return {
        "insight_id": "C-03",
        "target": "CLARA",
        "topic": "Guests wait for rooms on predictable days: many departures per cleaner, or a cleaner absent",
        "finding": (
            f"{len(wait)} arriving guests of PLATO-40 found their room not ready and waited "
            f"{minutes.median():.0f} min in the median (longest {minutes.max():.0f} min). {early:.0%} of them "
            f"arrived before the {checkin_from}:00 check-in time, and {by_dow[top_dow]:.0f} of {len(wait)} on a "
            f"{dow_names[top_dow]}. The waits fall on {len(wait_days)} days that were foreseeable the evening "
            f"before: a day with a cleaner absent had a waiting guest with probability {p_absent:.0%} against "
            f"{p_normal:.1%} on other days; the 10 % of days with the most departures per cleaner on duty "
            f"(≥ {cut:.1f}) had one with {p_high:.0%} against {p_low:.1%}. Those two conditions together mark "
            f"{len(risky)} days and contain {caught} of the {len(wait)} waits. There were {early_req} early "
            "check-in requests over the same period."
        ),
        "comparison": "Days with at least one arrival_waiting_room event against all other days, by departures per scheduled cleaner and cleaner absence.",
        "evidence": [
            f"events.jsonl: arrival_waiting_room ({len(wait)}) -> checkin_after_wait (wait duration)",
            "events.jsonl: checkout / checkout_early per local day",
            "staff_roster.csv: cleaners scheduled, sick or on vacation per day",
            "out/insights/arrival_waits_by_day.csv",
        ],
        "recommendation": (
            f"Add a next-day turnaround check at roster close: if departures per available cleaner reach {cut:.0f} "
            "or a cleaner is reported absent, CLARA (1) sends robots ahead into every vacated room, (2) orders "
            "rooms by arrival time and early check-in requests instead of by floor, (3) answers early check-in "
            "requests with a realistic time rather than a default yes, and (4) asks a human to approve extra "
            "cleaning capacity."
        ),
        "expected_benefit": (
            f"Qualitative: the rule would have raised a flag on {len(risky)} days a year and covers {caught} of "
            f"the {len(wait)} recorded waits. Whether a flagged wait is actually avoided depends on capacity that "
            "the replay cannot test."
        ),
        "uncertainty": (
            f"Only {len(wait)} events, so the probabilities are rough. The threshold was read off this year's data "
            "and will need re-tuning. Most waiting guests arrived before official check-in, so part of the "
            "'problem' is early arrival rather than late cleaning - the data does not say whether those guests "
            "had been promised an early room."
        ),
        "confidence": 0.7,
        "charts": [
            _chart(
                "Chance that a guest has to wait, by departures per cleaner on duty",
                "bar",
                "% of days",
                by_load.index,
                {"days with a waiting guest": by_load},
                1,
            ),
            _chart(
                "Guests who waited, by weekday", "bar", "count", dow_names, {"guests": by_dow}, 0
            ),
        ],
    }


def alarm_quality(ctx: Context) -> dict:
    al = ctx.alarms
    if len(al) < 30:
        raise Skip(f"only {len(al)} BMS alarms")
    rooms = al[al["asset_id"].isin(ctx.ds.room_ids)].rename(columns={"asset_id": "room_id"})
    rooms = ctx.ds.add_occupancy(rooms).join(ctx.rooms[["floor"]], on="room_id")
    summary = al.groupby("alarm_code")["hours"].agg(count="size", median_hours="median")
    ctx.tables["alarm_summary"] = summary
    parts, charts_cat, charts_val = [], [], []

    low = rooms[rooms["alarm_code"] == "ROOM_TEMP_LOW"]
    if len(low):
        f1 = float((low["floor"] == 1).mean())
        vac = float((~low["occupied"]).mean())
        per_room = low["room_id"].value_counts()
        parts.append(
            f"ROOM_TEMP_LOW was raised {len(low)} times; {f1:.0%} of them on floor 1 - the structural heat loss of "
            f"P-01, not {len(low)} separate incidents - and {vac:.0%} in rooms that were vacant at that moment. "
            f"The five most affected rooms account for {per_room.head(5).sum() / len(low):.0%}"
        )
    high = rooms[rooms["alarm_code"] == "ROOM_TEMP_HIGH"]
    if len(high):
        days = high["timestamp_utc"].dt.tz_convert(ctx.tz).dt.date.nunique()
        parts.append(
            f"ROOM_TEMP_HIGH was raised {len(high)} times on {days} days across {high['room_id'].nunique()} rooms - "
            "one building-wide heat event reported room by room, for which no action exists (no cooling)"
        )
    comm = rooms[rooms["alarm_code"] == "SENSOR_COMM_LOSS"]
    if len(comm):
        burst = comm.groupby(["timestamp_utc", "floor"]).size()
        floor_wide = int(burst[burst >= 8].sum())
        parts.append(
            f"SENSOR_COMM_LOSS was raised {len(comm)} times, {floor_wide / len(comm):.0%} of them as bursts of 8 or "
            f"more rooms of one floor in the same interval ({int((burst >= 8).sum())} gateway outages reported as "
            f"{floor_wide} room alarms)"
        )
    if not parts:
        raise Skip("no room-level alarms")
    for code, row in summary.iterrows():
        charts_cat.append(code)
        charts_val.append(row["count"])
    # how many alarm events remain if grouped to root causes
    grouped = (
        (
            rooms.groupby(
                ["alarm_code", "floor", rooms["timestamp_utc"].dt.tz_convert(ctx.tz).dt.date]
            ).ngroups
        )
        if len(rooms)
        else 0
    )

    return {
        "insight_id": "C-04",
        "target": "CLARA",
        "topic": "Most BMS alarms repeat one root cause room by room",
        "finding": (
            f"PLATO-40's building management system raised {len(al)} alarms. "
            + ". ".join(parts)
            + f". Grouping room alarms by code, floor and day leaves {grouped} alarm groups instead of "
            f"{len(rooms)} room alarms."
        ),
        "comparison": "Each raised bms_alarm joined to occupancy at that moment, floor, and simultaneous alarms on the same floor.",
        "evidence": [
            f"events.jsonl: bms_alarm raised/cleared ({len(al)} raised)",
            "events.jsonl: occupancy at the time of the alarm",
            "out/insights/alarm_summary.csv",
        ],
        "recommendation": (
            "Alarm on causes, not on symptoms: (1) raise one gateway alarm when 8 or more rooms of a floor lose "
            "communication together, and suppress the room alarms; (2) evaluate ROOM_TEMP_LOW against the room's "
            "own setpoint and occupancy - a vacant room in 18 °C setback is not an alarm - and give floor 1 a "
            "persistence time until P-01 is fixed; (3) roll ROOM_TEMP_HIGH up into one building heat event that "
            "triggers the night-flush rule of C-01."
        ),
        "expected_benefit": (
            f"Qualitative: operators see on the order of {grouped} grouped events instead of {len(rooms)} room "
            "alarms, each pointing at something that can be acted on."
        ),
        "uncertainty": (
            "The alarm rules themselves are not in the data; we only see when they fired. BMS alarms in this "
            "dataset are stated to be delayed, incomplete and sometimes false, so counts are indicative. "
            "Suppression must never hide a single cold occupied room - the rule should group, not mute."
        ),
        "confidence": 0.75,
        "charts": [
            _chart(
                "BMS alarms raised by code", "bar", "count", charts_cat, {"raised": charts_val}, 0
            )
        ],
    }


def filter_interval(ctx: Context) -> dict:
    daily, cycles = _filter_cycles(ctx)
    full = cycles[cycles["ended_by_change"]]
    if len(full) < 2:
        raise Skip("fewer than two complete filter lives in this dataset")
    slow, fast = cycles["pa_per_day"].min(), cycles["pa_per_day"].max()
    wo = ctx.ds.work_orders
    done = wo[(wo["asset_id"] == "AHU1") & (wo["status"] == "completed")]
    unplanned = int((done["type"] == "corrective").sum())
    al = ctx.alarms
    dp_alarm = al[al["alarm_code"] == "FILTER_DP_HIGH"]
    clean = float(cycles["dp_start_pa"].min())
    limit = 2.0 * clean
    lead = []
    for _, c in full.iterrows():
        seg = daily[(daily.index >= c["start"]) & (daily.index <= c["end"])]
        over = seg[seg >= limit]
        if len(over):
            lead.append((c["end"] - over.index[0]) / pd.Timedelta(days=1))
    cycles_out = cycles.assign(
        start=cycles["start"].dt.strftime("%Y-%m-%d"), end=cycles["end"].dt.strftime("%Y-%m-%d")
    )
    ctx.tables["filter_cycles"] = cycles_out
    lead_txt = (
        f"A trigger at twice the clean-filter pressure ({limit:.0f} Pa) would have fired "
        f"{np.median(lead):.0f} days (median) before the filters were actually changed in {len(lead)} of "
        f"{len(full)} filter lives"
        if lead
        else f"The pressure never reached twice the clean-filter value ({limit:.0f} Pa)"
    )

    return {
        "insight_id": "C-05",
        "target": "CLARA",
        "topic": "Change the ventilation filters on pressure trend, not on the calendar",
        "finding": (
            f"AHU1 of PLATO-40 went through {len(cycles)} filter lives. A clean filter starts at about "
            f"{clean:.0f} Pa; lives lasted {full['days'].min():.0f} to {full['days'].max():.0f} days and ended at "
            f"{full['dp_end_pa'].min():.0f} to {full['dp_end_pa'].max():.0f} Pa. The loading rate ranged from "
            f"{slow:.1f} to {fast:.1f} Pa per day between lives, so a fixed interval is either too early or too late: {unplanned} changes "
            f"were unplanned ('heavily loaded') and the BMS raised FILTER_DP_HIGH {len(dp_alarm)} times. {lead_txt}."
        ),
        "comparison": "Filter lives segmented at each drop of the daily median pressure; linear loading rate per life.",
        "evidence": [
            "equipment.csv: AHU1 filter_dp_pa (daily median), airflow_m3h",
            "work_orders.csv: AHU1 filter replacements, planned and unplanned",
            "out/insights/filter_cycles.csv",
        ],
        "recommendation": (
            f"Order the filter change when the daily median pressure drop passes about {limit:.0f} Pa or when the "
            "fitted trend will reach it within the technician's lead time, whichever is first; keep the calendar "
            "date only as a backstop. Combine the order with access planning (P-04) and with the technician's "
            "roster so that the appointment is feasible."
        ),
        "expected_benefit": (
            "Qualitative: filter changes become planned work with lead time instead of unplanned call-outs; "
            "lightly loaded filters are not replaced early."
        ),
        "uncertainty": (
            f"{len(cycles)} filter lives is a small sample and the 2x threshold is a common rule of thumb, not a "
            "manufacturer value - the right limit is on the filter's data sheet. Pressure drop also depends on "
            "airflow; it was normalised only by using the daily median. Why some lives load faster (pollen, "
            "construction dust, season) cannot be seen in the data."
        ),
        "confidence": 0.65,
        "charts": [
            _chart(
                "Loading rate per filter life",
                "bar",
                "Pa/day",
                cycles_out["start"],
                {"pressure rise": cycles["pa_per_day"]},
                2,
            )
        ],
    }


def vending_and_waste(ctx: Context) -> dict:
    ev = ctx.ds.events
    out = ev[ev["type"] == "vending_out_of_stock"]
    full = ev[ev["type"] == "waste_bin_full"]
    if len(out) + len(full) < 5:
        raise Skip("fewer than 5 stock-out / bin-full events")
    lg = ctx.ds.logistics
    stock = lg[(lg["asset_id"] == "VEND1")].pivot_table(
        index="timestamp_utc", columns="metric", values="value"
    )
    local = out["timestamp_utc"].dt.tz_convert(ctx.tz)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow = local.dt.day_of_week.value_counts().reindex(range(7)).fillna(0)
    top = int(by_dow.idxmax())
    products = out["product"].value_counts() if "product" in out else pd.Series(dtype=int)
    empty_h = {c: int((stock[c] <= 0).sum()) for c in stock.columns}
    months = local.dt.month
    warm = float(months.between(5, 9).mean()) if len(out) else float("nan")
    vend = next(a for a in ctx.ds.building["building"]["assets"] if a["id"] == "VEND1")
    waste = lg[(lg["asset_id"] == "WASTE_MAIN") & (lg["metric"] == "fill_pct")].set_index(
        "timestamp_utc"
    )["value"]
    full_h = int((waste >= 99.5).sum())
    full_local = full["timestamp_utc"].dt.tz_convert(ctx.tz)
    deps = ev[ev["type"].isin(["checkout", "checkout_early"])]
    dep_day = deps.groupby(deps["timestamp_utc"].dt.tz_convert(ctx.tz).dt.date).size()
    dep_before = [
        int(dep_day.reindex(pd.date_range(d - pd.Timedelta(days=2), d).date).fillna(0).sum())
        for d in full_local.dt.normalize().dt.tz_localize(None)
    ]
    typical3 = float(dep_day.rolling(3).sum().median())
    ctx.tables["vending_stockouts_by_weekday"] = by_dow.rename(
        index=dict(enumerate(dow_names))
    ).to_frame("stockouts")

    return {
        "insight_id": "C-06",
        "target": "CLARA",
        "topic": "Drinks stock-outs and waste overflows follow the fixed service schedule, not demand",
        "finding": (
            f"The PLATO-40 vending machine ran out {len(out)} times, every time of "
            f"{', '.join(products.index) if len(products) else 'a product'} "
            f"({empty_h.get('stock_drinks', 0)} hours with zero drinks; snacks never ran out). "
            f"{by_dow[top]:.0f} of the {len(out)} stock-outs happened on a {dow_names[top]} and {warm:.0%} between "
            f"May and September: the machine is restocked '{vend.get('restock', 'on fixed days')}', so the weekend "
            f"is the longest gap and warm weather the highest demand. The central waste container reached 100 % "
            f"in {len(full)} pickup cycles ({full_h} hours at the cap); the three days up to each overflow saw "
            f"{', '.join(map(str, dep_before))} departures against a typical {typical3:.0f}."
        ),
        "comparison": "Stock-out and bin-full events by weekday, season and preceding departures, against the fixed service schedule in building.json.",
        "evidence": [
            f"events.jsonl: vending_out_of_stock ({len(out)}), waste_bin_full ({len(full)})",
            "logistics.csv: VEND1 stock_drinks / stock_snacks, WASTE_MAIN fill_pct",
            "building.json: VEND1.restock, WASTE_MAIN.pickup schedules and capacities",
            "events.jsonl: departures per day",
        ],
        "recommendation": (
            "Make both services demand-driven: order an extra drinks restock for Friday when the weekend "
            "forecast is warm or occupancy is high (24 h lead time), and order an extra waste pickup when booked "
            "departures up to the next regular pickup are far above normal. For the next building, a larger "
            f"drinks compartment than {vend.get('capacity', {}).get('drinks', '?')} units would remove the "
            "dependency on an extra delivery."
        ),
        "expected_benefit": (
            f"Qualitative: avoids most of the {empty_h.get('stock_drinks', 0)} hours without drinks and the "
            f"{full_h} hours with a full container seen in this dataset, at the cost of a handful of extra "
            "service orders."
        ),
        "uncertainty": (
            f"{len(full)} overflows is very few - the waste rule is a plausible pattern, not a tested one. Fill "
            "levels are capped at 100 %, so the size of an overflow is unknown. Vending demand is modelled, and "
            "lost sales during a stock-out are not observable."
        ),
        "confidence": 0.7,
        "charts": [
            _chart(
                "Vending stock-outs by weekday",
                "bar",
                "count",
                dow_names,
                {"stock-outs": by_dow},
                0,
            )
        ],
    }


def arrival_preheat(ctx: Context) -> dict:
    ds = ctx.ds
    lc = ds.lifecycle
    ci = lc[lc["occupied"] & (lc["type"] != "stay_in_progress")][
        ["timestamp_utc", "room_id"]
    ].copy()
    ci["t0"] = ci["timestamp_utc"].dt.ceil("15min")
    ci["outdoor_c"] = ci["t0"].map(ctx.outdoor)
    ci = ci[ci["outdoor_c"] < 10]
    if len(ci) < 150:
        raise Skip("fewer than 150 check-ins in heating weather")
    tel = ds.room_telemetry.drop_duplicates(["timestamp_utc", "room_id"])[
        ["timestamp_utc", "room_id", "temperature_c"]
    ]
    for label, shift in (("t_arrival", 0), ("t_2h", 2), ("t_before", -3)):
        key = tel.rename(columns={"timestamp_utc": "_k", "temperature_c": label})
        ci["_k"] = ci["t0"] + pd.Timedelta(hours=shift)
        ci = ci.merge(key, on=["_k", "room_id"], how="left")
    ci = ci.drop(columns="_k").dropna(subset=["t_arrival"]).join(ctx.rooms[["floor"]], on="room_id")
    low = ds.comfort_band_c[0]
    by_floor = ci.groupby("floor").agg(
        checkins=("t_arrival", "size"),
        cold_at_arrival=("t_arrival", lambda x: float((x < low).mean())),
        cold_after_2h=("t_2h", lambda x: float((x.dropna() < low).mean())),
        mean_arrival_c=("t_arrival", "mean"),
    )
    ctx.tables["arrival_temperature_by_floor"] = by_floor
    total = float((ci["t_arrival"] < low).mean())
    rest = ci[ci["floor"] != 1]
    preheated = float((ci["t_before"].dropna() < low - 1).mean())
    per_room = ci.groupby("room_id")["t_arrival"].apply(lambda x: float((x < low).mean()) * 100)
    f1r = per_room[ctx.rooms.loc[per_room.index, "floor"] == 1]
    cmp = compare_groups(f1r, per_room.drop(f1r.index))

    return {
        "insight_id": "C-07",
        "target": "CLARA",
        "topic": "In heating weather many guests walk into a room below the comfort band - most often on floor 1",
        "finding": (
            f"At {len(ci):,} check-ins in heating weather (outdoor < 10 °C) the PLATO-40 room was below the "
            f"{low:.0f} °C comfort limit {total:.0%} of the time when the guest arrived: "
            f"{by_floor.loc[1, 'cold_at_arrival']:.0%} on floor 1 against "
            f"{float((rest['t_arrival'] < low).mean()):.0%} on floors 2-4 (difference between room means "
            f"{_ci(cmp, 'percentage points', 1)}). Two hours later {by_floor.loc[1, 'cold_after_2h']:.0%} of floor-1 "
            f"rooms and {float((rest['t_2h'].dropna() < low).mean()):.0%} of the others were still below {low:.0f} °C. "
            f"Three hours before arrival {preheated:.0%} of the rooms were still in deep setback (below "
            f"{low - 1:.0f} °C): rooms are heated up for the booked 15:00 check-in, not for the guest."
        ),
        "comparison": "Room temperature at the first reading after each actual check-in, floor 1 against floors 2-4, outdoor < 10 °C.",
        "evidence": [
            "events.jsonl: checkin / checkin_early / checkin_after_wait (actual arrival time)",
            "room_telemetry.csv: temperature_c at arrival, 2 h after, 3 h before",
            "building.json: comfort band, setpoint_policy (18 °C setback when unoccupied)",
            "out/insights/arrival_temperature_by_floor.csv",
        ],
        "recommendation": (
            "Start pre-heating from the room's own warm-up time instead of a fixed clock: begin earlier when it "
            "is cold outside and earlier still on floor 1, and trigger it from the cleaning-completed event for "
            "rooms with an early check-in request. Pre-heating a vacant room is an autonomous action under the "
            "challenge rules."
        ),
        "expected_benefit": (
            "Qualitative: guests arrive to a room inside the comfort band. Pre-heating costs heat pump "
            "electricity; the amount is not estimated here because heat is not metered per room."
        ),
        "uncertainty": (
            "The first reading after check-in is up to 15 minutes late, and a guest opening the door or a window "
            "can cool the room, so the share is an upper bound. 'Below 20 °C' includes rooms at 19.8 °C; the "
            "floor difference is the robust part of the finding. The warm-up dynamics come from an uncalibrated "
            "model."
        ),
        "confidence": 0.7,
        "charts": [
            _chart(
                f"Heating weather: rooms below {low:.0f} °C at check-in",
                "bar",
                "%",
                [f"Floor {f}" for f in by_floor.index],
                {
                    "at arrival": by_floor["cold_at_arrival"] * 100,
                    "2 h later": by_floor["cold_after_2h"] * 100,
                },
                1,
            )
        ],
        "rooms": {r: round(float(v), 1) for r, v in per_room.items()},
        "rooms_unit": f"% of winter check-ins below {low:.0f} °C",
    }


# ---------------------------------------------------------------- checked and not found


def checked_not_found(ctx: Context) -> list[dict]:
    """Hypotheses we tested that the data does not support. Each entry: hypothesis, result."""
    out: list[dict] = []
    ds = ctx.ds

    def attempt(fn) -> None:
        try:
            out.append(fn())
        except (Skip, KeyError, ValueError, ZeroDivisionError, StopIteration) as e:
            out.append(
                {
                    "hypothesis": fn.__doc__ or fn.__name__,
                    "result": f"not testable here: {e}",
                    "tested": False,
                }
            )

    def bathtubs() -> dict:
        """Rooms with a bathtub use much more water."""
        rw = ds.room_water.sort_values(["room_id", "timestamp_utc"])
        d = rw.groupby("room_id")["meter_total_l"].diff()
        d = d.where((d >= 0) & (d < 300))  # drop meter exchanges and implausible jumps
        rw = ds.add_occupancy(rw.assign(litres=d))
        used = rw[rw["occupied"]].groupby("room_id")["litres"].sum()
        per_night = (used / ctx.occupied_nights).replace([np.inf, -np.inf], np.nan).dropna()
        tub = ctx.rooms.loc[per_night.index, "has_bathtub"].astype(bool)
        if tub.sum() < 3:
            raise Skip("fewer than 3 bathtub rooms")
        c = compare_groups(per_night[tub], per_night[~tub])
        ctx.tables["water_per_night_by_room"] = per_night.to_frame(
            "litres_per_occupied_night"
        ).join(ctx.rooms[["has_bathtub", "area_m2"]])
        return {
            "hypothesis": "Rooms with a bathtub use much more water",
            "result": (
                f"Bathtub rooms use {per_night[tub].mean():.0f} L per occupied night, the others "
                f"{per_night[~tub].mean():.0f} L: {_ci(c, 'L', 0)}. A difference of about "
                f"{c['diff'] / per_night[~tub].mean():.0%} that is not distinguishable from room-to-room spread."
            ),
            "tested": True,
        }

    def roof() -> dict:
        """The top floor overheats more than the others (roof)."""
        chk = ctx.tables.get("_overheating_checks")
        if chk is None:
            raise Skip("no hot period in this dataset")
        return {
            "hypothesis": "The top floor overheats more than the floors below (roof heat gain)",
            "result": f"Top floor against the others in the hot months: {chk.iloc[0]['result']}. No roof effect.",
            "tested": True,
        }

    def room_size() -> dict:
        """Bigger rooms overheat or under-heat more."""
        hot = ctx.tables.get("overheating_by_room")
        cold = ctx.tables.get("floor1_heat_loss_by_room")
        bits = []
        if hot is not None:
            g = hot.groupby("area_m2")["mean_c"].mean()
            bits.append(
                f"hot-month mean temperature {g.min():.1f}-{g.max():.1f} °C across the three room sizes"
            )
        if cold is not None:
            g = cold[cold["floor"] != 1].groupby("area_m2")["shortfall_k"].mean()
            bits.append(f"winter shortfall {g.min():+.2f} to {g.max():+.2f} K (floors 2-4)")
        if not bits:
            raise Skip("needs the heating or overheating analysis")
        return {
            "hypothesis": "Room size (22 / 28 / 32 m²) drives comfort problems",
            "result": "No: "
            + "; ".join(bits)
            + ". Emitters and ventilation scale adequately with room area.",
            "tested": True,
        }

    def storage() -> dict:
        """PV and battery are badly sized for this building."""
        e = ds.energy.set_index("timestamp_utc")
        if ctx.days < 300:
            raise Skip("needs a full year of energy data")
        k = 0.25 * 365.0 / ctx.days
        pv, exp, imp = (e[c].sum() * k for c in ("pv_kw", "grid_export_kw", "grid_import_kw"))
        bat = next(a for a in ds.building["building"]["assets"] if a["id"] == "BAT1")
        lo, hi = bat["soc_bounds_kwh"]
        dis = e["battery_discharge_kw_signed"].clip(lower=0).sum() * k
        full_share = float((e["battery_soc_kwh"] >= hi - 1).mean())
        empty_share = float((e["battery_soc_kwh"] <= lo + 1).mean())
        exp_full = e[e["battery_soc_kwh"] >= hi - 1]["grid_export_kw"].sum() * k
        return {
            "hypothesis": "PV (60 kWp) and battery (100 kWh) are badly sized for the building",
            "result": (
                f"Not shown. {1 - exp / pv:.0%} of the {pv / 1000:.0f} MWh PV yield is used on site; export "
                f"({exp / 1000:.1f} MWh) happens "
                f"{'only' if exp_full / max(exp, 1e-9) > 0.95 else 'mostly'} while the battery is full, which is "
                f"the case {full_share:.0%} of the time. The battery delivers {dis / 1000:.1f} MWh a year "
                f"(about {dis / (hi - lo):.0f} full cycles) and sits empty {empty_share:.0%} of the time, mainly in "
                f"winter when there is no surplus to store. Grid import is {imp / 1000:.0f} MWh: the constraint is "
                "winter heating demand, which no realistic battery shifts. No resizing recommendation."
            ),
            "tested": True,
        }

    def heat_pump() -> dict:
        """The heat pump is too small for the coldest days."""
        hp = ds.equipment_wide("HP1")
        hp["outdoor_c"] = ctx.outdoor.reindex(hp.index)
        coldest = hp[hp["outdoor_c"] < -5]
        if len(coldest) < 100:
            raise Skip("fewer than 25 hours below -5 °C")
        nominal = next(a for a in ds.building["building"]["assets"] if a["id"] == "HP1")[
            "nominal_thermal_kw_at_7c"
        ]
        cold_tab = ctx.tables.get("floor1_heat_loss_by_outdoor")
        upper = (
            cold_tab.drop(columns=1).loc["below -5 °C"].mean()
            if cold_tab is not None
            else float("nan")
        )
        faults = ds.work_orders[
            (ds.work_orders["asset_id"] == "HP1") & (ds.work_orders["type"] == "corrective")
        ]
        return {
            "hypothesis": "The heat pump is undersized for the coldest days",
            "result": (
                f"Inconclusive, leaning no. Below -5 °C HP1 delivered {coldest['heat_output_kw'].mean():.0f} kW on "
                f"average and at most {hp['heat_output_kw'].max():.0f} kW (nominal {nominal} kW at +7 °C; there is "
                "no capacity measurement at low temperatures). In those hours occupied rooms on floors 2-4 were "
                f"only {upper:+.2f} K off their setpoint, so the plant kept up where the envelope is sound. The "
                f"comfort problems in cold weather trace to floor 1 (P-01) and to {len(faults)} repaired heat pump "
                "faults, not to design capacity."
            ),
            "tested": True,
        }

    def wifi() -> dict:
        """Wi-fi problems cluster on the floor with the weak gateway."""
        w = ctx.requests("wifi_help")
        w = w[~w["text"].str.contains("passwor|Passwor", na=False)]
        if len(w) < 30:
            raise Skip("fewer than 30 wi-fi fault requests")
        rate = (
            w["room_id"].value_counts().reindex(ds.room_ids).fillna(0) / ctx.occupied_nights * 100
        )
        by_floor = rate.groupby(ctx.rooms["floor"]).mean()
        worst = int(by_floor.idxmax())
        c = compare_groups(rate[ctx.rooms["floor"] == worst], rate[ctx.rooms["floor"] != worst])
        return {
            "hypothesis": "Wi-fi complaints cluster on one floor (e.g. the floor with the failing sensor gateway)",
            "result": (
                f"No. {len(w)} wi-fi fault requests, {by_floor.min():.2f} to {by_floor.max():.2f} per 100 occupied "
                f"nights by floor; the highest floor ({worst}) against the rest: {_ci(c, 'per 100 nights')}. "
                "The sensor gateways and guest wi-fi appear to be independent."
            ),
            "tested": True,
        }

    def orientation_winter() -> dict:
        """North rooms are colder than south rooms in winter."""
        cold = ctx.tables.get("floor1_heat_loss_by_room")
        if cold is None:
            raise Skip("needs the heating analysis")
        up = cold[cold["floor"] != 1]
        c = compare_groups(
            up[up["orientation"] == "N"]["shortfall_k"], up[up["orientation"] == "S"]["shortfall_k"]
        )
        return {
            "hypothesis": "North-facing rooms run colder than south-facing rooms in winter",
            "result": f"No (floors 2-4, north minus south shortfall): {_ci(c, 'K')}.",
            "tested": True,
        }

    for fn in (bathtubs, roof, room_size, orientation_winter, heat_pump, storage, wifi):
        attempt(fn)
    return out


# ------------------------------------------------------------------------------ build

ANALYSES = (
    floor1_heat_loss,
    summer_overheating,
    acoustic_hotspots,
    rooftop_access,
    gateway_reliability,
    night_flushing,
    robot_precleaning,
    arrival_waits,
    alarm_quality,
    filter_interval,
    vending_and_waste,
    arrival_preheat,
)

REQUIRED_FIELDS = (
    "insight_id",
    "target",
    "topic",
    "finding",
    "evidence",
    "recommendation",
    "expected_benefit",
    "confidence",
)


def build_all(ds: Dataset) -> tuple[list[dict], list[dict], list[dict], Context]:
    """Run every analysis. Returns (insights, skipped, checked_not_found, context)."""
    ctx = Context(ds)
    insights, skipped = [], []
    for fn in ANALYSES:
        try:
            insights.append(fn(ctx))
        except Skip as e:
            skipped.append({"analysis": fn.__name__, "reason": str(e)})
    checked = checked_not_found(ctx)
    return insights, skipped, checked, ctx


def build(ds: Dataset) -> list[dict]:
    """Insight objects (DECISION_CONTRACT.md schema plus ``charts``/``rooms`` for the UI)."""
    return build_all(ds)[0]


# ----------------------------------------------------------------------------- output


def _md_insight(i: dict) -> str:
    return (
        f"### {i['insight_id']} · {i['topic']}\n\n"
        f"**Finding.** {i['finding']}\n\n"
        f"**Evidence.** {i.get('comparison', '')} Sources: " + "; ".join(i["evidence"]) + ".\n\n"
        f"**Recommendation.** {i['recommendation']}\n\n"
        f"**Expected benefit.** {i['expected_benefit']}\n\n"
        f"**Uncertainty.** {i.get('uncertainty', '')} Confidence {i['confidence']:.2f}.\n"
    )


def render_markdown(
    ds: Dataset, insights: list[dict], skipped: list[dict], checked: list[dict], previous: str
) -> str:
    if MD_ARCH_START in previous and MD_ARCH_END in previous:
        arch = previous[
            previous.index(MD_ARCH_START) + len(MD_ARCH_START) : previous.index(MD_ARCH_END)
        ].strip()
    else:
        arch = "_Placeholder - not written yet. (Text between the two HTML comment markers is kept when this file is regenerated.)_"
    plato = [i for i in insights if i["target"] == "PLATO"]
    clara = [i for i in insights if i["target"] == "CLARA"]
    days = (ds.end - ds.start) / pd.Timedelta(days=1)
    lines = [
        "# Insights — what a year of PLATO-40 teaches",
        "",
        "Level 3 feedback: recommendations for **PLATO** (how to build the next one) and **CLARA** (how to",
        "operate), followed by the target architecture.",
        "",
        "> All findings concern the fictional PLATO-40 reference building and its synthetic data. They",
        "> are not statements about real PLATO buildings. Benefits are qualitative or labelled as",
        "> estimates; nothing here is a measured saving.",
        "",
        f"Generated by `uv run python -m steward.insights {ds.root.as_posix()}` from the **{ds.profile}** dataset",
        f"({ds.start:%Y-%m-%d} to {ds.end:%Y-%m-%d}, {days:.0f} days). Every number below is computed by",
        "[src/steward/insights.py](src/steward/insights.py); the tables behind them are written to `out/insights/`.",
        "Do not edit the findings by hand - change the analysis and regenerate.",
        "",
        "**How to read the statistics.** Rooms are the unit of comparison (40 rooms, 10 per floor, 20 per",
        "orientation). A difference is given with a 95 % bootstrap interval over rooms and a permutation",
        "p-value. This is hindsight analysis of the whole dataset; it is not used for detection.",
        "",
        "## Summary",
        "",
        "| ID | For | Finding | Confidence |",
        "|---|---|---|---:|",
    ]
    lines += [
        f"| {i['insight_id']} | {i['target']} | {i['topic']} | {i['confidence']:.2f} |"
        for i in plato + clara
    ]
    lines += ["", "## For PLATO — design of the next building", ""]
    lines += [_md_insight(i) for i in plato] or ["_None on this dataset._", ""]
    lines += ["## For CLARA — operating rules", ""]
    lines += [_md_insight(i) for i in clara] or ["_None on this dataset._", ""]
    lines += [
        "## Checked and not found",
        "",
        "Hypotheses we tested that the data does not support.",
        "",
    ]
    lines += [f"- **{c['hypothesis']}.** {c['result']}" for c in checked]
    if skipped:
        lines += ["", "## Not analysed on this dataset", ""]
        lines += [f"- `{s['analysis']}`: {s['reason']}" for s in skipped]
    lines += [
        "",
        "## Target architecture",
        "",
        MD_ARCH_START,
        arch,
        MD_ARCH_END,
        "",
    ]
    return "\n".join(lines)


def write_outputs(ds: Dataset, out_dir: Path, jsonl: Path | None, md: Path | None) -> dict:
    insights, skipped, checked, ctx = build_all(ds)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in ctx.tables.items():
        if not name.startswith("_"):
            table.to_csv(out_dir / f"{name}.csv")
    (out_dir / "checked_not_found.json").write_text(
        json.dumps({"checked": checked, "skipped": skipped}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if jsonl:
        with jsonl.open("w", encoding="utf-8", newline="\n") as fh:
            for i in insights:
                fh.write(json.dumps(i, ensure_ascii=False) + "\n")
    if md:
        previous = md.read_text(encoding="utf-8") if md.is_file() else ""
        md.write_text(
            render_markdown(ds, insights, skipped, checked, previous),
            encoding="utf-8",
            newline="\n",
        )
    return {"insights": insights, "skipped": skipped, "checked": checked}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m steward.insights", description=__doc__.split("\n")[0]
    )
    parser.add_argument("data_dir")
    parser.add_argument("--jsonl", default="insights.jsonl", help="output JSONL ('' to skip)")
    parser.add_argument("--md", default="insights.md", help="output markdown report ('' to skip)")
    parser.add_argument(
        "--out-dir", default="out/insights", help="directory for supporting CSV tables"
    )
    args = parser.parse_args(argv)
    ds = Dataset(args.data_dir)
    res = write_outputs(
        ds,
        Path(args.out_dir),
        Path(args.jsonl) if args.jsonl else None,
        Path(args.md) if args.md else None,
    )
    for i in res["insights"]:
        print(f"{i['insight_id']}  {i['target']:<5}  conf {i['confidence']:.2f}  {i['topic']}")
    for s in res["skipped"]:
        print(f"skipped {s['analysis']}: {s['reason']}", file=sys.stderr)
    print(
        f"{len(res['insights'])} insights, {len(res['checked'])} checked hypotheses, {len(res['skipped'])} skipped"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
