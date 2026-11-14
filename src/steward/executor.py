"""Mock executor: turns decisions into an action trail. Nothing here touches a real system.

The data is an offline replay, so "executing" an action only records what the building
would have done. Before an autonomous action is recorded as executed, the executor checks
the safety rules with what was known at that moment (when a dataset is given):

* its dependencies were executed first (in dependency order, not just time order);
* ``release_room`` only after a cleaner completed cleaning since the last checkout, with the
  room vacant and reported clean - robots can never release a room;
* a robot is never sent into an occupied room;
* no staff member or robot is used while known to be absent.

Anything that fails a check is recorded as ``refused_safety_check`` or ``blocked_by_dependency``
with the reason. Actions that need approval (and consent) stay pending: nobody in the replay
can give it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from steward.contract import Decision

STATUS_BY_AUTONOMY = {
    "autonomous": "executed_simulated",
    "human_approval": "pending_human_approval",
    "approval_and_consent": "pending_human_approval_and_guest_consent",
}
NOTE = "Mock executor on replayed data: no real actuation, no message delivered."


def _ordered(decisions: list[Decision]) -> list[Decision]:
    """Time order, but a decision never runs before a dependency with the same or earlier time."""
    by_id = {d.event_id: d for d in decisions}
    done: set[str] = set()
    out: list[Decision] = []

    def visit(d: Decision, stack: set[str]) -> None:
        if d.event_id in done or d.event_id in stack:
            return
        stack.add(d.event_id)
        for dep in d.dependencies or []:
            if dep in by_id:
                visit(by_id[dep], stack)
        done.add(d.event_id)
        out.append(d)

    for d in sorted(decisions, key=lambda d: (d.timestamp_utc, d.event_id)):
        visit(d, set())
    return out


class _Checks:
    """Safety checks against the replay, using only what was known at the decision time."""

    def __init__(self, ds):
        self.ds = ds
        self.rooms = set(ds.room_ids)
        ev = ds.events
        self.cleaned = ev[ev["type"] == "cleaning_completed"][["timestamp_utc", "room_id"]]
        lc = ds.lifecycle
        self.checkouts = lc[~lc["occupied"]][["timestamp_utc", "room_id"]]
        self.robots = set(ds.staff_roster.loc[ds.staff_roster["role"] == "robot", "resource_id"])

    def occupied(self, room: str, at: pd.Timestamp) -> bool:
        frame = pd.DataFrame({"timestamp_utc": [at], "room_id": [room]})
        return bool(self.ds.add_occupancy(frame)["occupied"].iloc[0])

    def check(self, d: Decision) -> str | None:
        """None if the action is safe to record as executed, otherwise the reason it is refused."""
        at = pd.Timestamp(d.timestamp_utc)
        rooms = [a for a in d.affected_assets if a in self.rooms]
        if d.resource_id and d.resource_id in set(self.ds.resource_ids):
            absent = self.ds.known_absence(d.resource_id, at)
            if absent:
                return f"{d.resource_id} is known to be {absent} at this time"
        if d.action == "release_room":
            if d.resource_id in self.robots:
                return "a robot cannot release a room"
            for room in rooms:
                if self.occupied(room, at):
                    return f"{room} is occupied"
                out = self.checkouts[
                    (self.checkouts["room_id"] == room) & (self.checkouts["timestamp_utc"] <= at)
                ]
                since = out["timestamp_utc"].max() if len(out) else None
                done = self.cleaned[
                    (self.cleaned["room_id"] == room) & (self.cleaned["timestamp_utc"] <= at)
                ]
                if since is not None:
                    done = done[done["timestamp_utc"] >= since]
                if done.empty:
                    return f"no cleaning of {room} was completed since the last checkout"
        if d.action == "dispatch_robot":
            for room in rooms:
                if self.occupied(room, at):
                    return f"robots never enter an occupied room ({room})"
        return None


def execute(decisions: list[Decision], ds=None) -> list[dict]:
    checks = _Checks(ds) if ds is not None else None
    status: dict[str, str] = {}
    trail = []
    for d in _ordered(decisions):
        state = STATUS_BY_AUTONOMY[d.autonomy]
        why = None
        if state == "executed_simulated":
            blocked = [
                x for x in d.dependencies or [] if status.get(x) not in (None, "executed_simulated")
            ]
            if blocked:
                state, why = "blocked_by_dependency", f"waits for {', '.join(blocked)}"
            elif checks is not None:
                why = checks.check(d)
                if why:
                    state = "refused_safety_check"
        status[d.event_id] = state
        row = {
            "event_id": d.event_id,
            "episode_id": d.episode_id,
            "timestamp_utc": d.timestamp_utc,
            "action": d.action,
            "affected_assets": d.affected_assets,
            "resource_id": d.resource_id,
            "autonomy": d.autonomy,
            "status": state,
            "simulated": True,
            "note": NOTE,
        }
        if why:
            row["reason"] = why
        trail.append(row)
    return trail


def write_trail(trail: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in trail:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
