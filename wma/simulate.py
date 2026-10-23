"""Two simulators used for differential testing of behavioral fidelity.

SourceSimulator runs the legacy workflow (IR) with the legacy platform's step semantics.
FlowSimulator runs the generated Power Automate definition with Power Automate semantics.
Both record observable side effects as normalized events; a conversion is behavior-equivalent
for a test case when both runs produce the same events, final variables and run status.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from . import wdl
from .expr import Ref, evaluate_condition, parse_condition, render_template
from .ir import TestCase, Workflow

MAX_ACTIONS = 5000


class _Stop(Exception):
    def __init__(self, status: str):
        self.status = status


@dataclass
class RunResult:
    events: list[dict] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    status: str = "Succeeded"
    error: str | None = None

    def signature(self):
        return self.events, self.variables, self.status, self.error


# ============================================================================ source platform
class SourceSimulator:
    def __init__(self, wf: Workflow):
        self.wf = wf

    def run(self, case: TestCase) -> RunResult:
        res = RunResult(variables={v.name: copy.deepcopy(v.default) for v in self.wf.variables})
        approvals: dict[str, str] = {}
        self._count = 0

        def resolver(item):
            def resolve(ref: Ref):
                if ref.kind == "field":
                    return case.trigger_body.get(ref.name)
                if ref.kind == "var":
                    return res.variables.get(ref.name)
                if ref.kind == "item":
                    return (item or {}).get(ref.name)
                if ref.kind == "approval":
                    return approvals.get(ref.name)
                raise KeyError(ref)

            return resolve

        def run_steps(steps, item):
            for s in steps:
                self._count += 1
                if self._count > MAX_ACTIONS:
                    raise _Stop("Failed")
                r = resolver(item)
                t = lambda x, r=r: render_template(x, r)
                a = s.attrs
                if s.kind == "If":
                    run_steps(s.then if evaluate_condition(parse_condition(a["condition"]), r) else s.else_, item)
                elif s.kind == "ForEach":
                    for it in t(a["items"]) or []:
                        run_steps(s.body, it)
                elif s.kind == "SetVariable":
                    res.variables[a["name"]] = t(a["value"])
                elif s.kind == "SendEmail":
                    res.events.append(
                        {"kind": "email", "to": t(a["to"]), "subject": t(a["subject"]), "body": t(a["body"])}
                    )
                elif s.kind == "CreateListItem":
                    res.events.append(
                        {"kind": "sp_create", "list": t(a["list"]), "fields": {k: t(v) for k, v in a["fields"].items()}}
                    )
                elif s.kind == "UpdateListItem":
                    res.events.append(
                        {
                            "kind": "sp_update",
                            "list": t(a["list"]),
                            "item_id": t(a["item_id"]),
                            "fields": {k: t(v) for k, v in a["fields"].items()},
                        }
                    )
                elif s.kind == "HttpRequest":
                    res.events.append({"kind": "http", "method": a["method"], "url": t(a["url"]), "body": t(a["body"])})
                elif s.kind == "RequestApproval":
                    res.events.append({"kind": "approval", "assigned_to": t(a["assigned_to"]), "title": t(a["title"])})
                    approvals[s.id] = case.approvals.get(s.id, "Approve")
                elif s.kind == "Delay":
                    res.events.append({"kind": "delay", "minutes": int(a["minutes"])})
                elif s.kind == "Terminate":
                    raise _Stop(a["status"])
                elif s.kind == "PostToTeamsChannel":
                    res.events.append(
                        {"kind": "teams", "team": t(a["team"]), "channel": t(a["channel"]), "message": t(a["message"])}
                    )
                elif s.kind == "CreateTask":
                    res.events.append(
                        {"kind": "planner", "plan": t(a["plan"]), "title": t(a["title"]), "assignee": t(a["assignee"])}
                    )
                elif s.kind == "LogToHistoryList":  # the platform writes to its 'Workflow History' list
                    res.events.append(
                        {"kind": "sp_create", "list": "Workflow History", "fields": {"Description": t(a["message"])}}
                    )
                elif s.kind == "RunPowerShellScript":
                    res.events.append({"kind": "script", "script": t(a["script"])})
                elif s.kind == "AssignToGroup":
                    res.events.append({"kind": "permissions", "group": t(a["group"]), "item_id": t(a["item_id"])})
                elif s.kind == "SendSMS":
                    res.events.append({"kind": "sms", "phone": t(a["phone"]), "message": t(a["message"])})
                else:
                    raise ValueError(f"source simulator has no semantics for {s.kind}")

        try:
            run_steps(self.wf.steps, None)
        except _Stop as stop:
            res.status = stop.status
        except Exception as exc:  # a crash is an observable outcome too
            res.status, res.error = "Failed", f"{type(exc).__name__}: {exc}"
        return res


# ============================================================================ Power Automate
CONNECTOR_EVENTS = {
    ("shared_office365", "SendEmailV2"): lambda p: {
        "kind": "email",
        "to": p.get("emailMessage/To"),
        "subject": p.get("emailMessage/Subject"),
        "body": p.get("emailMessage/Body"),
    },
    ("shared_sharepointonline", "PostItem"): lambda p: {
        "kind": "sp_create",
        "list": p.get("table"),
        "fields": _item_fields(p),
    },
    ("shared_sharepointonline", "PatchItem"): lambda p: {
        "kind": "sp_update",
        "list": p.get("table"),
        "item_id": p.get("id"),
        "fields": _item_fields(p),
    },
    ("shared_approvals", "StartAndWaitForAnApproval"): lambda p: {
        "kind": "approval",
        "assigned_to": p.get("WebhookApprovalCreationInput/assignedTo"),
        "title": p.get("WebhookApprovalCreationInput/title"),
    },
    ("shared_teams", "PostMessageToConversation"): lambda p: {
        "kind": "teams",
        "team": p.get("body/recipient/groupId"),
        "channel": p.get("body/recipient/channelId"),
        "message": p.get("body/messageBody"),
    },
    ("shared_planner", "CreateTask_V3"): lambda p: {
        "kind": "planner",
        "plan": p.get("body/planId"),
        "title": p.get("body/title"),
        "assignee": p.get("body/assignments"),
    },
}


def _item_fields(p: dict) -> dict:
    return {k.split("/", 1)[1]: v for k, v in p.items() if k.startswith("item/")}


def ordered(actions: dict) -> list[tuple[str, dict]]:
    """Order actions in a scope by their runAfter chain (the emitter produces linear chains)."""
    remaining, done, out = dict(actions), set(), []
    while remaining:
        ready = [n for n, a in remaining.items() if set(a.get("runAfter", {})) <= done]
        if not ready:
            raise ValueError(f"runAfter cycle or dangling reference among {sorted(remaining)}")
        for n in sorted(ready, key=list(actions).index):
            out.append((n, remaining.pop(n)))
            done.add(n)
    return out


class FlowSimulator:
    def __init__(self, definition: dict):
        self.d = definition

    def run(self, case: TestCase) -> RunResult:
        res = RunResult()
        ctx = wdl.Context(trigger_body=dict(case.trigger_body), variables=res.variables, items={}, bodies={})
        self._count = 0

        def run_scope(actions: dict):
            for name, a in ordered(actions):
                self._count += 1
                if self._count > MAX_ACTIONS:
                    raise _Stop("Failed")
                typ = a["type"]
                ev = lambda x: wdl.evaluate_value(x, ctx)  # noqa: E731
                if typ == "InitializeVariable":
                    for var in a["inputs"]["variables"]:
                        res.variables[var["name"]] = ev(var.get("value"))
                elif typ == "SetVariable":
                    res.variables[a["inputs"]["name"]] = ev(a["inputs"]["value"])
                elif typ == "If":
                    branch = (
                        a["actions"]
                        if wdl.evaluate_condition(a["expression"], ctx)
                        else a.get("else", {}).get("actions", {})
                    )
                    run_scope(branch)
                elif typ == "Foreach":
                    for it in ev(a["foreach"]) or []:
                        ctx.items[name] = it
                        run_scope(a["actions"])
                    ctx.items.pop(name, None)
                elif typ == "Compose":
                    ctx.bodies[name] = ev(a["inputs"])
                elif typ == "Wait":
                    iv = a["inputs"]["interval"]
                    res.events.append(
                        {"kind": "delay", "minutes": int(iv["count"]) * (60 if iv["unit"] == "Hour" else 1)}
                    )
                elif typ == "Http":
                    i = a["inputs"]
                    res.events.append(
                        {"kind": "http", "method": i["method"], "url": ev(i["uri"]), "body": ev(i.get("body", ""))}
                    )
                elif typ == "Terminate":
                    raise _Stop(a["inputs"]["runStatus"])
                elif typ == "OpenApiConnection":
                    host = a["inputs"]["host"]
                    params = ev(a["inputs"]["parameters"])
                    make = CONNECTOR_EVENTS.get((host["connectionName"], host["operationId"]))
                    if make is None:
                        raise ValueError(
                            f"simulator has no semantics for {host['connectionName']}/{host['operationId']}"
                        )
                    res.events.append(make(params))
                    if host["operationId"] == "StartAndWaitForAnApproval":
                        src = a.get("metadata", {}).get("wmaSourceStep", name)
                        ctx.bodies[name] = {"outcome": case.approvals.get(src, "Approve")}
                else:
                    raise ValueError(f"simulator has no semantics for action type {typ}")

        try:
            run_scope(self.d["actions"])
        except _Stop as stop:
            res.status = stop.status
        except Exception as exc:
            res.status, res.error = "Failed", f"{type(exc).__name__}: {exc}"
        return res


def diff(a: RunResult, b: RunResult) -> list[str]:
    """Human-readable differences between a source run (a) and a flow run (b)."""
    out = []
    if a.status != b.status:
        out.append(f"status {a.status} -> {b.status}")
    if a.error or b.error:
        out.append(f"error: source={a.error} flow={b.error}")
    for i in range(max(len(a.events), len(b.events))):
        x = a.events[i] if i < len(a.events) else None
        y = b.events[i] if i < len(b.events) else None
        if x != y:
            out.append(f"event {i + 1}: source={x} flow={y}")
            break
    for k in sorted(set(a.variables) | set(b.variables)):
        if a.variables.get(k) != b.variables.get(k):
            out.append(f"variable {k}: {a.variables.get(k)!r} -> {b.variables.get(k)!r}")
    return out
