"""Typed, lazy access to one PLATO-40 dataset directory (quickstart, dev or holdout).

Conventions enforced here so detectors do not have to re-learn them:

* Every ``*_utc`` column is a tz-aware UTC timestamp. Convert to ``ds.local_tz`` for
  anything operational (check-in 15:00, check-out 11:00, shifts) - mind DST.
* A blank field is a MISSING value and stays NaN/NaT. Never fill it with zero.
* Meter columns (``meter_total_l``) are cumulative and can restart after a meter exchange.
* Some tables carry a time of availability (``created_utc``, ``published_utc``,
  ``issued_utc``, ``status_known_utc``). A decision at time t may only use rows known at t.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REQUIRED_FILES = (
    "building.json",
    "manifest.json",
    "bookings.csv",
    "access_log.csv",
    "room_telemetry.csv",
    "room_water.csv",
    "water_main.csv",
    "energy.csv",
    "weather.csv",
    "weather_forecast.csv",
    "tariff.csv",
    "equipment.csv",
    "logistics.csv",
    "staff_roster.csv",
    "work_orders.csv",
    "guest_requests.csv",
    "events.jsonl",
)


class DatasetError(Exception):
    """The data directory is missing or incomplete."""


class Dataset:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise DatasetError(f"Data directory not found: {self.root}")
        missing = [f for f in REQUIRED_FILES if not (self.root / f).is_file()]
        if missing:
            raise DatasetError(f"{self.root} is missing dataset files: {', '.join(missing)}")

    def __repr__(self) -> str:
        return f"Dataset({self.root})"

    # ------------------------------------------------------------------ metadata

    @cached_property
    def building(self) -> dict:
        return json.loads((self.root / "building.json").read_text(encoding="utf-8"))

    @cached_property
    def manifest(self) -> dict:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    @property
    def profile(self) -> str:
        return self.manifest.get("profile", "unknown")

    @cached_property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.manifest["start_utc"])

    @cached_property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(self.manifest["end_utc"])

    @cached_property
    def local_tz(self) -> ZoneInfo:
        return ZoneInfo(self.building["time"]["local_timezone"])

    @cached_property
    def room_ids(self) -> list[str]:
        return [r["id"] for r in self.building["building"]["rooms"]]

    @cached_property
    def rooms(self) -> pd.DataFrame:
        """Static room properties (floor, area, orientation, bathtub, ...), indexed by room id."""
        return pd.DataFrame(self.building["building"]["rooms"]).set_index("id")

    @cached_property
    def asset_ids(self) -> list[str]:
        return [a["id"] for a in self.building["building"]["assets"]]

    @cached_property
    def resource_ids(self) -> list[str]:
        return [r["resource_id"] for r in self.building["staff"]["resources"]]

    @cached_property
    def comfort_band_c(self) -> tuple[float, float]:
        low, high = self.building["constraints"]["occupied_comfort_c"]
        return float(low), float(high)

    @cached_property
    def known_ids(self) -> set[str]:
        """Every ID that may appear in a decision's ``affected_assets``."""
        b = self.building["building"]
        ids = set(self.room_ids) | set(self.asset_ids) | set(self.resource_ids)
        ids |= {g["id"] for g in b["gateways"]}
        ids |= {r["water_meter_id"] for r in b["rooms"] if r.get("water_meter_id")}
        ids |= {"MAIN_ENTRANCE", "BUILDING"}
        return ids

    # -------------------------------------------------------------------- tables

    def _csv(self, name: str, dtype: dict[str, str] | None = None) -> pd.DataFrame:
        df = pd.read_csv(self.root / name, dtype=dtype)
        for col in df.columns:
            if col.endswith("_utc"):
                df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
        return df

    @cached_property
    def bookings(self) -> pd.DataFrame:
        return self._csv("bookings.csv")

    @cached_property
    def bookings_expected(self) -> pd.DataFrame:
        """Bookings created in the replay, regardless of their final status.

        The dataset has no cancellation timestamp. Detectors must filter by
        ``created_utc <= decision_time`` and treat overlapping bookings as uncertain.
        """
        return self.bookings

    @cached_property
    def access_log(self) -> pd.DataFrame:
        return self._csv("access_log.csv")

    @cached_property
    def room_telemetry(self) -> pd.DataFrame:
        return self._csv("room_telemetry.csv")

    @cached_property
    def room_water(self) -> pd.DataFrame:
        return self._csv("room_water.csv")

    @cached_property
    def water_main(self) -> pd.DataFrame:
        return self._csv("water_main.csv")

    @cached_property
    def energy(self) -> pd.DataFrame:
        return self._csv("energy.csv")

    @cached_property
    def weather(self) -> pd.DataFrame:
        return self._csv("weather.csv")

    @cached_property
    def weather_forecast(self) -> pd.DataFrame:
        return self._csv("weather_forecast.csv")

    @cached_property
    def tariff(self) -> pd.DataFrame:
        return self._csv("tariff.csv")

    @cached_property
    def equipment(self) -> pd.DataFrame:
        """Long format. ``value`` is text because HP1 ``status`` is a code; see ``equipment_wide``."""
        return self._csv("equipment.csv", dtype={"value": "str"})

    def equipment_wide(self, asset_id: str) -> pd.DataFrame:
        """One asset pivoted to a timestamp-indexed frame; numeric metrics become floats."""
        df = self.equipment[self.equipment["asset_id"] == asset_id]
        wide = df.pivot_table(
            index="timestamp_utc", columns="metric", values="value", aggfunc="last"
        )
        for col in wide.columns:
            if col != "status":
                wide[col] = pd.to_numeric(wide[col], errors="coerce")
        wide.columns.name = None
        return wide

    @cached_property
    def logistics(self) -> pd.DataFrame:
        return self._csv("logistics.csv", dtype={"value": "float64"})

    @cached_property
    def staff_roster(self) -> pd.DataFrame:
        return self._csv("staff_roster.csv")

    @cached_property
    def work_orders(self) -> pd.DataFrame:
        return self._csv("work_orders.csv")

    @cached_property
    def guest_requests(self) -> pd.DataFrame:
        return self._csv("guest_requests.csv")

    @cached_property
    def events(self) -> pd.DataFrame:
        """All operational events and BMS alarms; type-specific fields are NaN where absent."""
        with (self.root / "events.jsonl").open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        df = pd.DataFrame(rows)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, format="ISO8601")
        return df

    # ----------------------------------------------------------------- occupancy

    _CHECKIN_TYPES = ("stay_in_progress", "checkin", "checkin_early", "checkin_after_wait")
    _CHECKOUT_TYPES = ("checkout", "checkout_early")

    @cached_property
    def lifecycle(self) -> pd.DataFrame:
        """Actual check-in / check-out moments per room, time-ordered, with ``occupied`` after each."""
        ev = self.events
        lc = ev[ev["type"].isin(self._CHECKIN_TYPES + self._CHECKOUT_TYPES)]
        lc = lc[["timestamp_utc", "room_id", "booking_id", "type", "guests"]].copy()
        lc["occupied"] = lc["type"].isin(self._CHECKIN_TYPES)
        return lc.sort_values(["timestamp_utc", "room_id"], kind="stable").reset_index(drop=True)

    @cached_property
    def stays(self) -> pd.DataFrame:
        """One row per actual stay: room, booking, start, end (NaT if still in house at the end).

        HINDSIGHT TABLE: ``end_utc`` is only known once the guest has left. Use it for analysis
        and insights; inside a detector use ``add_occupancy``, which is causal.
        """
        lc = self.lifecycle
        starts = lc[lc["occupied"]].rename(
            columns={"timestamp_utc": "start_utc", "type": "start_type"}
        )
        ends = lc[~lc["occupied"]].rename(columns={"timestamp_utc": "end_utc", "type": "end_type"})
        stays = starts[["room_id", "booking_id", "start_utc", "start_type", "guests"]].merge(
            ends[["room_id", "booking_id", "end_utc", "end_type"]],
            on=["room_id", "booking_id"],
            how="left",
        )
        return stays.sort_values(["start_utc", "room_id"], kind="stable").reset_index(drop=True)

    def add_occupancy(self, df: pd.DataFrame, time_col: str = "timestamp_utc") -> pd.DataFrame:
        """Add ``occupied`` (bool) and ``stay_booking_id`` to a frame with ``room_id`` and a time column.

        Causal: the state at t follows from the last check-in/check-out event at or before t,
        so it is exactly what the building knew at t. Rooms with no event yet count as vacant.
        """
        lc = self.lifecycle[["timestamp_utc", "room_id", "booking_id", "occupied"]].rename(
            columns={"timestamp_utc": "_lc_time", "booking_id": "stay_booking_id"}
        )
        order = df[time_col].argsort(kind="stable")
        merged = pd.merge_asof(
            df.iloc[order],
            lc.sort_values("_lc_time"),
            left_on=time_col,
            right_on="_lc_time",
            by="room_id",
        )
        merged.index = df.index[order]
        merged["occupied"] = merged["occupied"].eq(True)
        merged.loc[~merged["occupied"], "stay_booking_id"] = pd.NA
        return merged.drop(columns="_lc_time").loc[df.index]

    # ------------------------------------------------------------------- helpers

    def known_absence(self, resource_id: str, at: pd.Timestamp) -> str | None:
        """Absence status (sick/vacation/out_of_service) covering ``at`` that was already known at ``at``."""
        r = self.staff_roster
        hit = r[
            (r["resource_id"] == resource_id)
            & (r["start_utc"] <= at)
            & (r["end_utc"] > at)
            & (r["status"] != "scheduled")
            & (r["status_known_utc"] <= at)
        ]
        return None if hit.empty else str(hit.iloc[0]["status"])

    def on_shift(self, resource_id: str, at: pd.Timestamp) -> bool:
        """True if a ``scheduled`` roster shift covers ``at`` (ONCALL_1 has no roster rows)."""
        r = self.staff_roster
        hit = r[
            (r["resource_id"] == resource_id)
            & (r["start_utc"] <= at)
            & (r["end_utc"] > at)
            & ((r["status"] == "scheduled") | (r["status_known_utc"] > at))
        ]
        return not hit.empty
