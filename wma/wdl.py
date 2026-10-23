"""A small evaluator for the Power Automate / Logic Apps workflow-definition expression subset
that the emitter produces. Used by the flow simulator and by the structural validator.

Supported: '@expr' values, '@{expr}' string interpolation, '@@' escapes, function calls,
'?['x']' / '['x']' / '.x' property access, string/number/bool/null literals.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .expr import compare, format_value


class WdlError(ValueError):
    pass


_TOK = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>-?\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<punct>\?\[|\[|\]|\(|\)|,|\.))"
)


def _tokens(text: str):
    pos, out = 0, []
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOK.match(text, pos)
        if not m or m.end() == pos:
            raise WdlError(f"bad expression near {text[pos : pos + 25]!r}")
        out.append((m.lastgroup, m.group(m.lastgroup)))
        pos = m.end()
    return out


class _Parser:
    """Parses into a tiny AST: ('lit', v) | ('call', name, [args]) | ('get', target, key)."""

    def __init__(self, text):
        self.t, self.i, self.src = _tokens(text), 0, text

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, expected=None):
        tok = self.peek()
        if expected and tok[1] != expected:
            raise WdlError(f"expected {expected!r} in {self.src!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.i != len(self.t):
            raise WdlError(f"trailing tokens in {self.src!r}")
        return node

    def expr(self):
        kind, val = self.take()
        if kind == "str":
            node = ("lit", val[1:-1].replace("''", "'"))
        elif kind == "num":
            node = ("lit", float(val) if "." in val else int(val))
        elif kind == "name" and val in ("true", "false", "null") and self.peek()[1] != "(":
            node = ("lit", {"true": True, "false": False, "null": None}[val])
        elif kind == "name":
            self.take("(")
            args = []
            if self.peek()[1] != ")":
                args.append(self.expr())
                while self.peek()[1] == ",":
                    self.take()
                    args.append(self.expr())
            self.take(")")
            node = ("call", val, args)
        else:
            raise WdlError(f"unexpected {val!r} in {self.src!r}")
        while self.peek()[1] in ("?[", "[", "."):
            p = self.take()[1]
            if p == ".":
                node = ("get", node, ("lit", self.take()[1]))
            else:
                key = self.expr()
                self.take("]")
                node = ("get", node, key)
        return node


def parse(expr_text: str):
    return _Parser(expr_text).parse()


class Context:
    """Run-time state the expression functions read."""

    def __init__(self, trigger_body: dict, variables: dict, items: dict, bodies: dict):
        self.trigger_body, self.variables, self.items, self.bodies = trigger_body, variables, items, bodies


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError) as exc:
        raise WdlError(f"not a number: {v!r}") from exc


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "triggerBody": lambda ctx: ctx.trigger_body,
    "variables": lambda ctx, name: ctx.variables[name],
    "items": lambda ctx, name: ctx.items[name],
    "body": lambda ctx, name: ctx.bodies.get(name),
    "concat": lambda ctx, *a: "".join(format_value(x) for x in a),
    "string": lambda ctx, v: format_value(v),
    "int": lambda ctx, v: int(_num(v)),
    "float": lambda ctx, v: float(_num(v)),
    "add": lambda ctx, a, b: _num(a) + _num(b),
    "sub": lambda ctx, a, b: _num(a) - _num(b),
    "length": lambda ctx, v: len(v or []),
    "toLower": lambda ctx, v: str(v).lower(),
    "toUpper": lambda ctx, v: str(v).upper(),
    "equals": lambda ctx, a, b: compare("==", a, b),
    "greater": lambda ctx, a, b: compare(">", a, b),
    "less": lambda ctx, a, b: compare("<", a, b),
    "greaterOrEquals": lambda ctx, a, b: compare(">=", a, b),
    "lessOrEquals": lambda ctx, a, b: compare("<=", a, b),
    "and": lambda ctx, *a: all(a),
    "or": lambda ctx, *a: any(a),
    "not": lambda ctx, a: not a,
    "contains": lambda ctx, a, b: b in (a or ""),
    "coalesce": lambda ctx, *a: next((x for x in a if x is not None), None),
}


def _eval(node, ctx: Context):
    tag = node[0]
    if tag == "lit":
        return node[1]
    if tag == "get":
        target, key = _eval(node[1], ctx), _eval(node[2], ctx)
        if target is None:
            return None
        if isinstance(target, dict):
            return target.get(key)
        if isinstance(target, list) and isinstance(key, int):
            return target[key] if -len(target) <= key < len(target) else None
        return None
    fn = FUNCTIONS.get(node[1])
    if fn is None:
        raise WdlError(f"function '{node[1]}' is not supported by the simulator")
    return fn(ctx, *[_eval(a, ctx) for a in node[2]])


_INTERP = re.compile(r"(?<!@)@\{(.*?)\}")  # '@@{' is an escaped literal, not interpolation


def evaluate_value(value: Any, ctx: Context) -> Any:
    """Evaluate a workflow-definition value: literals, '@expr', '@{...}' interpolation, nested objects."""
    if isinstance(value, dict):
        return {k: evaluate_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [evaluate_value(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    if value.startswith("@") and not value.startswith("@@") and not value.startswith("@{"):
        return _eval(parse(value[1:]), ctx)
    # interpolation and escapes
    out, last = [], 0
    for m in _INTERP.finditer(value):
        out.append(value[last : m.start()].replace("@@", "@"))
        out.append(format_value(_eval(parse(m.group(1)), ctx)))
        last = m.end()
    out.append(value[last:].replace("@@", "@"))
    return "".join(out)


def evaluate_condition(obj: Any, ctx: Context) -> bool:
    """Evaluate an 'If' action expression object such as {"and": [{"greater": [a, b]}]}."""
    if not isinstance(obj, dict) or len(obj) != 1:
        raise WdlError(f"bad condition object: {obj!r}")
    ((op, args),) = obj.items()
    if op == "and":
        return all(evaluate_condition(a, ctx) for a in args)
    if op == "or":
        return any(evaluate_condition(a, ctx) for a in args)
    if op == "not":
        return not evaluate_condition(args, ctx)
    if op in ("equals", "greater", "less", "greaterOrEquals", "lessOrEquals"):
        a, b = (evaluate_value(x, ctx) for x in args)
        return FUNCTIONS[op](ctx, a, b)
    raise WdlError(f"unsupported condition operator '{op}'")


def expressions_in(value: Any) -> list[str]:
    """Every expression string inside a value, for static validation."""
    if isinstance(value, dict):
        return [e for v in value.values() for e in expressions_in(v)]
    if isinstance(value, list):
        return [e for v in value for e in expressions_in(v)]
    if not isinstance(value, str):
        return []
    if value.startswith("@") and not value.startswith("@@") and not value.startswith("@{"):
        return [value[1:]]
    return _INTERP.findall(value)
