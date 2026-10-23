"""Map each source step to a catalog action: deterministic rules first, bounded LLM second."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ir import CONTROL_KINDS, Step, Workflow
from .llm_mapper import BoundedLLMMapper

# source kind -> (catalog key, param builder). These cover the common, well-understood steps.
RULES: dict[str, tuple[str, Any]] = {
    "SendEmail": ("outlook.send_email", lambda a: {"to": a["to"], "subject": a["subject"], "body": a["body"]}),
    "CreateListItem": ("sharepoint.create_item", lambda a: {"list": a["list"], "fields": a["fields"]}),
    "UpdateListItem": (
        "sharepoint.update_item",
        lambda a: {"list": a["list"], "item_id": a["item_id"], "fields": a["fields"]},
    ),
    "HttpRequest": ("http.request", lambda a: {"method": a["method"], "url": a["url"], "body": a["body"]}),
    "RequestApproval": ("approvals.start_and_wait", lambda a: {"assigned_to": a["assigned_to"], "title": a["title"]}),
    "Delay": ("core.wait", lambda a: {"minutes": a["minutes"]}),
    "SetVariable": ("core.set_variable", lambda a: {"name": a["name"], "value": a["value"]}),
    "Terminate": ("core.terminate", lambda a: {"status": a["status"]}),
}


@dataclass
class Decision:
    step_id: str
    source_kind: str
    method: str  # control | rule | llm | unsupported
    target: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    rationale: str = ""
    issues: list[str] = field(default_factory=list)
    suggestion: dict | None = None


def available_refs(wf: Workflow, approvals: list[str]) -> set[str]:
    """References a proposal may use besides field:X (always allowed) and the step's own item:X."""
    return {f"var:{v.name}" for v in wf.variables} | {f"approval:{a}" for a in approvals}


def map_workflow(wf: Workflow, llm: BoundedLLMMapper | None = None) -> dict[str, Decision]:
    approvals = [s.id for s in wf.all_steps() if s.kind == "RequestApproval"]
    decisions: dict[str, Decision] = {}

    def visit(steps: list[Step], in_loop: bool):
        for step in steps:
            if step.kind in CONTROL_KINDS:
                decisions[step.id] = Decision(step.id, step.kind, "control")
                visit(step.then, in_loop)
                visit(step.else_, in_loop)
                visit(step.body, in_loop or step.kind == "ForEach")
                continue
            if step.kind in RULES:
                target, build = RULES[step.kind]
                decisions[step.id] = Decision(
                    step.id, step.kind, "rule", target, build(step.attrs), 1.0, "deterministic rule"
                )
                continue
            if llm is None:
                decisions[step.id] = Decision(
                    step.id, step.kind, "unsupported", issues=["no deterministic rule; LLM mapping is off"]
                )
                continue
            refs = available_refs(wf, approvals)
            if in_loop:  # inside a loop the step's own item:X references stay valid
                refs |= set(_item_refs(step))
            res = llm.map(step, f"{wf.name}/{step.id}", refs)
            decisions[step.id] = Decision(
                step.id,
                step.kind,
                "llm" if res.accepted else "unsupported",
                res.target if res.accepted else None,
                res.params if res.accepted else {},
                res.confidence,
                res.rationale,
                res.issues,
                res.suggestion,
            )

    visit(wf.steps, False)
    return decisions


def _item_refs(step: Step) -> list[str]:
    from .expr import refs_in

    out = []
    for v in step.attrs.values():
        out += [f"{r.kind}:{r.name}" for r in refs_in(v) if r.kind == "item"]
    return out
