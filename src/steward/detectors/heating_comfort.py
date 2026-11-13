"""S03 Energy vs Comfort (Level 1, mandatory). NOT IMPLEMENTED YET.

Problem: cold weather, dynamic prices and reduced heat-pump performance meet occupied rooms.
Hard rule: protect occupants first - never silently trade comfort for an energy metric.
Look-alikes: open windows, setback in vacant rooms (18 C), guest-chosen setpoints (19-24 C),
defrost cycles, sensor dropouts.

Signals: room_telemetry (temperature vs setpoint, window_open), equipment HP1 (heat output,
electric, supply temp, status), weather + weather_forecast (use issued_utc), tariff (use
published_utc), guest_requests temperature_cold, comfort band in building.json.
Typical actions: adjust_room_setpoint, optimize_energy, dispatch_technician (approval),
notify_guest.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
