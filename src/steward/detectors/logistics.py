"""Cleaning & logistics (Level 2): central waste container, vending machine, floor bins.

Three independent parts, each replayed in time order on what was known at that moment:

1. ``WASTE_MAIN`` - order an extra pickup when the container is projected to reach 100 %
   before the next regular pickup and the ~24 h lead time still allows it.
2. ``VEND1`` - order an extra restock when a product is projected to run out before the next
   regular restock.
3. Floor bins ``WASTE_F*`` - on a day when a cleaner is known to be absent, a robot carries the
   floor bins that will need emptying to the central container, so housekeeping keeps that
   time for rooms. ``building.json`` describes this as possible but not used in operations.

Service days, times, lead times and the robot carry time are read from ``building.json``.

Forecasts (evaluated on the dev year, one order per service cycle):

* Container: the trailing 14-day container growth rate. The previous cycle-trend projection
  ordered 12 extra pickups for 3 cycles that reached 100 % (2 preceded); this one orders 3
  (2 preceded). Weighting in booked guest-hours made the container forecast worse
  (mean error 11.3 -> 13.9 points), so it is not used. Most container growth arrives in the
  hour housekeeping empties a floor bin, which is why a short trend extrapolates badly.
* Vending: 0.75 x trailing consumption rate + 0.25 x consumption per guest-hour times the
  guest-hours already booked until the restock. In both halves of the year this caught more
  stock-outs than either term alone without more false orders.
"""

from __future__ import annotations

import re
from datetime import timedelta

import numpy as np
import pandas as pd

from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "logistics"
CONTAINER = "WASTE_MAIN"
VENDING = "VEND1"

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEFAULT_PICKUP = ((1, 4), (7, 0))  # used only if building.json cannot be parsed
DEFAULT_RESTOCK = ((0, 3), (10, 0))
DEFAULT_LEAD = pd.Timedelta(hours=24)
DEFAULT_CARRY_MIN = 20.0

# Backwards-compatible names (tests import them).
PICKUP_WEEKDAYS, (PICKUP_HOUR_LOCAL, _) = DEFAULT_PICKUP
LEAD_TIME = DEFAULT_LEAD

TRAILING = pd.Timedelta(days=14)
MIN_HISTORY_H = 72  # three days of hourly readings before a rate is trusted
MIN_FILL_PCT = 60.0  # do not speculate on a container that is not at least this full
W_TRAILING_VENDING = 0.75  # see module docstring

BIN_PLAN_HOUR_LOCAL = 7  # housekeeping shift start: the day's roster is known
BIN_DUE_HOUR_LOCAL = 13  # housekeeping empties floor bins in the afternoon
DEFAULT_BIN_EMPTY_PCT = 40.0  # until enough emptying events have been seen
MIN_EMPTY_EVENTS = 5
CONTAINER_ROOM_PCT = 85.0  # do not carry bins into an almost full container


# ------------------------------------------------------------------- schedules


