import numpy as np
import pandas as pd
import pytest

from steward import audit
from steward.detectors import heating_comfort as hc


@pytest.fixture(scope="module")
def result(ds):
    audit.drain()
    decisions = hc.detect(ds)
    return decisions, audit.drain()


def test_efficiency_line_is_learned_and_ignores_a_past_degraded_spell():
    rng = np.random.default_rng(0)
    outdoor = rng.uniform(-10, 10, 2000)
    cop = 2.9 + 0.08 * outdoor + rng.normal(0, 0.03, 2000)
    cop[:150] *= 0.85  # an old incident inside the history must not drag the baseline down
    a, b, sigma = hc.fit_efficiency_line(outdoor, cop)
    assert a == pytest.approx(2.9, abs=0.03)
    assert b == pytest.approx(0.08, abs=0.005)
    assert 0.85 < hc.degradation_threshold(sigma) <= 1 - hc.MIN_DROP


def test_no_baseline_without_enough_history():
    assert hc.fit_efficiency_line(np.array([1.0, 2.0]), np.array([3.0, 3.1])) is None
    flat = np.full(500, 2.0)  # plenty of samples but no outdoor range to fit a slope on
    assert hc.fit_efficiency_line(flat, np.full(500, 3.0)) is None


def test_quickstart_has_exactly_one_plant_incident_before_the_repair(result, ds):
    decisions, _ = result
    assert len({d.episode_id for d in decisions}) == 1
    repair = ds.work_orders[
        (ds.work_orders["asset_id"] == "HP1") & (ds.work_orders["type"] == "corrective")
    ]["completed_utc"].min()
    flag = next(d for d in decisions if d.action == "flag_anomaly")
    assert pd.Timestamp(flag.timestamp_utc) < repair - pd.Timedelta(days=2)
    assert flag.affected_assets[0] == "HP1"


def test_service_needs_approval_and_everything_carries_evidence(result):
    decisions, _ = result
    service = [d for d in decisions if d.action in ("order_service", "dispatch_technician")]
    assert service and all(d.requires_human_approval for d in service)
    assert all(d.evidence and d.verification_plan and d.confidence for d in decisions)
    assert all(not d.requires_human_approval for d in decisions if d.action == "notify_guest")


def test_complaints_during_the_incident_are_answered_within_their_deadline(result, ds):
    decisions, _ = result
    due = ds.guest_requests.set_index("request_id")["response_due_utc"]
    replies = [d for d in decisions if d.guest_request_ids]
    assert replies
    for d in replies:
        assert pd.Timestamp(d.timestamp_utc) <= due[d.guest_request_ids[0]]
        assert d.proposed_message and d.message_language


def test_isolated_fault_and_benign_complaints_are_dismissed_not_alerted(result, ds):
    decisions, dismissed = result
    first = min(pd.Timestamp(d.timestamp_utc) for d in decisions)
    hp = ds.equipment_wide("HP1")
    early_fault = hp.index[(hp["status"] == "lockout_high_pressure") & (hp.index < first)]
    assert len(early_fault) == 1  # a single lockout a week before the real incident
    assert any(x["asset"] == "HP1" for x in dismissed)
    assert any("warming up" in x["reason"] or "window" in x["reason"] for x in dismissed)


def test_unknown_guest_language_falls_back_to_english_and_german():
    text, lang = hc._message("proactive", "R101", None)
    assert lang == "en+de" and "Dear guest" in text and "Liebe Gäste" in text
    assert hc._message("reply", "R101", "it")[1] == "it"
