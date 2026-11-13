"""Building & energy optimisation (Level 2). NOT IMPLEMENTED YET.

Problem: shift load using day-ahead prices, PV, battery and weather forecasts without
leaving the comfort band. Any saving we state is a SIMULATED estimate and must say so.

Signals: tariff (published_utc), energy (PV, battery SoC; positive battery = discharging),
weather_forecast (issued_utc), BAT1 limits in building.json (10-95 kWh, 40 kW, 0.9 RTE).
Typical action: optimize_energy.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
