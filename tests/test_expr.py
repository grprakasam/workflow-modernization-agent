import pytest

from wma.expr import (
    ExpressionError,
    Ref,
    condition_to_wdl,
    escape_literal,
    evaluate_condition,
    parse_condition,
    render_template,
    template_to_wdl,
)

VALUES = {("field", "Amount"): 9800, ("field", "Region"): "EU", ("var", "Status"): "Open", ("approval", "s2"): "Reject"}
resolve = lambda r: VALUES.get((r.kind, r.name))  # noqa: E731


@pytest.mark.parametrize(
    "cond,expected",
    [
        ("field:Amount > 5000", True),
        ("field:Amount > 5000 and field:Region == 'US'", False),
        ("field:Amount > 5000 and field:Region == 'US' or var:Status == 'Open'", True),  # and binds tighter
        ("field:Amount > 5000 and (field:Region == 'US' or var:Status == 'Open')", True),
        ("not approval:s2 == 'Approve'", True),
        ("approval:s2 != 'Reject'", False),
        ("field:Missing > 1", False),  # comparisons with a missing value are false, not an error
    ],
)
def test_conditions(cond, expected):
    assert evaluate_condition(parse_condition(cond), resolve) is expected


def test_bad_conditions_raise():
    for bad in ("field:Amount >", "Amount > 5", "field:A == 'x' and", "(field:A == 1"):
        with pytest.raises(ExpressionError):
            parse_condition(bad)


def test_templates_keep_type_for_single_reference():
    assert render_template("{field:Amount}", resolve) == 9800
    assert render_template("field:Amount", resolve) == 9800
    assert render_template("Total {field:Amount} in {field:Region}", resolve) == "Total 9800 in EU"


def test_translation_to_power_automate():
    tr = lambda r: f"triggerBody()?['{r.name}']" if r.kind == "field" else f"variables('{r.name}')"  # noqa: E731
    assert template_to_wdl("{field:Amount}", tr) == "@triggerBody()?['Amount']"
    assert (
        template_to_wdl("Hi {field:Name} @ {var:Status}", tr)
        == "Hi @{triggerBody()?['Name']} @@ @{variables('Status')}"
    )
    assert condition_to_wdl(parse_condition("field:Amount >= 10 and var:Status != 'Done'"), tr) == {
        "and": [
            {"greaterOrEquals": ["@triggerBody()?['Amount']", 10]},
            {"not": {"equals": ["@variables('Status')", "Done"]}},
        ]
    }


def test_literal_at_signs_are_escaped():
    assert escape_literal("mail me @ ops") == "mail me @@ ops"


def test_unknown_reference_kind():
    with pytest.raises(ExpressionError):
        Ref.parse("secret:Token")
