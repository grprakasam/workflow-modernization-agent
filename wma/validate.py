"""Static checks on a generated flow definition, run before any simulation."""

from __future__ import annotations

from . import wdl
from .catalog import CATALOG

ALLOWED_TYPES = {
    "InitializeVariable",
    "SetVariable",
    "If",
    "Foreach",
    "Compose",
    "Wait",
    "Http",
    "Terminate",
    "OpenApiConnection",
}
ALLOWED_OPS = {(t.connector, t.operation_id) for t in CATALOG.values() if t.connector}


def _calls(node, out):
    if node[0] == "call":
        out.append((node[1], node[2]))
        for a in node[2]:
            _calls(a, out)
    elif node[0] == "get":
        _calls(node[1], out)
        _calls(node[2], out)
    return out


def validate_definition(d: dict) -> list[str]:
    issues: list[str] = []
    for key in ("$schema", "contentVersion", "triggers", "actions"):
        if key not in d:
            issues.append(f"missing top-level '{key}'")
    if len(d.get("triggers", {})) != 1:
        issues.append("a flow needs exactly one trigger")

    names: list[str] = []
    initialized: set[str] = set()
    all_actions: dict[str, dict] = {}

    def collect(actions: dict):
        for n, a in actions.items():
            names.append(n)
            all_actions[n] = a
            if a.get("type") == "InitializeVariable":
                initialized.update(v["name"] for v in a["inputs"]["variables"])
            collect(a.get("actions", {}))
            collect(a.get("else", {}).get("actions", {}))

    collect(d.get("actions", {}))
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        issues.append(f"duplicate action names: {sorted(dupes)}")

    def check_scope(actions: dict, loops: list[str]):
        for n, a in actions.items():
            typ = a.get("type")
            if typ not in ALLOWED_TYPES:
                issues.append(f"{n}: action type '{typ}' is not allowed")
            for dep in a.get("runAfter", {}):
                if dep not in actions:
                    issues.append(f"{n}: runAfter '{dep}' is not in the same scope")
            if typ == "OpenApiConnection":
                host = a.get("inputs", {}).get("host", {})
                if (host.get("connectionName"), host.get("operationId")) not in ALLOWED_OPS:
                    issues.append(
                        f"{n}: connector operation {host.get('connectionName')}/{host.get('operationId')} is not in the catalog"
                    )
            if typ == "SetVariable" and a["inputs"]["name"] not in initialized:
                issues.append(f"{n}: variable '{a['inputs']['name']}' is never initialized")
            exprs = wdl.expressions_in({k: v for k, v in a.items() if k not in ("actions", "else", "metadata")})
            for e in exprs:
                try:
                    calls = _calls(wdl.parse(e), [])
                except wdl.WdlError as exc:
                    issues.append(f"{n}: expression does not parse: {exc}")
                    continue
                for fn, args in calls:
                    if fn not in wdl.FUNCTIONS and fn not in ("parameters", "triggerOutputs"):
                        issues.append(f"{n}: function '{fn}' is not supported")
                    arg = args[0][1] if args and args[0][0] == "lit" else None
                    if fn == "variables" and arg not in initialized:
                        issues.append(f"{n}: variables('{arg}') is never initialized")
                    if fn == "items" and arg not in loops:
                        issues.append(f"{n}: items('{arg}') is not an enclosing loop")
                    if fn == "body" and arg not in all_actions:
                        issues.append(f"{n}: body('{arg}') refers to a missing action")
            if typ == "Foreach":
                check_scope(a.get("actions", {}), loops + [n])
            elif typ == "If":
                check_scope(a.get("actions", {}), loops)
                check_scope(a.get("else", {}).get("actions", {}), loops)

    check_scope(d.get("actions", {}), [])
    return issues
