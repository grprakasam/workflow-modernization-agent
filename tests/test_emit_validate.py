import copy

from conftest import SAMPLES

from wma.evaluate import convert
from wma.parsers import parse_file
from wma.validate import validate_definition


def all_definitions(mapper):
    for f in sorted([*SAMPLES.glob("*.xml"), *SAMPLES.glob("*.json")]):
        wf = parse_file(f)
        yield wf, convert(wf, mapper)


def test_every_generated_flow_passes_static_checks(fixture_mapper):
    for wf, (_, definition, _) in all_definitions(fixture_mapper):
        assert validate_definition(definition) == [], wf.name


def test_flagged_steps_become_visible_todo_placeholders(fixture_mapper):
    wf = parse_file(SAMPLES / "it_access_request.json")
    decisions, definition, _ = convert(wf, fixture_mapper)
    todo = definition["actions"]["Condition_s2"]["actions"]["TODO_manual_rebuild_s3"]
    assert todo["type"] == "Compose" and "Rebuild manually" in todo["inputs"]
    assert todo["metadata"]["wmaMethod"] == "unsupported"


def test_validator_catches_broken_definitions(fixture_mapper):
    wf = parse_file(SAMPLES / "invoice_approval.xml")
    _, good, _ = convert(wf, fixture_mapper)

    bad = copy.deepcopy(good)
    bad["actions"]["Condition_s1"]["runAfter"] = {"Nope": ["Succeeded"]}
    assert any("runAfter 'Nope'" in i for i in validate_definition(bad))

    bad = copy.deepcopy(good)
    bad["actions"]["Initialize_Status"]["inputs"]["variables"][0]["name"] = "Other"
    assert any("never initialized" in i for i in validate_definition(bad))

    bad = copy.deepcopy(good)
    bad["actions"]["Condition_s1"]["actions"]["Start_and_wait_for_an_approval_s2"]["inputs"]["host"]["operationId"] = (
        "DeleteAll"
    )
    assert any("not in the catalog" in i for i in validate_definition(bad))

    bad = copy.deepcopy(good)
    bad["actions"]["Condition_s1"]["expression"]["and"][0]["greater"][0] = "@runScript('x')"
    assert any("not supported" in i for i in validate_definition(bad))


def test_rule_only_conversion_needs_no_llm():
    wf = parse_file(SAMPLES / "purchase_order.json")
    decisions, definition, connectors = convert(wf, None)
    assert {d.method for d in decisions.values()} <= {"rule", "control"}
    assert connectors == ["shared_approvals", "shared_office365", "shared_sharepointonline"]
    assert validate_definition(definition) == []
