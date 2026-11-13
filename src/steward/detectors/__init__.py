"""Detector registry: one module per problem class, one owner per module.

A detector is a function ``detect(ds: Dataset) -> list[Decision]``. It replays the data,
and every decision it returns must be justified by information that was already known at
``timestamp_utc``. To add a detector, create a module and register it here.
"""

from __future__ import annotations

from collections.abc import Callable

from steward.contract import Decision
from steward.dataset import Dataset
from steward.detectors import (
    energy,
    heating_comfort,
    logistics,
    maintenance,
    occupancy_surge,
    turnaround,
    water_leak,
)

Detector = Callable[[Dataset], list[Decision]]

DETECTORS: dict[str, Detector] = {
    "room_turnaround": turnaround.detect,  # S01
    "water_leak": water_leak.detect,  # S02
    "heating_comfort": heating_comfort.detect,  # S03
    "ventilation_maintenance": maintenance.detect,  # S04
    "occupancy_surge": occupancy_surge.detect,  # S05
    "energy_optimization": energy.detect,  # L2
    "logistics": logistics.detect,  # L2
}
