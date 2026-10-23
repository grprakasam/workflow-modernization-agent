import pytest
from conftest import SAMPLES

from wma.ir import WorkflowParseError
from wma.parsers import parse_file, parse_json, parse_xml


def test_all_samples_parse():
    files = sorted([*SAMPLES.glob("*.xml"), *SAMPLES.glob("*.json")])
    assert len(files) == 12
    for f in files:
        wf = parse_file(f)
        assert wf.cases, f"{f.name} has no behavioral test cases"


def test_xml_types_and_nesting():
    wf = parse_file(SAMPLES / "invoice_approval.xml")
    assert wf.trigger.kind == "item_created" and wf.trigger.list == "Invoices"
    assert wf.variables[0].default == "Pending"
    assert wf.cases[1].trigger_body["Amount"] == 9800 and wf.cases[1].approvals == {"s2": "Approve"}
    assert [s.id for s in wf.all_steps()] == [f"s{i}" for i in range(1, 12)]


def test_typed_step_parameters_are_coerced():
    wf = parse_file(SAMPLES / "contract_renewal.xml")
    delay = next(s for s in wf.all_steps() if s.kind == "Delay")
    assert delay.attrs["minutes"] == 60


def _wf(steps, variables=None):
    import json

    return json.dumps({"name": "t", "trigger": {"kind": "manual"}, "variables": variables or [], "steps": steps})


@pytest.mark.parametrize(
    "steps,variables,msg",
    [
        ([{"id": "a", "type": "SetVariable", "name": "X", "value": 1}], None, "not declared"),
        ([{"id": "a", "type": "SendEmail", "to": "{item:Email}", "subject": "s"}], None, "outside a ForEach"),
        ([{"id": "a", "type": "Teleport", "to": "moon"}], None, "unknown step kind"),
        ([{"id": "a", "type": "Delay", "minutes": 1}, {"id": "a", "type": "Delay", "minutes": 2}], None, "duplicate"),
        ([{"id": "a", "type": "If", "condition": "approval:zz == 'Approve'"}], None, "not a RequestApproval"),
        ([{"id": "a", "type": "SendEmail", "subject": "no recipient"}], None, "Field required"),
        ([{"id": "a", "type": "If", "condition": "field:A >"}], None, "expected a value"),
    ],
)
def test_static_checks_reject_bad_workflows(steps, variables, msg):
    with pytest.raises(WorkflowParseError, match=msg):
        parse_json(_wf(steps, variables))


def test_invalid_xml_is_reported():
    with pytest.raises(WorkflowParseError, match="invalid XML"):
        parse_xml("<Workflow><Trigger kind='manual'></Workflow>")
