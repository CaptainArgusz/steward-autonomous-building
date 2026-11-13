"""Mock executor: turns decisions into an action trail. Nothing here touches a real system.

The data is an offline replay, so "executing" an action only records what the building
would have done. Autonomous actions are marked as simulated executions; everything else
waits for approval (and consent) that nobody in the replay can give.
"""

from __future__ import annotations

import json
from pathlib import Path

from steward.contract import Decision

STATUS_BY_AUTONOMY = {
    "autonomous": "executed_simulated",
    "human_approval": "pending_human_approval",
    "approval_and_consent": "pending_human_approval_and_guest_consent",
}


def execute(decisions: list[Decision]) -> list[dict]:
    trail = []
    for d in sorted(decisions, key=lambda d: (d.timestamp_utc, d.event_id)):
        trail.append(
            {
                "event_id": d.event_id,
                "episode_id": d.episode_id,
                "timestamp_utc": d.timestamp_utc,
                "action": d.action,
                "affected_assets": d.affected_assets,
                "resource_id": d.resource_id,
                "autonomy": d.autonomy,
                "status": STATUS_BY_AUTONOMY[d.autonomy],
                "simulated": True,
                "note": "Mock executor on replayed data: no real actuation, no message delivered.",
            }
        )
    return trail


def write_trail(trail: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in trail:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