def parse_schedule(text: str | None, default):
    """``"municipal service Tue + Fri 07:00 local; ... (~24 h lead time)"`` -> (days, (h, m), lead)."""
    days, hm = default
    lead = DEFAULT_LEAD
    if text:
        found = [_DAYS[d.lower()] for d in re.findall(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", text)]
        clock = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        hours = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*h\b", text)
        if found:
            days = tuple(sorted(set(found)))
        if clock:
            hm = (int(clock.group(1)), int(clock.group(2)))
        if hours:
            lead = pd.Timedelta(hours=float(hours.group(1)))
    return days, hm, lead


def _asset(ds: Dataset, asset_id: str) -> dict:
    return next((a for a in ds.building["building"]["assets"] if a["id"] == asset_id), {})


def next_regular_pickup(at: pd.Timestamp, tz, days=PICKUP_WEEKDAYS, hm=(PICKUP_HOUR_LOCAL, 0)):
    """First regular service strictly after ``at`` (UTC in, UTC out)."""
    local = at.tz_convert(tz)
    for offset in range(8):
        day = (local + timedelta(days=offset)).normalize()
        service = day.replace(hour=hm[0], minute=hm[1])
        if day.weekday() in days and service > local:
            return service.tz_convert("UTC")
    raise AssertionError("unreachable: a service weekday occurs within any 8 days")


# -------------------------------------------------------------------- helpers


def _hourly(ds: Dataset, asset_id: str, metric: str) -> pd.Series:
    lg = ds.logistics
    s = (
        lg[(lg["asset_id"] == asset_id) & (lg["metric"] == metric)]
        .dropna(subset=["value"])  # missing reading = unknown, not empty
        .drop_duplicates("timestamp_utc", keep="last")
        .set_index("timestamp_utc")["value"]
        .sort_index()
    )
    return s


def _steps(s: pd.Series) -> pd.Series:
    """Change between consecutive readings exactly one hour apart (NaN otherwise)."""
    gap = s.index.to_series().diff()
    return s.diff().where(gap == pd.Timedelta(hours=1))


class _Guests:
    """Guests in house per hour (causal) and guest-hours booked ahead (known bookings only)."""

    def __init__(self, ds: Dataset, index: pd.DatetimeIndex):
        st = ds.stays
        occ = np.zeros(len(index))
        t = index.values
        for s in st.itertuples():
            end = s.end_utc if pd.notna(s.end_utc) else index[-1] + pd.Timedelta(hours=1)
            g = s.guests if pd.notna(s.guests) else 1
            occ[(t >= s.start_utc.to_datetime64()) & (t < end.to_datetime64())] += g
        self.occupied = pd.Series(occ, index=index)
        b = ds.bookings.dropna(subset=["created_utc", "arrival_utc", "departure_utc"])
        self._created = b["created_utc"].values.astype("datetime64[ns]").astype(np.int64)
        self._arr = b["arrival_utc"].values.astype("datetime64[ns]").astype(np.int64)
        self._dep = b["departure_utc"].values.astype("datetime64[ns]").astype(np.int64)
        self._g = b["guest_count"].fillna(1).to_numpy(dtype=float)

    def booked_hours(self, at: pd.Timestamp, until: pd.Timestamp) -> float:
        a, u = at.value, until.value
        m = (self._created <= a) & (self._arr < u) & (self._dep > a)
        span = np.minimum(self._dep[m], u) - np.maximum(self._arr[m], a)
        return float((span / 3.6e12 * self._g[m]).sum())

    def past_hours(self, at: pd.Timestamp) -> float:
        occ = self.occupied
        return float(occ[(occ.index > at - TRAILING) & (occ.index <= at)].sum())


# ------------------------------------------------------------ waste container


def _container(ds: Dataset) -> list[Decision]:
    days, hm, lead = parse_schedule(_asset(ds, CONTAINER).get("pickup"), DEFAULT_PICKUP)
    fill = _hourly(ds, CONTAINER, "fill_pct")
    if fill.empty:
        return []
    growth = _steps(fill).where(lambda x: x >= 0)  # drops are pickups

    decisions: list[Decision] = []
    ordered: set[pd.Timestamp] = set()
    for at, value in fill.items():
        if value < MIN_FILL_PCT:
            continue
        pickup = next_regular_pickup(at, ds.local_tz, days, hm)
        wait = pickup - at
        if wait <= lead or pickup in ordered:
            continue
        past = growth[(growth.index > at - TRAILING) & (growth.index <= at)].dropna()
        if len(past) < MIN_HISTORY_H:
            continue
        rate = float(past.mean())
        hours = wait / pd.Timedelta(hours=1)
        projected = value + rate * hours
        if projected < 100.0:
            continue
        ordered.add(pickup)
        decisions.append(
            Decision(
                event_id=make_event_id(ISSUE, CONTAINER, at, "order_service"),
                episode_id=f"{ISSUE}:{CONTAINER}:{pickup.tz_convert(ds.local_tz):%Y%m%d}",
                timestamp_utc=at,
                affected_assets=[CONTAINER],
                issue_type=ISSUE,
                action="order_service",
                priority="high" if value >= 90 else "medium",
                reason=(
                    f"Central waste container at {value:.0f} %; over the last 14 days it grew "
                    f"{rate:.2f} %/h on average, so it is projected at {projected:.0f} % by the next "
                    f"regular pickup in {hours:.0f} h. Order an extra pickup "
                    f"(~{lead / pd.Timedelta(hours=1):.0f} h lead time)."
                ),
                requires_human_approval=False,
                evidence=[
                    f"logistics.csv:{CONTAINER}:fill_pct={value:.1f}@{utc_z(at)}",
                    f"logistics.csv:{CONTAINER}:mean hourly growth {rate:.2f} % over {len(past)} readings before {utc_z(at)}",
                    f"building.json:{CONTAINER}.pickup next regular {utc_z(pickup)}",
                ],
                expected_outcome="Simulated: an extra pickup within the lead time keeps the container below 100 %.",
                verification_plan=(
                    "A waste_pickup event and a fill_pct drop appear before the regular pickup; "
                    "escalate to a human if fill_pct exceeds 95 % before then."
                ),
                confidence=round(min(0.85, 0.5 + (projected - 100.0) / 100.0), 2),
            )
        )
    return decisions


# ------------------------------------------------------------------- vending


def _vending(ds: Dataset) -> list[Decision]:
    days, hm, lead = parse_schedule(_asset(ds, VENDING).get("restock"), DEFAULT_RESTOCK)
    metrics = sorted(
        m for m in ds.logistics.loc[ds.logistics["asset_id"] == VENDING, "metric"].unique()
    )
    decisions: list[Decision] = []
    guests: _Guests | None = None
    for metric in metrics:
        stock = _hourly(ds, VENDING, metric)
        if stock.empty:
            continue
        if guests is None:
            guests = _Guests(ds, pd.date_range(ds.start, ds.end, freq="h", inclusive="left"))
        product = metric.removeprefix("stock_")
        used = (-_steps(stock)).where(lambda x: x >= 0)  # increases are restocks
        ordered: set[pd.Timestamp] = set()
        for at, units in stock.items():
            restock = next_regular_pickup(at, ds.local_tz, days, hm)
            wait = restock - at
            if wait <= lead or restock in ordered:
                continue
            past = used[(used.index > at - TRAILING) & (used.index <= at)].dropna()
            if len(past) < MIN_HISTORY_H:
                continue
            hours = wait / pd.Timedelta(hours=1)
            rate = float(past.mean())
            trailing_need = rate * hours
            past_gh = guests.past_hours(at)
            booked_gh = guests.booked_hours(at, restock)
            per_guest = float(past.sum()) / past_gh if past_gh > 0 else None
            if per_guest is None:
                need = trailing_need
            else:
                need = (
                    W_TRAILING_VENDING * trailing_need
                    + (1 - W_TRAILING_VENDING) * per_guest * booked_gh
                )
            projected = units - need
            if projected > 0:
                continue
            ordered.add(restock)
            empty_in_h = units / (need / hours) if need > 0 else hours
            decisions.append(
                Decision(
                    event_id=make_event_id(ISSUE, f"{VENDING}-{product}", at, "order_service"),
                    episode_id=f"{ISSUE}:{VENDING}:{product}:{restock.tz_convert(ds.local_tz):%Y%m%d}",
                    timestamp_utc=at,
                    affected_assets=[VENDING],
                    issue_type=ISSUE,
                    action="order_service",
                    priority="medium" if units > 0 else "high",
                    reason=(
                        f"Vending machine has {units:.0f} {product} left. Expected use until the next regular "
                        f"restock in {hours:.0f} h is ~{need:.0f} units (trailing rate {rate:.2f}/h"
                        + (f", {booked_gh:.0f} guest-hours booked" if per_guest is not None else "")
                        + f"), so it would run out in ~{empty_in_h:.0f} h. Order an extra restock "
                        f"(~{lead / pd.Timedelta(hours=1):.0f} h lead time)."
                    ),
                    requires_human_approval=False,
                    evidence=[
                        f"logistics.csv:{VENDING}:{metric}={units:.0f}@{utc_z(at)}",
                        f"logistics.csv:{VENDING}:{metric} mean hourly use {rate:.2f} over {len(past)} readings",
                        f"bookings.csv:created<= {utc_z(at)}: {booked_gh:.0f} guest-hours until {utc_z(restock)}",
                        f"building.json:{VENDING}.restock next regular {utc_z(restock)}",
                    ],
                    expected_outcome=f"Simulated: an extra restock arrives before {product} run out. No delivery is confirmed by the data.",
                    verification_plan=(
                        f"A vending_restocked event and a {metric} increase appear before {utc_z(restock)}; "
                        f"a vending_out_of_stock event for {product} means the order came too late."
                    ),
                    confidence=0.6 if per_guest is not None else 0.5,
                )
            )
    return decisions


# ---------------------------------------------------------- floor bins (robot)


def _carry_minutes(ds: Dataset) -> float:
    note = ds.building.get("staff", {}).get("notes", {}).get("robot", "")
    m = re.search(r"carry[^(]*\(\s*~?\s*(\d+)\s*min", note)
    return float(m.group(1)) if m else DEFAULT_CARRY_MIN


def _floor_bins(ds: Dataset) -> list[Decision]:
    bins = sorted(
        a["id"] for a in ds.building["building"]["assets"] if a.get("type") == "waste_bin"
    )
    if not bins:
        return []
    tz = ds.local_tz
    carry = pd.Timedelta(minutes=_carry_minutes(ds))
    levels = {b: _hourly(ds, b, "fill_pct") for b in bins}
    container = _hourly(ds, CONTAINER, "fill_pct")
    roster = ds.staff_roster
    cleaners = roster[roster["role"] == "cleaner"]
    robots = roster[roster["role"] == "robot"]
    ev = ds.events
    emptied = ev[ev["type"] == "floor_bin_emptied"].sort_values("timestamp_utc")
    robot_ev = ev[
        ev["type"].isin(["robot_floor_cleaning_started", "robot_floor_cleaning_completed"])
    ]

    def level_before(at: pd.Timestamp, s: pd.Series) -> float | None:
        prior = s[s.index <= at]
        return None if prior.empty else float(prior.iloc[-1])

    def empty_threshold(at: pd.Timestamp) -> float:
        """Median level at which housekeeping emptied a bin, from events before ``at``."""
        seen = []
        for e in emptied[emptied["timestamp_utc"] < at].tail(60).itertuples():
            s = levels.get(e.asset_id)
            if s is None:
                continue
            v = level_before(e.timestamp_utc - pd.Timedelta(minutes=1), s)
            if v is not None:
                seen.append(v)
        return float(np.median(seen)) if len(seen) >= MIN_EMPTY_EVENTS else DEFAULT_BIN_EMPTY_PCT

    def morning_rise(b: str, at: pd.Timestamp, due: pd.Timestamp) -> float:
        """Median rise of this bin between plan time and due time on the previous 14 days.

        Bins fill in bursts while rooms are cleaned, so an all-day average underestimates the
        morning rise. Only past days are used.
        """
        s = levels[b]
        rises = []
        for k in range(1, 15):
            a0, d0 = at - pd.Timedelta(days=k), due - pd.Timedelta(days=k)
            v0, v1 = level_before(a0, s), level_before(d0, s)
            if v0 is not None and v1 is not None and v1 >= v0:
                rises.append(v1 - v0)
        return float(np.median(rises)) if len(rises) >= 5 else 0.0

    def robot_busy(robot: str, at: pd.Timestamp) -> bool:
        r = robot_ev[(robot_ev["timestamp_utc"] <= at)]
        if "resource_id" in r.columns:
            r = r[r["resource_id"] == robot]
        if r.empty:
            return False
        return r.iloc[-1]["type"] == "robot_floor_cleaning_started"

    decisions: list[Decision] = []
    first = ds.start.tz_convert(tz).normalize()
    last = ds.end.tz_convert(tz).normalize()
    for day in pd.date_range(first, last, freq="D"):
        at = day.replace(hour=BIN_PLAN_HOUR_LOCAL).tz_convert("UTC")
        if not (ds.start <= at < ds.end):
            continue
        day_end = (day + pd.Timedelta(days=1)).tz_convert("UTC")
        today = cleaners[
            (cleaners["start_utc"] < day_end) & (cleaners["end_utc"] > at - pd.Timedelta(hours=2))
        ]
        absent = today[(today["status"] != "scheduled") & (today["status_known_utc"] <= at)]
        if absent.empty:
            continue
        main = level_before(at, container)
        if main is None or main >= CONTAINER_ROOM_PCT:
            continue

        due = day.replace(hour=BIN_DUE_HOUR_LOCAL).tz_convert("UTC")
        threshold = empty_threshold(at)
        chosen: list[tuple[str, float, float]] = []
        for b in bins:
            now = level_before(at, levels[b])
            if now is None:
                continue
            expected = now + morning_rise(b, at, due)
            if expected >= threshold:
                chosen.append((b, now, expected))
        if not chosen:
            continue

        job_end = at + carry * len(chosen)
        robot = None
        for r in robots[(robots["start_utc"] <= at) & (robots["end_utc"] >= job_end)].itertuples():
            if r.status != "scheduled" and r.status_known_utc <= at:
                continue
            if robot_busy(r.resource_id, at):
                continue
            robot = r.resource_id
            break
        if robot is None:
            continue

        names = ", ".join(b for b, _, _ in chosen)
        add_pct = sum(now for _, now, _ in chosen)
        decisions.append(
            Decision(
                event_id=make_event_id(ISSUE, "FLOOR_BINS", at, "dispatch_robot"),
                episode_id=f"{ISSUE}:FLOOR_BINS:{day:%Y%m%d}",
                timestamp_utc=at,
                affected_assets=[robot] + [b for b, _, _ in chosen] + [CONTAINER],
                issue_type=ISSUE,
                action="dispatch_robot",
                priority="low",
                reason=(
                    f"{len(absent)} cleaner(s) known absent today ({', '.join(absent['resource_id'])}), so housekeeping "
                    f"time is short. {robot} carries {names} to {CONTAINER} now (~{carry / pd.Timedelta(minutes=1):.0f} min each, "
                    f"done by {utc_z(job_end)}), before housekeeping would normally empty them "
                    f"(bins are usually emptied at ~{threshold:.0f} %). This frees cleaner time for rooms."
                ),
                requires_human_approval=False,
                resource_id=robot,
                evidence=[
                    *(
                        f"staff_roster.csv:{r.resource_id} {r.status} (known {utc_z(r.status_known_utc)})"
                        for r in absent.itertuples()
                    ),
                    *(
                        f"logistics.csv:{b}:fill_pct={now:.1f}@{utc_z(at)}, expected {exp:.0f} % by {utc_z(due)}"
                        for b, now, exp in chosen
                    ),
                    f"logistics.csv:{CONTAINER}:fill_pct={main:.1f}@{utc_z(at)} (room for ~{add_pct:.0f} % of floor-bin waste)",
                    f"staff_roster.csv:{robot} on shift, no robot job in progress at {utc_z(at)}",
                    "building.json:staff.notes.robot can carry a floor waste bin to the central container",
                ],
                expected_outcome=(
                    "Simulated: floor bins emptied by the robot, so cleaners skip that task today. "
                    "The robot never enters a room; nothing about room readiness changes."
                ),
                verification_plan=(
                    f"fill_pct of {names} drops and {CONTAINER} rises before {utc_z(job_end)}; "
                    "if a robot cleaning job for a room is requested meanwhile, the room job takes priority."
                ),
                confidence=0.6,
            )
        )
    return decisions


def detect(ds: Dataset) -> list[Decision]:
    return _container(ds) + _vending(ds) + _floor_bins(ds)
