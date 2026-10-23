"""Typed intermediate representation (IR) shared by parsers, mapper, emitter and simulators.

Parsers turn XML or JSON workflow definitions into these models. Everything downstream
works on the IR only, so a new source format needs a parser and nothing else.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

TriggerKind = Literal["item_created", "item_modified", "manual", "schedule", "http"]
VarType = Literal["string", "integer", "float", "boolean", "array"]


class Trigger(BaseModel):
    kind: TriggerKind
    site: str | None = None
    list: str | None = None
    every_minutes: int | None = None


class Variable(BaseModel):
    name: str
    type: VarType = "string"
    default: Any = None


class Step(BaseModel):
    """One source step. `kind` is the legacy step type, e.g. ``SendEmail``."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    then: list[Step] = Field(default_factory=list)
    else_: list[Step] = Field(default_factory=list, alias="else")
    body: list[Step] = Field(default_factory=list)

    def walk(self):
        yield self
        for child in (*self.then, *self.else_, *self.body):
            yield from child.walk()


class TestCase(BaseModel):
    """A behavioral test: a trigger payload plus scripted approval outcomes."""

    __test__ = False  # not a pytest class

    name: str
    trigger_body: dict[str, Any] = Field(default_factory=dict)
    approvals: dict[str, str] = Field(default_factory=dict)  # source step id -> outcome


class Workflow(BaseModel):
    name: str
    source_format: Literal["xml", "json"]
    description: str = ""
    trigger: Trigger
    variables: list[Variable] = Field(default_factory=list)
    steps: list[Step]
    cases: list[TestCase] = Field(default_factory=list)

    def all_steps(self) -> list[Step]:
        return [s for top in self.steps for s in top.walk()]


# ---------------------------------------------------------------------------------------------
# Typed parameters for the step kinds the legacy platform supports. Parsers validate known
# kinds against these models; unknown kinds keep their raw attributes and go to the LLM mapper.
# ---------------------------------------------------------------------------------------------
class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetVariableP(_P):
    name: str
    value: Any


class IfP(_P):
    condition: str


class ForEachP(_P):
    items: str  # a reference such as field:Approvers


class SendEmailP(_P):
    to: str
    subject: str
    body: str = ""


class CreateListItemP(_P):
    list: str
    fields: dict[str, Any]


class UpdateListItemP(_P):
    list: str
    item_id: str
    fields: dict[str, Any]


class HttpRequestP(_P):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str
    body: str = ""


class RequestApprovalP(_P):
    assigned_to: str
    title: str


class DelayP(_P):
    minutes: int


class TerminateP(_P):
    status: Literal["Succeeded", "Failed", "Cancelled"] = "Succeeded"


class PostToTeamsChannelP(_P):
    team: str
    channel: str
    message: str


class CreateTaskP(_P):
    plan: str
    title: str
    assignee: str


class LogToHistoryListP(_P):
    message: str


class RunPowerShellScriptP(_P):
    script: str


class AssignToGroupP(_P):
    group: str
    item_id: str


class SendSmsP(_P):
    phone: str
    message: str


STEP_PARAMS: dict[str, type[_P]] = {
    "SetVariable": SetVariableP,
    "If": IfP,
    "ForEach": ForEachP,
    "SendEmail": SendEmailP,
    "CreateListItem": CreateListItemP,
    "UpdateListItem": UpdateListItemP,
    "HttpRequest": HttpRequestP,
    "RequestApproval": RequestApprovalP,
    "Delay": DelayP,
    "Terminate": TerminateP,
    "PostToTeamsChannel": PostToTeamsChannelP,
    "CreateTask": CreateTaskP,
    "LogToHistoryList": LogToHistoryListP,
    "RunPowerShellScript": RunPowerShellScriptP,
    "AssignToGroup": AssignToGroupP,
    "SendSMS": SendSmsP,
}

CONTROL_KINDS = {"If", "ForEach"}


class WorkflowParseError(ValueError):
    pass


def validate_step_attrs(step: Step) -> None:
    """Validate a step's attributes against its typed parameter model (if the kind is known)."""
    model = STEP_PARAMS.get(step.kind)
    if model is None:
        raise WorkflowParseError(f"step {step.id}: unknown step kind '{step.kind}'")
    try:
        step.attrs = model(**step.attrs).model_dump()
    except ValidationError as exc:
        raise WorkflowParseError(f"step {step.id} ({step.kind}): {exc.errors()[0]['msg']}") from exc
