"""The closed catalog of Power Automate actions the converter may emit.

Both the deterministic rules and the LLM mapper must pick a key from this catalog and supply
parameters that validate against the key's parameter model. Nothing outside the catalog can
reach a generated flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SendEmail(_P):
    to: str
    subject: str
    body: str = ""


class SpCreate(_P):
    list: str
    fields: dict[str, Any]


class SpUpdate(_P):
    list: str
    item_id: str
    fields: dict[str, Any]


class Http(_P):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str
    body: str = ""


class Approval(_P):
    assigned_to: str
    title: str


class TeamsPost(_P):
    team: str
    channel: str
    message: str


class PlannerTask(_P):
    plan: str
    title: str
    assignee: str


class Compose(_P):
    text: str


class Wait(_P):
    minutes: int


class SetVariable(_P):
    name: str
    value: Any


class Terminate(_P):
    status: Literal["Succeeded", "Failed", "Cancelled"] = "Succeeded"


@dataclass(frozen=True)
class Target:
    key: str
    params: type[_P]
    label: str  # human description shown to the LLM and in reports
    connector: str | None = None  # shared_* connection name, if a connector is needed
    operation_id: str | None = None


SHAREPOINT, OUTLOOK, APPROVALS, TEAMS, PLANNER = (
    "shared_sharepointonline",
    "shared_office365",
    "shared_approvals",
    "shared_teams",
    "shared_planner",
)

CATALOG: dict[str, Target] = {
    t.key: t
    for t in [
        Target("outlook.send_email", SendEmail, "Office 365 Outlook: Send an email (V2)", OUTLOOK, "SendEmailV2"),
        Target("sharepoint.create_item", SpCreate, "SharePoint: Create item in a list", SHAREPOINT, "PostItem"),
        Target("sharepoint.update_item", SpUpdate, "SharePoint: Update item in a list", SHAREPOINT, "PatchItem"),
        Target(
            "approvals.start_and_wait",
            Approval,
            "Approvals: Start and wait for an approval (outcome 'Approve' or 'Reject')",
            APPROVALS,
            "StartAndWaitForAnApproval",
        ),
        Target(
            "teams.post_message",
            TeamsPost,
            "Microsoft Teams: Post message in a chat or channel",
            TEAMS,
            "PostMessageToConversation",
        ),
        Target("planner.create_task", PlannerTask, "Planner: Create a task", PLANNER, "CreateTask_V3"),
        Target("http.request", Http, "HTTP: call an external endpoint"),
        Target("core.compose", Compose, "Compose: build a value (no side effect)"),
        Target("core.wait", Wait, "Delay: wait a number of minutes"),
        Target("core.set_variable", SetVariable, "Set variable"),
        Target("core.terminate", Terminate, "Terminate the run with a status"),
    ]
}

UNSUPPORTED = "UNSUPPORTED"


def catalog_for_prompt() -> list[dict]:
    """A compact JSON-able view of the catalog for the LLM prompt."""
    out = []
    for t in CATALOG.values():
        schema = t.params.model_json_schema()
        out.append(
            {
                "key": t.key,
                "description": t.label,
                "params": {k: v.get("type", v.get("enum", "any")) for k, v in schema["properties"].items()},
                "required": schema.get("required", []),
            }
        )
    return out
