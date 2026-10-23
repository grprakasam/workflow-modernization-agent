import copy

from conftest import SAMPLES

from wma.evaluate import convert
from wma.parsers import parse_file
from wma.simulate import FlowSimulator, SourceSimulator


def runs(wf, definition):
    src, flow = SourceSimulator(wf), FlowSimulator(definition)
    return [(c.name, src.run(c), flow.run(c)) for c in wf.cases]


def test_equivalent_workflows_match_on_every_case(fixture_mapper):
    for name in (
        "invoice_approval.xml",
        "purchase_order.json",
        "employee_onboarding.xml",
        "document_retention.json",
        "expense_escalation.xml",
        "clinical_document_review.json",
    ):
        wf = parse_file(SAMPLES / name)
        _, definition, _ = convert(wf, fixture_mapper)
        for case, a, b in runs(wf, definition):
            assert a.signature() == b.signature(), f"{name} / {case}"


def test_terminate_and_loops_are_simulated():
    wf = parse_file(SAMPLES / "purchase_order.json")
    _, definition, _ = convert(wf, None)
    results = {c: (a, b) for c, a, b in runs(wf, definition)}
    a, b = results["large PO rejected"]
    assert a.status == b.status == "Cancelled"
    assert [e["kind"] for e in results["small PO"][1].events] == ["sp_create", "sp_create", "http", "email"]


def test_harness_catches_a_mutated_condition(fixture_mapper):
    """Mutation test: a wrong operator in the generated flow must be detected."""
    wf = parse_file(SAMPLES / "invoice_approval.xml")
    _, definition, _ = convert(wf, fixture_mapper)
    mutated = copy.deepcopy(definition)
    cond = mutated["actions"]["Condition_s1"]["expression"]["and"][0]
    mutated["actions"]["Condition_s1"]["expression"]["and"][0] = {"less": cond["greater"]}
    assert any(a.signature() != b.signature() for _, a, b in runs(wf, mutated))


def test_harness_catches_a_dropped_action(fixture_mapper):
    wf = parse_file(SAMPLES / "leave_request.json")
    _, definition, _ = convert(wf, fixture_mapper)
    mutated = copy.deepcopy(definition)
    then = mutated["actions"]["Condition_s2"]["actions"]
    del then["Create_item_s4"]
    assert any(a.signature() != b.signature() for _, a, b in runs(wf, mutated))


def test_unsupported_steps_show_up_as_differences(fixture_mapper):
    wf = parse_file(SAMPLES / "it_access_request.json")
    _, definition, _ = convert(wf, fixture_mapper)
    results = {c: (a, b) for c, a, b in runs(wf, definition)}
    a, b = results["approved"]
    assert a.events[1]["kind"] == "script" and "script" not in [e["kind"] for e in b.events]
    a, b = results["declined"]  # the flagged step is not on this path, so behavior matches
    assert a.signature() == b.signature()
