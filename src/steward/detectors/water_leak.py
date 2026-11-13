"""S02 The Hidden Water Leak (Level 1, mandatory). NOT IMPLEMENTED YET.

Problem: unusual consumption - loud, barely above normal, or only visible as a gap between
the main meter and the sum of room meters. Look-alikes: guests (bathtubs), cleaning,
legionella flushing (work_orders), meter exchanges (counter restarts), frozen/implausible
readings, roof-garden irrigation and other common-area use on the main meter.

Signals: room_water + water_main (cumulative - difference them), room_telemetry presence,
events (stays, cleaning), work_orders, building.json has_bathtub.
Typical actions: flag_anomaly, request_inspection. Entering an occupied room or shutting
off water needs approval AND guest consent.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
