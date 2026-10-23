import json

from conftest import SAMPLES

from wma.cli import main
from wma.evaluate import evaluate_samples


def test_scorecard_numbers(fixture_mapper):
    s = evaluate_samples(SAMPLES, fixture_mapper)["summary"]
    assert s["workflows"] == 12 and s["static_issues"] == 0
    assert s["silent_failures"] == 0  # every failing case belongs to a workflow with a flagged step
    assert s["unsupported"] == 3 and s["llm_mapped"] == 9
    assert s["auto_converted_rate"] >= 0.9


def test_rules_only_mode_flags_instead_of_guessing():
    s = evaluate_samples(SAMPLES, None)["summary"]
    assert s["llm_mapped"] == 0 and s["unsupported"] == 12 and s["silent_failures"] == 0


def test_cli_evaluate_writes_reports(tmp_path):
    code = main(["evaluate", "--out", str(tmp_path), "--flows", str(tmp_path / "flows"), "--min-auto", "0.9"])
    assert code == 0
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["summary"]["workflows"] == 12
    assert (tmp_path / "flows" / "invoice-approval" / "definition.json").exists()
    assert "behavior-equivalent" in (tmp_path / "latest_scorecard.md").read_text()


def test_cli_quality_gate_fails_when_bar_is_too_high(tmp_path):
    assert main(["evaluate", "--out", str(tmp_path), "--min-auto", "0.99"]) == 1
