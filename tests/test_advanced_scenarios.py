"""Level 2 S04/S05 behavior on the supplied quickstart replay."""

import re

from steward.detectors import maintenance, occupancy_surge, turnaround


def test_gateway_gap_is_separate_from_ahu_service(ds):
    decisions = maintenance.detect(ds)
    gateways = [d for d in decisions if d.affected_assets[0].startswith("GW_F")]
    ahu = [d for d in decisions if d.affected_assets[0] == "AHU1"]
    assert gateways and ahu
    assert {d.action for d in gateways} == {"flag_anomaly", "request_inspection"}
    assert all(
        d.requires_human_approval
        for d in ahu
        if d.action in ("order_service", "dispatch_technician")
    )
    assert any(d.action == "order_service" for d in ahu)


def test_weekend_plan_uses_shared_room_replay_without_duplicate_first_notices(ds):
    weekend = occupancy_surge.detect(ds)
    room = turnaround.detect(ds)
    assert any(d.action == "schedule_cleaning" for d in weekend)
    assert any(d.action == "request_human_approval" for d in weekend)
    assert all(d.resource_id != "ONCALL_1" for d in weekend)
    first = [d for d in weekend if d.action == "notify_guest"]
    assert first and all(not re.search(r"\d{1,2}:\d{2}", d.proposed_message) for d in first)
    for notice in first:
        assert not any(
            other.action == "notify_guest"
            and other.timestamp_utc == notice.timestamp_utc
            and set(other.affected_assets) & set(notice.affected_assets)
            for other in room
        )
