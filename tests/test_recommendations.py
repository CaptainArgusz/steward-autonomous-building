import json
from datetime import UTC, datetime

import pytest

from steward import recommendations as rec


def packet(fingerprint="dataset-a"):
    return {
        "dataset": {"profile": "holdout", "fingerprint": fingerprint},
        "observations": [
            {
                "insight_id": "O-1",
                "topic": "Coverage",
                "finding": "Missing readings",
                "uncertainty": "Cause unknown",
            }
        ],
        "checked": [],
        "skipped": [],
    }


def generated():
    return rec.Generated(
        recommendations=[
            rec.Advice(
                target="CLARA",
                topic="Investigate coverage",
                evidence_ids=["O-1"],
                recommendation="Inspect gateway connections.",
                why_it_fits="Reduces maintenance uncertainty.",
                expected_benefit="Qualitative improvement in visibility.",
                trade_off="Requires staff time.",
                next_check="Check gateway power and network records.",
            )
        ],
        limitations="The cause is unconfirmed.",
    )


def test_cache_separates_datasets_and_models(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "CACHE", tmp_path)
    calls = []

    def generate(evidence, model):
        calls.append((evidence["dataset"]["fingerprint"], model))
        return generated()

    monkeypatch.setattr(rec, "generate", generate)
    a = rec.recommend(packet())
    assert rec.recommend(packet()) == a
    rec.recommend(packet("dataset-b"))
    monkeypatch.setenv("GROQ_MODEL", "another-model")
    rec.recommend(packet())
    assert len(calls) == 3
    assert a["dataset"]["profile"] == "holdout"
    assert datetime.fromisoformat(a["generated_at"]) <= datetime.now(UTC)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("evidence_ids", ["DEV-ONLY"], "outside this dataset"),
        ("expected_benefit", "Saves 99 percent of costs.", "number absent"),
    ],
)
def test_unsupported_claims_are_rejected_before_publishing(monkeypatch, field, value, error):
    response = generated().model_dump()
    response["recommendations"][0][field] = value

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": json.dumps(response)}}]}
            ).encode()

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(rec, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(rec.RecommendationError, match=error):
        rec.generate(packet(), rec.MODEL)


def test_existing_recommendation_text_is_not_sent_to_model():
    evidence = packet()
    evidence["observations"][0]["recommendation"] = "Old recommendation"
    evidence["observations"][0]["expected_benefit"] = "Old benefit"
    prompt = rec.model_input(evidence)
    assert "Old recommendation" not in json.dumps(prompt)
    assert "Old benefit" not in json.dumps(prompt)
    assert prompt["observations"][0]["finding"] == "Missing readings"


def test_holdout_profile_has_distinct_evidence_identity(ds, tmp_path):
    from steward.dataset import REQUIRED_FILES, Dataset

    root = tmp_path / "holdout"
    root.mkdir()
    for name in REQUIRED_FILES:
        if name != "manifest.json":
            (root / name).symlink_to((ds.root / name).resolve())
    manifest = dict(ds.manifest, profile="holdout")
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert rec.fingerprint(Dataset(root)) != rec.fingerprint(ds)
