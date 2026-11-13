from pathlib import Path

import pytest

from steward.dataset import Dataset

ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "data" / "quickstart"


@pytest.fixture(scope="session")
def ds() -> Dataset:
    return Dataset(QUICKSTART)


@pytest.fixture
def valid() -> dict:
    """A minimal decision that satisfies the contract."""
    return {
        "event_id": "t-1",
        "timestamp_utc": "2026-01-06T08:15:00Z",
        "affected_assets": ["R203"],
        "issue_type": "room_turnaround",
        "action": "schedule_cleaning",
        "priority": "high",
        "reason": "test",
        "requires_human_approval": False,
    }
