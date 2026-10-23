"""Emit a Power Automate cloud-flow definition (Workflow Definition Language) from the IR."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .catalog import CATALOG
from .expr import Ref, condition_to_wdl, escape_literal, parse_condition, refs_in, template_to_wdl
from .ir import Step, Workflow
from .mapper import Decision

SCHEMA = "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#"
DEFAULT_SITE = "https://contoso.sharepoint.com/sites/Operations"
NS = uuid.UUID("6f1c2d3e-0000-4000-8000-0000000000aa")  # stable operationMetadataId per step

ACTION_PREFIX = {
    "outlook.send_email": "Send_email",
    "sharepoint.create_item": "Create_item",
    "sharepoint.update_item": "Update_item",
    "approvals.start_and_wait": "Start_and_wait_for_an_approval",
    "teams.post_message": "Post_message",
    "planner.create_task": "Create_task",
    "http.request": "HTTP",
    "core.compose": "Compose",
    "core.wait": "Delay",
    "core.set_variable": "Set_variable",
    "core.terminate": "Terminate",
    "If": "Condition",
    "ForEach": "Apply_to_each",
    None: "TODO_manual_rebuild",
}
VAR_TYPES = {"string": "string", "integer": "integer", "float": "float", "boolean": "boolean", "array": "array"}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


class Emitter:
    def __init__(self, wf: Workflow, decisions: dict[str, Decision]):
        self.wf, self.d = wf, decisions
        self.site = wf.trigger.site or DEFAULT_SITE
        self.names: dict[str, str] = {}
        for s in wf.all_steps():
            key = s.kind if s.kind in ("If", "ForEach") else decisions[s.id].target
            self.names[s.id] = f"{ACTION_PREFIX[key]}_{_safe(s.id)}"
        self.connectors: set[str] = set()

    # ------------------------------------------------------------------ references
    def _translator(self, loops: list[str]):
        def tr(ref: Ref) -> str:
            if ref.kind == "field":
                return f"triggerBody()?['{ref.name}']"
            if ref.kind == "var":
                return f"variables('{ref.name}')"
            if ref.kind == "item":
                if not loops:
                    raise ValueError(f"item:{ref.name} used outside a loop")
                return f"items('{loops[-1]}')?['{ref.name}']"
            if ref.kind == "approval":
                return f"body('{self.names[ref.name]}')?['outcome']"
            raise ValueError(ref)

        return tr

    def _val(self, value: Any, loops: list[str]) -> Any:
        if isinstance(value, dict):
            return {k: self._val(v, loops) for k, v in value.items()}
        if isinstance(value, str):
            if refs_in(value):
                return template_to_wdl(value, self._translator(loops))
            return escape_literal(value)
        return value

    # ------------------------------------------------------------------ actions
    def _connector(self, target_key: str, params: dict) -> dict:
        t = CATALOG[target_key]
        self.connectors.add(t.connector)
        return {
            "type": "OpenApiConnection",
            "inputs": {
                "host": {
                    "apiId": f"/providers/Microsoft.PowerApps/apis/{t.connector}",
                    "connectionName": t.connector,
                    "operationId": t.operation_id,
                },
                "parameters": params,
                "authentication": "@parameters('$authentication')",
            },
        }

    def _action(self, step: Step, dec: Decision, loops: list[str]) -> dict:
        v = lambda x: self._val(x, loops)  # noqa: E731
        p, key = dec.params, dec.target
        if dec.method == "unsupported":
            note = {
                "todo": f"No Power Automate equivalent was accepted for {step.kind}. Rebuild manually.",
                "source_step": step.id,
                "source_attrs": step.attrs,
                "issues": dec.issues,
                "suggestion": dec.suggestion,
            }
            return {"type": "Compose", "inputs": escape_literal(json.dumps(note, default=str))}
        if key == "outlook.send_email":
            return self._connector(
                key,
                {
                    "emailMessage/To": v(p["to"]),
                    "emailMessage/Subject": v(p["subject"]),
                    "emailMessage/Body": v(p["body"]),
                },
            )
        if key == "sharepoint.create_item":
            return self._connector(
                key,
                {"dataset": self.site, "table": v(p["list"]), **{f"item/{k}": v(x) for k, x in p["fields"].items()}},
            )
        if key == "sharepoint.update_item":
            return self._connector(
                key,
                {
                    "dataset": self.site,
                    "table": v(p["list"]),
                    "id": v(p["item_id"]),
                    **{f"item/{k}": v(x) for k, x in p["fields"].items()},
                },
            )
        if key == "approvals.start_and_wait":
            return self._connector(
                key,
                {
                    "approvalType": "Basic",
                    "WebhookApprovalCreationInput/title": v(p["title"]),
                    "WebhookApprovalCreationInput/assignedTo": v(p["assigned_to"]),
                },
            )
        if key == "teams.post_message":
            return self._connector(
                key,
                {
                    "poster": "Flow bot",
                    "location": "Channel",
                    "body/recipient/groupId": v(p["team"]),
                    "body/recipient/channelId": v(p["channel"]),
                    "body/messageBody": v(p["message"]),
                },
            )
        if key == "planner.create_task":
            return self._connector(
                key, {"body/planId": v(p["plan"]), "body/title": v(p["title"]), "body/assignments": v(p["assignee"])}
            )
        if key == "http.request":
            return {"type": "Http", "inputs": {"method": p["method"], "uri": v(p["url"]), "body": v(p["body"])}}
        if key == "core.compose":
            return {"type": "Compose", "inputs": v(p["text"])}
        if key == "core.wait":
            return {"type": "Wait", "inputs": {"interval": {"count": int(p["minutes"]), "unit": "Minute"}}}
        if key == "core.set_variable":
            return {"type": "SetVariable", "inputs": {"name": p["name"], "value": v(p["value"])}}
        if key == "core.terminate":
            return {"type": "Terminate", "inputs": {"runStatus": p["status"]}}
        raise ValueError(f"no emitter for {key}")

    def _steps(self, steps: list[Step], loops: list[str]) -> dict:
        actions, prev = {}, None
        for step in steps:
            name, dec = self.names[step.id], self.d[step.id]
            if step.kind == "If":
                body = {
                    "type": "If",
                    "expression": condition_to_wdl(parse_condition(step.attrs["condition"]), self._translator(loops)),
                    "actions": self._steps(step.then, loops),
                    "else": {"actions": self._steps(step.else_, loops)},
                }
            elif step.kind == "ForEach":
                body = {
                    "type": "Foreach",
                    "foreach": template_to_wdl(step.attrs["items"], self._translator(loops)),
                    "actions": self._steps(step.body, loops + [name]),
                }
            else:
                body = self._action(step, dec, loops)
            body["runAfter"] = {prev: ["Succeeded"]} if prev else {}
            body["metadata"] = {
                "operationMetadataId": str(uuid.uuid5(NS, f"{self.wf.name}/{step.id}")),
                "wmaSourceStep": step.id,
                "wmaMethod": dec.method,
            }
            actions[name] = body
            prev = name
        return actions

    # ------------------------------------------------------------------ trigger + definition
    def _trigger(self) -> dict:
        t = self.wf.trigger
        if t.kind in ("item_created", "item_modified"):
            self.connectors.add("shared_sharepointonline")
            op = "GetOnNewItems" if t.kind == "item_created" else "GetOnUpdatedItems"
            name = "When_an_item_is_created" if t.kind == "item_created" else "When_an_item_is_created_or_modified"
            return {
                name: {
                    "type": "OpenApiConnection",
                    "recurrence": {"interval": 1, "frequency": "Minute"},
                    "splitOn": "@triggerOutputs()?['body/value']",
                    "inputs": {
                        "host": {
                            "apiId": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
                            "connectionName": "shared_sharepointonline",
                            "operationId": op,
                        },
                        "parameters": {"dataset": self.site, "table": t.list},
                        "authentication": "@parameters('$authentication')",
                    },
                }
            }
        if t.kind == "schedule":
            return {
                "Recurrence": {
                    "type": "Recurrence",
                    "recurrence": {"frequency": "Minute", "interval": t.every_minutes or 60},
                }
            }
        fields = sorted(
            {
                r.name
                for s in self.wf.all_steps()
                for v in s.attrs.values()
                for r in (refs_in(v) if isinstance(v, str) else [])
                if r.kind == "field"
            }
        )
        schema = {"type": "object", "properties": {f: {} for f in fields}}
        kind = "Button" if t.kind == "manual" else "Http"
        return {"manual": {"type": "Request", "kind": kind, "inputs": {"schema": schema}}}

    def emit(self) -> dict:
        triggers = self._trigger()
        actions, prev = {}, None
        for var in self.wf.variables:
            name = f"Initialize_{_safe(var.name)}"
            actions[name] = {
                "type": "InitializeVariable",
                "runAfter": {prev: ["Succeeded"]} if prev else {},
                "inputs": {
                    "variables": [{"name": var.name, "type": VAR_TYPES[var.type], "value": self._val(var.default, [])}]
                },
            }
            prev = name
        body = self._steps(self.wf.steps, [])
        if prev and body:
            first = next(iter(body))
            body[first]["runAfter"] = {prev: ["Succeeded"]}
        actions.update(body)
        return {
            "$schema": SCHEMA,
            "contentVersion": "1.0.0.0",
            "parameters": {
                "$connections": {"defaultValue": {}, "type": "Object"},
                "$authentication": {"defaultValue": {}, "type": "SecureObject"},
            },
            "triggers": triggers,
            "actions": actions,
        }


def emit(wf: Workflow, decisions: dict[str, Decision]) -> tuple[dict, list[str]]:
    em = Emitter(wf, decisions)
    definition = em.emit()
    return definition, sorted(c for c in em.connectors if c)
