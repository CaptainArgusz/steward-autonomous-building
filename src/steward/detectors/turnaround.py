"""S01 The Perfect Checkout - turnaround conflicts (Level 1, mandatory). NOT IMPLEMENTED YET.

Problem: an early departure, an early check-in request and a tight cleaning shift collide.
Hard rules: never release an uncleaned room; give the guest a realistic time.

Signals: events (checkout, checkout_early, arrival_waiting_room, cleaning_*), bookings,
guest_requests (early_checkin, waiting_for_room, late_checkout), room_telemetry
housekeeping_state, staff_roster (absences only count from status_known_utc).
Typical actions: schedule_cleaning, dispatch_robot, notify_guest, release_room.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
