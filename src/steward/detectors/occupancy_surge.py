"""S05 The Unexpected Weekend (Level 2). NOT IMPLEMENTED YET.

Problem: an event weekend fills the house while a cleaner calls in sick.
Hard rule: never invent capacity - only cleaners can certify and release a room; robots
shorten turnover but cannot clean bathrooms or enter occupied rooms; ONCALL_1 is one person,
15:30-22:00 local, ~45 min to site, one room at a time, for waiting guests only.

Signals: bookings (use created_utc), staff_roster (use status_known_utc), events, logistics.
Typical actions: schedule_cleaning, dispatch_robot, notify_guest, order_service.
"""

from __future__ import annotations

from steward.contract import Decision
from steward.dataset import Dataset


def detect(ds: Dataset) -> list[Decision]:
    return []
