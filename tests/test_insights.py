import json

import pytest

from steward import insights
from steward.insights import REQUIRED_FIELDS, Skip, compare_groups


@pytest.fixture(scope="module")
def result(ds):
    return insights.build_all(ds)


def test_runs_on_three_weeks_and_skips_what_needs_a_year(result):
    found, skipped, checked, _ = result
    # 21 January days cannot show a summer; the analysis must say so instead of crashing.
    assert "summer_overheating" in {s["analysis"] for s in skipped}
    assert all(s["reason"] for s in skipped)
    assert len(found) + len(skipped) == len(insights.ANALYSES)
    assert checked and all({"hypothesis", "result"} <= set(c) for c in checked)


def test_insights_follow_the_contract_schema(result):
    found = result[0]
    assert found, "at least one analysis should work on the quickstart data"
    ids = [i["insight_id"] for i in found]
    assert len(ids) == len(set(ids))
    for i in found:
        assert all(i.get(f) not in (None, "", []) for f in REQUIRED_FIELDS), i["insight_id"]
        assert i["target"] in ("PLATO", "CLARA")
        assert 0.0 <= i["confidence"] <= 1.0
        assert "PLATO-40" in i["finding"]
        for chart in i["charts"]:
            assert chart["kind"] in ("bar", "line")
            for series in chart["series"]:
                assert len(series["values"]) == len(chart["categories"])
        json.dumps(i)  # must be serialisable as-is


def test_outputs_are_written_and_architecture_section_survives(ds, tmp_path):
    md, jsonl = tmp_path / "insights.md", tmp_path / "insights.jsonl"
    md.write_text(
        f"{insights.MD_ARCH_START}\nOUR ARCHITECTURE\n{insights.MD_ARCH_END}\n", encoding="utf-8"
    )
    res = insights.write_outputs(ds, tmp_path / "tables", jsonl, md)
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(res["insights"])
    text = md.read_text(encoding="utf-8")
    assert "OUR ARCHITECTURE" in text and "Checked and not found" in text


def test_compare_groups_detects_a_real_difference_and_not_noise():
    real = compare_groups([1.0, 1.1, 0.9, 1.2, 1.0], [0.1, 0.0, 0.2, 0.1, -0.1, 0.0])
    assert real["p"] < 0.05 and real["ci_low"] > 0
    none = compare_groups([0.1, -0.2, 0.3, 0.0, -0.1], [0.0, 0.2, -0.3, 0.1, -0.1, 0.05])
    assert none["p"] > 0.05
    with pytest.raises(Skip):
        compare_groups([1.0], [2.0, 3.0, 4.0])
