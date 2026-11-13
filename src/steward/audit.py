"""Record of candidates a detector looked at and deliberately did NOT act on.

Rejecting look-alikes is half the job (a bathtub is not a leak, an open window is not a
failing heat pump), but a rejected candidate leaves no trace in decisions.jsonl. Detectors
call ``dismiss`` so that the reasoning stays visible in the UI and to the jury without
adding alert spam to the decision file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from steward.contract import utc_z

_dismissed: list[dict] = []


def dismiss(
    issue_type: str, asset: str, at: Any, reason: str, evidence: list[str] | None = None
) -> None:
    """Note a suspicious-looking situation that was explained away, and why."""
    _dismissed.append(
        {
            "timestamp_utc": utc_z(at),
            "issue_type": issue_type,
            "asset": asset,
            "reason": reason,
            "evidence": evidence or [],
        }
    )


def drain() -> list[dict]:
    """Return everything recorded since the last drain, time-ordered, and reset."""
    out = sorted(_dismissed, key=lambda r: (r["timestamp_utc"], r["issue_type"], r["asset"]))
    _dismissed.clear()
    return out


def write(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
