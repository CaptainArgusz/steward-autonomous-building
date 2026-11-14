"""Building & energy optimisation (Level 2): a day-ahead battery plan.

When tomorrow's hourly prices are published, plan when ``BAT1`` should charge and discharge
so the building buys less electricity in expensive hours. Heating is not touched, so occupied
rooms keep their setpoints and the comfort band is never traded for cost (pre-heating was
considered and left out: little heat fits into a 20-23 C band and the saving is unproven).

Only what is known at the publication time is used:

* prices: ``tariff.csv`` rows with ``published_utc`` <= now;
* building load: same-hour average of the last 7 days, with the heating part re-estimated
  from the latest outdoor-temperature forecast (a straight line of HVAC kW against measured
  outdoor temperature over the last 14 days);
* PV: same-hour average of the last 7 days (the dataset has no PV forecast);
* battery limits from ``building.json`` (SoC bounds, charge/discharge kW, round-trip efficiency).

The plan is a small dynamic programme over battery state of charge (1 kWh steps) that
minimises the cost of grid import. It must end the day with at least the charge it started
with, so it cannot "save" money by draining the battery. Export is valued at zero because the
data has no feed-in price.

The plan is compared with the battery's recorded routine (its average hourly power over the
last 7 days) under the same forecast. A decision is emitted only when the plan beats that
routine by at least ``MIN_SAVING_EUR`` and ``MIN_SAVING_SHARE`` of the day's cost; ordinary days
stay quiet. Every number is a SIMULATED estimate on forecasts, never a measured saving. While
the heat pump reports a fault, S03 (heating_comfort) owns energy planning and this stays quiet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from steward.contract import Decision, make_event_id, utc_z
from steward.dataset import Dataset

ISSUE = "energy_optimization"
BATTERY = "BAT1"
PV = "PV1"
PLANT = "HP1"

LOAD_DAYS = 7
HVAC_FIT_DAYS = 14
MIN_FIT_HOURS = 48
MIN_PRICES = 20  # a day-ahead publication covers 23-25 hours
STEP_KWH = 1.0
MIN_SAVING_EUR = 8.0  # alert budget: about one plan a week on the dev year, not a fitted constant
MIN_SAVING_SHARE = 0.10  # and at least 10 % of the day's simulated grid cost
HEALTHY_STATUS = {"running", "standby", "defrost"}
FAULT_LOOKBACK = pd.Timedelta(hours=48)


def _battery_spec(ds: Dataset) -> dict:
    a = next((x for x in ds.building["building"]["assets"] if x["id"] == BATTERY), None)
    if a is None:
        return {}
    lo, hi = a.get(
        "soc_bounds_kwh", [0.1 * a.get("usable_kwh", 100), 0.95 * a.get("usable_kwh", 100)]
    )
    rte = float(a.get("round_trip_efficiency", 0.9))
    return {
        "lo": float(lo),
        "hi": float(hi),
        "charge_kw": float(a.get("max_charge_kw", 0)),
        "discharge_kw": float(a.get("max_discharge_kw", 0)),
        "eta": rte**0.5,  # split the round-trip loss evenly between charging and discharging
    }


def plan_battery(
    price: np.ndarray, net: np.ndarray, start_kwh: float, spec: dict
) -> tuple[np.ndarray, float]:
    """Cheapest hourly SoC path. Returns (soc at the start and after each hour, grid cost in EUR).

    ``net`` is forecast load minus PV in kW for each hour (negative = PV surplus).
    """
    states = np.arange(spec["lo"], spec["hi"] + 1e-9, STEP_KWH)
    n = len(states)
    delta = states[None, :] - states[:, None]  # from i (row) to j (column)
    ac_in = np.where(delta > 0, delta / spec["eta"], 0.0)
    delivered = np.where(delta < 0, -delta * spec["eta"], 0.0)
    feasible = (ac_in <= spec["charge_kw"] + 1e-9) & (delivered <= spec["discharge_kw"] + 1e-9)

    start = int(np.argmin(np.abs(states - start_kwh)))
    value = np.where(states >= states[start] - 1e-9, 0.0, np.inf)  # end no emptier than start
    choice = np.zeros((len(price), n), dtype=int)
    for h in range(len(price) - 1, -1, -1):
        imp = np.maximum(0.0, net[h] + ac_in - delivered)
        total = np.where(feasible, price[h] * imp, np.inf) + value[None, :]
        choice[h] = np.argmin(total, axis=1)
        value = total[np.arange(n), choice[h]]
    path, i = [states[start]], start
    for h in range(len(price)):
        i = choice[h, i]
        path.append(states[i])
    return np.array(path), float(value[start])


def routine_cost(
    price: np.ndarray, net: np.ndarray, start_kwh: float, power_kw: np.ndarray, spec: dict
) -> tuple[np.ndarray, float]:
    """Cost of following a fixed hourly battery power profile (+ = discharge), within limits."""
    soc, cost, path = start_kwh, 0.0, []
    for p, n, kw in zip(price, net, power_kw, strict=True):
        if kw >= 0:
            out = min(kw, spec["discharge_kw"], (soc - spec["lo"]) * spec["eta"])
            soc -= out / spec["eta"]
            imp = max(0.0, n - out)
        else:
            ac = min(-kw, spec["charge_kw"], (spec["hi"] - soc) / spec["eta"])
            soc += ac * spec["eta"]
            imp = max(0.0, n + ac)
        cost += p * imp
        path.append(soc)
    return np.array(path), cost


class _Forecaster:
    def __init__(self, ds: Dataset):
        tz = ds.local_tz
        e = ds.energy.dropna(subset=["timestamp_utc"]).drop_duplicates("timestamp_utc", keep="last")
        e = e.set_index("timestamp_utc").sort_index()
        hourly = (
            e[
                [
                    "building_load_kw",
                    "hvac_electric_kw",
                    "pv_kw",
                    "battery_discharge_kw_signed",
                    "battery_soc_kwh",
                ]
            ]
            .resample("h")
            .mean()
        )
        hourly["hour"] = hourly.index.tz_convert(tz).hour
        self.hourly = hourly
        w = ds.weather.dropna(subset=["outdoor_c"]).drop_duplicates("timestamp_utc", keep="last")
        self.outdoor = w.set_index("timestamp_utc")["outdoor_c"].sort_index().resample("h").mean()
        f = ds.weather_forecast.dropna(subset=["outdoor_c_forecast"])
        self.forecast = f.sort_values(["issued_utc", "target_utc"])
        self.tz = tz

    def _past(self, at: pd.Timestamp, days: int) -> pd.DataFrame:
        h = self.hourly
        # an hourly mean covers [hour, hour+1h): only complete hours before ``at``
        return h[
            (h.index >= at - pd.Timedelta(days=days)) & (h.index + pd.Timedelta(hours=1) <= at)
        ]

    def predict(self, at: pd.Timestamp, hours: pd.DatetimeIndex) -> dict | None:
        past = self._past(at, LOAD_DAYS)
        if past["building_load_kw"].count() < 24 * 3:
            return None
        local_hours = hours.tz_convert(self.tz).hour
        by_hour = past.groupby("hour").mean(numeric_only=True)
        if not set(local_hours) <= set(by_hour.index):
            return None
        base = by_hour.reindex(local_hours)
        load = base["building_load_kw"].to_numpy()
        hvac_avg = base["hvac_electric_kw"].to_numpy()
        pv = base["pv_kw"].fillna(0.0).to_numpy()
        routine = base["battery_discharge_kw_signed"].fillna(0.0).to_numpy()

        # heating part from the temperature forecast, when there is enough to fit and forecast
        fit = (
            self._past(at, HVAC_FIT_DAYS)[["hvac_electric_kw"]]
            .join(self.outdoor.rename("t"))
            .dropna()
        )
        temp = self._temperature(at, hours)
        adjusted = False
        if len(fit) >= MIN_FIT_HOURS and fit["t"].std() > 0.5 and temp is not None:
            slope, intercept = np.polyfit(fit["t"], fit["hvac_electric_kw"], 1)
            hvac_fc = np.clip(intercept + slope * temp, 0.0, None)
            ok = ~np.isnan(hvac_fc)
            load = np.where(ok, load - hvac_avg + hvac_fc, load)
            adjusted = bool(ok.any())
        if np.isnan(load).any():
            return None
        soc_hist = past[past.index.tz_convert(self.tz).hour == local_hours[0]][
            "battery_soc_kwh"
        ].dropna()
        return {
            "load": load,
            "pv": pv,
            "routine": routine,
            "temp": temp,
            "adjusted": adjusted,
            "start_soc": float(soc_hist.median()) if len(soc_hist) else None,
        }

    def _temperature(self, at: pd.Timestamp, hours: pd.DatetimeIndex) -> np.ndarray | None:
        f = self.forecast[self.forecast["issued_utc"] <= at]
        if f.empty:
            return None
        latest = f[f["issued_utc"] == f["issued_utc"].max()].set_index("target_utc")[
            "outdoor_c_forecast"
        ]
        latest = latest[~latest.index.duplicated(keep="last")]
        temp = latest.reindex(hours).to_numpy(dtype=float)
        return None if np.isnan(temp).all() else temp


def _plant_fault_known(ds: Dataset, at: pd.Timestamp) -> bool:
    eq = ds.equipment
    s = eq[(eq["asset_id"] == PLANT) & (eq["metric"] == "status")]
    s = s[(s["timestamp_utc"] <= at) & (s["timestamp_utc"] > at - FAULT_LOOKBACK)]
    return bool((~s["value"].isin(HEALTHY_STATUS) & s["value"].notna()).any())


def _windows(hours: pd.DatetimeIndex, kw: np.ndarray, tz, sign: int) -> list[str]:
    """Human-readable local-time windows where the plan charges (sign<0) or discharges (sign>0)."""
    out, start, total = [], None, 0.0
    for i, (h, p) in enumerate(zip(hours, kw, strict=True)):
        on = p * sign > 1.0
        if on and start is None:
            start, total = h, 0.0
        if on:
            total += abs(p)
        last = i == len(hours) - 1
        if start is not None and (not on or last):
            end = h + pd.Timedelta(hours=1) if on and last else h
            out.append(
                f"{start.tz_convert(tz):%H:%M}-{end.tz_convert(tz):%H:%M} (~{total:.0f} kWh)"
            )
            start = None
    return out


def detect(ds: Dataset) -> list[Decision]:
    spec = _battery_spec(ds)
    if not spec or spec["charge_kw"] <= 0 or spec["discharge_kw"] <= 0:
        return []
    tariff = ds.tariff.dropna(subset=["price_eur_kwh", "published_utc"])
    tariff = tariff.drop_duplicates("timestamp_utc", keep="last").sort_values("timestamp_utc")
    fc = _Forecaster(ds)
    tz = ds.local_tz
    decisions: list[Decision] = []

    for published, day in tariff.groupby("published_utc", sort=True):
        if len(day) < MIN_PRICES:
            continue
        at = published.ceil("15min")
        if not (ds.start <= at < ds.end):
            continue
        day = day[day["timestamp_utc"] > at]
        hours = pd.DatetimeIndex(day["timestamp_utc"])
        if (
            len(hours) < MIN_PRICES
            or (hours.to_series().diff().dropna() != pd.Timedelta(hours=1)).any()
        ):
            continue
        if _plant_fault_known(ds, at):
            continue  # S03 plans energy comfort-first while the heat pump is impaired
        f = fc.predict(at, hours)
        if f is None or f["start_soc"] is None:
            continue
        price = day["price_eur_kwh"].to_numpy(dtype=float)
        net = f["load"] - f["pv"]
        start = min(max(f["start_soc"], spec["lo"]), spec["hi"])

        path, cost = plan_battery(price, net, start, spec)
        _, base_cost = routine_cost(price, net, start, f["routine"], spec)
        idle_cost = float((price * np.maximum(net, 0.0)).sum())
        saving = base_cost - cost
        if saving < MIN_SAVING_EUR or saving < MIN_SAVING_SHARE * base_cost:
            continue

        dsoc = np.diff(path)
        kw = np.where(
            dsoc < 0, -dsoc * spec["eta"], -dsoc / spec["eta"]
        )  # + discharge, - charge (AC side)
        charge = _windows(hours, kw, tz, -1)
        discharge = _windows(hours, kw, tz, +1)
        local_day = hours[0].tz_convert(tz)
        peak_i, cheap_i = int(np.argmax(price)), int(np.argmin(price))
        temp = f["temp"]
        decisions.append(
            Decision(
                event_id=make_event_id(ISSUE, BATTERY, at, "optimize_energy"),
                episode_id=f"{ISSUE}:{BATTERY}:{local_day:%Y%m%d}",
                timestamp_utc=at,
                affected_assets=[BATTERY, PV],
                issue_type=ISSUE,
                action="optimize_energy",
                priority="low",
                reason=(
                    f"Day-ahead prices for {local_day:%Y-%m-%d} range {price.min():.3f}-{price.max():.3f} EUR/kWh "
                    f"(cheapest {hours[cheap_i].tz_convert(tz):%H:%M}, peak {hours[peak_i].tz_convert(tz):%H:%M} local). "
                    f"Charge {BATTERY} {', '.join(charge) or 'not at all'}; discharge {', '.join(discharge) or 'not at all'}. "
                    f"Simulated grid cost {cost:.2f} EUR vs {base_cost:.2f} EUR with the battery's recent routine "
                    f"({saving:.2f} EUR less) and {idle_cost:.2f} EUR with no battery. Heating setpoints are unchanged, "
                    "so occupied rooms stay in the comfort band."
                ),
                requires_human_approval=False,
                evidence=[
                    f"tariff.csv:published {utc_z(published)}: {len(hours)} hourly prices from {utc_z(hours[0])}",
                    f"energy.csv:same-hour means of the {LOAD_DAYS} days before {utc_z(at)}: "
                    f"load {f['load'].mean():.1f} kW avg, PV {f['pv'].sum():.0f} kWh/day",
                    (
                        f"weather_forecast.csv:issued<= {utc_z(at)}: {np.nanmin(temp):.1f} to {np.nanmax(temp):.1f} C; "
                        "heating load re-estimated from it"
                        if f["adjusted"] and temp is not None
                        else "weather_forecast.csv: not enough history to adjust heating load; same-hour means used"
                    ),
                    f"energy.csv:battery_soc_kwh median {start:.0f} kWh at the start hour over the last {LOAD_DAYS} days",
                    f"building.json:{BATTERY} {spec['lo']:.0f}-{spec['hi']:.0f} kWh, "
                    f"{spec['charge_kw']:.0f}/{spec['discharge_kw']:.0f} kW, round trip {spec['eta'] ** 2:.2f}",
                ],
                expected_outcome=(
                    f"Simulated estimate on forecasts: about {saving:.2f} EUR lower grid cost than the recent routine, "
                    "ending the day with at least the starting charge. No saving is measured; export is valued at zero."
                ),
                verification_plan=(
                    "battery_discharge_kw_signed follows the charge/discharge windows; grid_import_kw is lower in the "
                    "discharge hours than on comparable days; battery_soc_kwh stays within its bounds; occupied rooms "
                    "stay in the comfort band. If load or PV deviates strongly from the forecast, re-plan."
                ),
                confidence=0.5 if f["adjusted"] else 0.4,
            )
        )
    return decisions
