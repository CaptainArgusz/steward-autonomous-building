"""Decision output contract v2 - the single definition of what we are allowed to emit.

Source of truth: data/quickstart/DECISION_CONTRACT.md and CHALLENGE.md section 7.
Every detector returns ``Decision`` objects; nothing else writes decisions*.jsonl.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

if TYPE_CHECKING:
    from steward.dataset import Dataset

ACTIONS = (
    "schedule_cleaning",
    "dispatch_robot",
    "adjust_room_setpoint",
    "optimize_energy",
    "block_room",
    "release_room",
    "reassign_room",
    "request_inspection",
    "dispatch_technician",
    "order_service",
    "notify_guest",
    "flag_anomaly",
    "defer_action",
    "request_human_approval",
)

# Actions that are never autonomous (CHALLENGE section 7). Other actions can also need
# approval depending on context (e.g. order_service for a contractor, any entry into an
# occupied room) - that is the detector author's responsibility.
APPROVAL_ACTIONS = frozenset(
    {"dispatch_technician", "block_room", "reassign_room", "request_human_approval"}
)

Priority = Literal["critical", "high", "medium", "low"]
Autonomy = Literal["autonomous", "human_approval", "approval_and_consent"]

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_z(ts: Any) -> str:
    """Format a timestamp as contract-style UTC ISO 8601 (``2026-01-05T08:15:00Z``)."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event_id(issue_type: str, asset: str, ts: Any, action: str) -> str:
    """Deterministic ID, so re-running the pipeline yields a byte-identical file."""
    return f"{issue_type}:{asset}:{pd.Timestamp(ts).strftime('%Y%m%dT%H%M')}:{action}"


class Decision(BaseModel):
    model_config = ConfigDict(extra="allow")

    # --- required ---
    event_id: str = Field(min_length=1)
    timestamp_utc: str
    affected_assets: list[str] = Field(min_length=1)
    issue_type: str = Field(min_length=1)
    action: str = Field(min_length=1)
    priority: Priority
    reason: str = Field(min_length=1)
    requires_human_approval: bool

    # --- optional (recommended) ---
    autonomy: Autonomy | None = None
    episode_id: str | None = None
    evidence: list[str] | None = None
    expected_outcome: str | None = None
    verification_plan: str | None = None
    dependencies: list[str] | None = None
    guest_request_ids: list[str] | None = None
    requires_guest_consent: bool | None = None
    resource_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    # --- our documented extensions (README "Extra fields") ---
    proposed_message: str | None = None  # guest message text; proposed, never "delivered"
    message_language: str | None = None  # language code of proposed_message (de, en, it, ...)

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v: Any) -> str:
        if not isinstance(v, str):
            v = utc_z(v)
        if not _TS_RE.match(v):
            raise ValueError(f"timestamp_utc must look like 2026-01-05T08:15:00Z, got {v!r}")
        return v

    @model_validator(mode="after")
    def _autonomy_rules(self) -> Decision:
        if self.requires_guest_consent and not self.requires_human_approval:
            raise ValueError("requires_guest_consent=true also needs requires_human_approval=true")
        if self.action in APPROVAL_ACTIONS and not self.requires_human_approval:
            raise ValueError(f"action {self.action!r} always needs requires_human_approval=true")

        derived: Autonomy = (
            "approval_and_consent"
            if self.requires_guest_consent
            else "human_approval"
            if self.requires_human_approval
            else "autonomous"
        )
        if self.autonomy is None:
            self.autonomy = derived
        elif self.autonomy != derived:
            raise ValueError(
                f"autonomy={self.autonomy!r} contradicts the approval/consent flags (implies {derived!r})"
            )
        return self

    def to_json_line(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), ensure_ascii=False)


