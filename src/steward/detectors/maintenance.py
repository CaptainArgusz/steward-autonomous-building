"""S04 The Maintenance Cascade (Level 2). NOT IMPLEMENTED YET.

Problem: AHU filter degradation, missing sensor readings and an unavailable technician.
The core skill is telling a data gap (gateway outage, SENSOR_COMM_LOSS) from real plant
degradation, then organising a service that is actually feasible.

Signals: equipment AHU1 (filter_dp_pa, airflow_m3h, fan_kw), room_telemetry co2_ppm,
events bms_alarm (a clue, not ground truth), staff_roster TECH_1, work_orders,
building.json sensors -> gateway mapping.
Typical actions: flag_anomaly (data_quality), dispatch_technician / order_service (approval),
defer_action.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
