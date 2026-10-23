"""Parsers for the two legacy formats (XML and JSON) into the typed IR.

The formats are modelled on SharePoint Designer / Nintex-style workflow exports: a trigger,
declared variables, a tree of steps, and optional behavioral test cases. Real platform exports
need a thin adapter onto this schema; everything after the IR is format-independent.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .expr import ExpressionError, condition_refs, parse_condition, refs_in
from .ir import CONTROL_KINDS, Step, TestCase, Trigger, Variable, Workflow, WorkflowParseError, validate_step_attrs


def _typed(value: str, kind: str | None) -> Any:
    if kind in (None, "string"):
        return value
    if kind in ("number", "integer", "float"):
        return float(value) if "." in value else int(value)
    if kind in ("bool", "boolean"):
        return value.strip().lower() == "true"
    if kind in ("json", "array"):
        return json.loads(value)
    raise WorkflowParseError(f"unknown value type '{kind}'")


# ------------------------------------------------------------------------------------- XML
def _xml_fields(el: ET.Element) -> dict[str, Any]:
    return {f.get("name"): _typed(f.get("value", ""), f.get("type")) for f in el.findall("Field")}


def _xml_step(el: ET.Element) -> Step:
    attrs: dict[str, Any] = {k: v for k, v in el.attrib.items() if k not in ("id", "valueType")}
    if el.get("valueType") and "value" in attrs:
        attrs["value"] = _typed(attrs["value"], el.get("valueType"))
    if el.findall("Field"):
        attrs["fields"] = _xml_fields(el)
    if "id" not in el.attrib:
        raise WorkflowParseError(f"<{el.tag}> is missing an id")
    then_el, else_el = el.find("Then"), el.find("Else")
    body = [_xml_step(c) for c in el if c.tag not in ("Field", "Then", "Else")] if el.tag == "ForEach" else []
    return Step(
        id=el.get("id"),
        kind=el.tag,
        attrs=attrs,
        then=[_xml_step(c) for c in then_el] if then_el is not None else [],
        **{"else": [_xml_step(c) for c in else_el] if else_el is not None else []},
        body=body,
    )


def parse_xml(text: str) -> Workflow:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise WorkflowParseError(f"invalid XML: {exc}") from exc
    if root.tag != "Workflow":
        raise WorkflowParseError("root element must be <Workflow>")
    tr = root.find("Trigger")
    if tr is None:
        raise WorkflowParseError("missing <Trigger>")
    trigger = Trigger(
        kind=tr.get("kind"),
        site=tr.get("site"),
        list=tr.get("list"),
        every_minutes=int(tr.get("every_minutes")) if tr.get("every_minutes") else None,
    )
    variables = [
        Variable(
            name=v.get("name"),
            type=v.get("type", "string"),
            default=_typed(v.get("default"), v.get("type")) if v.get("default") is not None else None,
        )
        for v in root.findall("Variables/Variable")
    ]
    steps_el = root.find("Steps")
    steps = [_xml_step(c) for c in (steps_el if steps_el is not None else [])]
    cases = []
    for c in root.findall("TestCases/Case"):
        body = _xml_fields(c.find("Trigger")) if c.find("Trigger") is not None else {}
        cases.append(
            TestCase(
                name=c.get("name"),
                trigger_body=body,
                approvals={a.get("step"): a.get("outcome") for a in c.findall("Approval")},
            )
        )
    wf = Workflow(
        name=root.get("name"),
        source_format="xml",
        description=root.get("description", ""),
        trigger=trigger,
        variables=variables,
        steps=steps,
        cases=cases,
    )
    check_workflow(wf)
    return wf


# ------------------------------------------------------------------------------------ JSON
_STRUCT_KEYS = {"id", "type", "then", "else", "do"}


def _json_step(obj: dict) -> Step:
    if "id" not in obj or "type" not in obj:
        raise WorkflowParseError(f"step needs 'id' and 'type': {obj}")
    attrs = {k: v for k, v in obj.items() if k not in _STRUCT_KEYS}
    return Step(
        id=obj["id"],
        kind=obj["type"],
        attrs=attrs,
        then=[_json_step(s) for s in obj.get("then", [])],
        **{"else": [_json_step(s) for s in obj.get("else", [])]},
        body=[_json_step(s) for s in obj.get("do", [])],
    )


def parse_json(text: str) -> Workflow:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowParseError(f"invalid JSON: {exc}") from exc
    wf = Workflow(
        name=data["name"],
        source_format="json",
        description=data.get("description", ""),
        trigger=Trigger(**data["trigger"]),
        variables=[Variable(**v) for v in data.get("variables", [])],
        steps=[_json_step(s) for s in data["steps"]],
        cases=[TestCase(**c) for c in data.get("cases", [])],
    )
    check_workflow(wf)
    return wf


def parse_file(path: str | Path) -> Workflow:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".xml":
        return parse_xml(text)
    if path.suffix.lower() == ".json":
        return parse_json(text)
    raise WorkflowParseError(f"unsupported file type: {path.suffix}")


# ---------------------------------------------------------------------------- static checks
def check_workflow(wf: Workflow) -> None:
    """Validate step parameters and every reference before anything is mapped."""
    seen: set[str] = set()
    variables = {v.name for v in wf.variables}
    approvals = {s.id for s in wf.all_steps() if s.kind == "RequestApproval"}

    def check_refs(step: Step, refs, in_loop: bool):
        for r in refs:
            if r.kind == "var" and r.name not in variables:
                raise WorkflowParseError(f"step {step.id}: variable '{r.name}' is not declared")
            if r.kind == "approval" and r.name not in approvals:
                raise WorkflowParseError(f"step {step.id}: approval '{r.name}' is not a RequestApproval step")
            if r.kind == "item" and not in_loop:
                raise WorkflowParseError(f"step {step.id}: item:{r.name} used outside a ForEach")

    def visit(steps: list[Step], in_loop: bool):
        for s in steps:
            if s.id in seen:
                raise WorkflowParseError(f"duplicate step id '{s.id}'")
            seen.add(s.id)
            validate_step_attrs(s)
            if s.kind == "SetVariable" and s.attrs["name"] not in variables:
                raise WorkflowParseError(f"step {s.id}: variable '{s.attrs['name']}' is not declared")
            try:
                if s.kind == "If":
                    check_refs(s, condition_refs(parse_condition(s.attrs["condition"])), in_loop)
                values = [v for k, v in s.attrs.items() if k != "condition"]
                values += list(s.attrs.get("fields", {}).values()) if isinstance(s.attrs.get("fields"), dict) else []
                check_refs(s, [r for v in values for r in refs_in(v)], in_loop)
            except ExpressionError as exc:
                raise WorkflowParseError(f"step {s.id}: {exc}") from exc
            if s.kind not in CONTROL_KINDS and (s.then or s.else_ or s.body):
                raise WorkflowParseError(f"step {s.id}: only If/ForEach may contain steps")
            visit(s.then, in_loop)
            visit(s.else_, in_loop)
            visit(s.body, in_loop or s.kind == "ForEach")

    visit(wf.steps, False)