def write_decisions(decisions: list[Decision], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(decisions, key=lambda d: (d.timestamp_utc, d.event_id))
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for d in ordered:
            fh.write(d.to_json_line() + "\n")


# --------------------------------------------------------------------------- validation


@dataclass
class Report:
    path: Path
    decisions: list[Decision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        d = self.decisions
        lines = [
            f"{self.path}: {len(d)} decisions, {len(self.errors)} errors, {len(self.warnings)} warnings"
        ]
        if d:
            episodes = {x.episode_id or x.event_id for x in d}
            lines.append(f"  incidents (distinct episode_id): {len(episodes)}")
            for label, counter in (
                ("issue_type", Counter(x.issue_type for x in d)),
                ("action", Counter(x.action for x in d)),
                ("autonomy", Counter(x.autonomy for x in d)),
                ("priority", Counter(x.priority for x in d)),
            ):
                lines.append(
                    f"  {label}: " + ", ".join(f"{k}={v}" for k, v in counter.most_common())
                )
        lines += [f"  ERROR   {e}" for e in self.errors]
        lines += [f"  warning {w}" for w in self.warnings]
        return "\n".join(lines)


_CONTRACT_FIELDS = set(Decision.model_fields)


def validate_file(path: str | Path, ds: Dataset | None = None) -> Report:
    """Check a decisions file against the contract and, given a dataset, against the building.

    Errors are things the jury's automated check would likely punish (broken contract,
    unknown assets, a resource that is known to be absent). Warnings need a human look.
    """
    path = Path(path)
    report = Report(path=path)
    seen: set[str] = set()

    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            d = Decision.model_validate(json.loads(line))
        except json.JSONDecodeError as e:
            report.errors.append(f"line {n}: not valid JSON ({e.msg})")
            continue
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, x['loc'])) or 'decision'}: {x['msg']}" for x in e.errors()
            )
            report.errors.append(f"line {n}: {msgs}")
            continue

        where = f"line {n} ({d.event_id})"
        if d.event_id in seen:
            report.errors.append(f"{where}: duplicate event_id")
        seen.add(d.event_id)
        if d.action not in ACTIONS:
            report.warnings.append(
                f"{where}: action {d.action!r} is outside the vocabulary - document it in README"
            )
        extra = set(d.model_dump(exclude_none=True)) - _CONTRACT_FIELDS
        if extra:
            report.warnings.append(f"{where}: fields outside the contract: {sorted(extra)}")
        report.decisions.append(d)

    all_ids = {d.event_id for d in report.decisions}
    if ds is not None:
        requests = set(ds.guest_requests["request_id"])
        for d in report.decisions:
            where = f"{d.event_id}"
            ts = pd.Timestamp(d.timestamp_utc)
            unknown = [a for a in d.affected_assets if a not in ds.known_ids]
            if unknown:
                report.errors.append(f"{where}: affected_assets not in building.json: {unknown}")
            if not (ds.start <= ts <= ds.end):
                report.warnings.append(f"{where}: timestamp outside the dataset window")
            bad_requests = [r for r in d.guest_request_ids or [] if r not in requests]
            if bad_requests:
                report.errors.append(f"{where}: unknown guest_request_ids {bad_requests}")
            if d.resource_id:
                if d.resource_id not in ds.resource_ids:
                    report.errors.append(f"{where}: unknown resource_id {d.resource_id!r}")
                elif absence := ds.known_absence(d.resource_id, ts):
                    report.errors.append(
                        f"{where}: assigns {d.resource_id}, known to be {absence} at that time"
                    )
                elif not d.resource_id.startswith("ONCALL") and not ds.on_shift(d.resource_id, ts):
                    report.warnings.append(
                        f"{where}: {d.resource_id} has no scheduled shift covering the decision time"
                    )

    # Alert-spam guard: many decisions repeating the same action inside one episode.
    per_episode = Counter((d.episode_id or d.event_id, d.action) for d in report.decisions)
    for (episode, action), count in per_episode.items():
        if count > 5:
            report.warnings.append(f"episode {episode}: {count}x {action} - possible alert spam")
    for d in report.decisions:
        for dep in d.dependencies or []:
            if ":" in dep and dep not in all_ids and not dep.startswith(("precondition:", "http")):
                report.warnings.append(
                    f"{d.event_id}: dependency {dep!r} is not an event_id in this file"
                )
    return report
