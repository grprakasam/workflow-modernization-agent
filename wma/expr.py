"""The legacy expression language: references, templates and conditions.

References   field:Amount   var:Status   item:Email   approval:s3
Templates    "Invoice {field:Title} needs review"
Conditions   field:Amount > 5000 and (var:Region == 'EU' or approval:s3 != 'Approve')

This module parses that language, evaluates it (for the source simulator) and translates it
to Power Automate workflow-definition expressions (for the emitter).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REF_KINDS = ("field", "var", "item", "approval")
_REF = r"(?:field|var|item|approval):[A-Za-z_][A-Za-z0-9_]*"
TEMPLATE_REF = re.compile(r"\{(" + _REF + r")\}")
_TOKEN = re.compile(
    r"\s*(?:(?P<ref>" + _REF + r")|(?P<str>'(?:[^']|'')*')|(?P<num>-?\d+(?:\.\d+)?)"
    r"|(?P<op>==|!=|>=|<=|>|<)|(?P<kw>\band\b|\bor\b|\bnot\b|\btrue\b|\bfalse\b)|(?P<paren>[()]))"
)


class ExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class Ref:
    kind: str
    name: str

    @classmethod
    def parse(cls, text: str) -> Ref:
        kind, name = text.split(":", 1)
        if kind not in REF_KINDS:
            raise ExpressionError(f"unknown reference kind '{kind}'")
        return cls(kind, name)


@dataclass(frozen=True)
class Lit:
    value: Any


@dataclass(frozen=True)
class Cmp:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class BoolOp:
    op: str  # and | or
    items: tuple


@dataclass(frozen=True)
class Not:
    item: Any


# ---------------------------------------------------------------------------------- parsing
def _tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ExpressionError(f"cannot parse condition near: {text[pos : pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.i != len(self.t):
            raise ExpressionError(f"unexpected token {self.peek()[1]!r}")
        return node

    def or_(self):
        items = [self.and_()]
        while self.peek() == ("kw", "or"):
            self.take()
            items.append(self.and_())
        return items[0] if len(items) == 1 else BoolOp("or", tuple(items))

    def and_(self):
        items = [self.not_()]
        while self.peek() == ("kw", "and"):
            self.take()
            items.append(self.not_())
        return items[0] if len(items) == 1 else BoolOp("and", tuple(items))

    def not_(self):
        if self.peek() == ("kw", "not"):
            self.take()
            return Not(self.not_())
        return self.cmp()

    def cmp(self):
        if self.peek() == ("paren", "("):
            self.take()
            node = self.or_()
            if self.take() != ("paren", ")"):
                raise ExpressionError("missing ')'")
            return node
        left = self.operand()
        if self.peek()[0] == "op":
            op = self.take()[1]
            return Cmp(op, left, self.operand())
        return Cmp("==", left, Lit(True))  # bare operand: truthy test

    def operand(self):
        kind, val = self.take()
        if kind == "ref":
            return Ref.parse(val)
        if kind == "str":
            return Lit(val[1:-1].replace("''", "'"))
        if kind == "num":
            return Lit(float(val) if "." in val else int(val))
        if kind == "kw" and val in ("true", "false"):
            return Lit(val == "true")
        raise ExpressionError(f"expected a value, got {val!r}")


def parse_condition(text: str):
    return _Parser(_tokenize(text)).parse()


def refs_in(text: Any) -> list[Ref]:
    """All references used in a template string (or a bare reference)."""
    if not isinstance(text, str):
        return []
    if re.fullmatch(_REF, text.strip()):
        return [Ref.parse(text.strip())]
    return [Ref.parse(m) for m in TEMPLATE_REF.findall(text)]


def condition_refs(node) -> list[Ref]:
    if isinstance(node, Ref):
        return [node]
    if isinstance(node, Cmp):
        return condition_refs(node.left) + condition_refs(node.right)
    if isinstance(node, BoolOp):
        return [r for item in node.items for r in condition_refs(item)]
    if isinstance(node, Not):
        return condition_refs(node.item)
    return []


# ------------------------------------------------------------------------------- evaluation
Resolver = Callable[[Ref], Any]


def format_value(value: Any) -> str:
    """How both platforms render a value inside text (ints without '.0', booleans lower-case)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if value is None:
        return ""
    return str(value)


def render_template(text: Any, resolve: Resolver) -> Any:
    """Fill a template. A template that is exactly one reference keeps the referenced type."""
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if re.fullmatch(_REF, stripped):
        return resolve(Ref.parse(stripped))
    whole = re.fullmatch(r"\{(" + _REF + r")\}", stripped)
    if whole:
        return resolve(Ref.parse(whole.group(1)))
    return TEMPLATE_REF.sub(lambda m: format_value(resolve(Ref.parse(m.group(1)))), text)


def compare(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if a is None or b is None:
        return False
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def evaluate_condition(node, resolve: Resolver) -> bool:
    if isinstance(node, BoolOp):
        results = (evaluate_condition(i, resolve) for i in node.items)
        return all(results) if node.op == "and" else any(results)
    if isinstance(node, Not):
        return not evaluate_condition(node.item, resolve)
    if isinstance(node, Cmp):
        val = lambda x: resolve(x) if isinstance(x, Ref) else x.value  # noqa: E731
        return compare(node.op, val(node.left), val(node.right))
    raise ExpressionError(f"not a condition: {node!r}")


# --------------------------------------------------------------- translation to Power Automate
RefTranslator = Callable[[Ref], str]  # returns a WDL expression body, e.g. "triggerBody()?['Amount']"

_WDL_OPS = {"==": "equals", ">": "greater", "<": "less", ">=": "greaterOrEquals", "<=": "lessOrEquals"}


def escape_literal(text: str) -> str:
    """A literal string in a workflow definition must not start an expression: '@' becomes '@@'."""
    return text.replace("@", "@@")


def template_to_wdl(text: Any, translate: RefTranslator) -> Any:
    """Translate a template into a WDL value: '@expr' for a single reference, '@{...}' interpolation otherwise."""
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    single = re.fullmatch(_REF, stripped) or re.fullmatch(r"\{(" + _REF + r")\}", stripped)
    if single:
        ref_text = single.group(1) if single.groups() else stripped
        return "@" + translate(Ref.parse(ref_text))
    parts, last = [], 0
    for m in TEMPLATE_REF.finditer(text):
        parts.append(escape_literal(text[last : m.start()]))
        parts.append("@{" + translate(Ref.parse(m.group(1))) + "}")
        last = m.end()
    parts.append(escape_literal(text[last:]))
    return "".join(parts)


def _operand_to_wdl(node, translate: RefTranslator):
    if isinstance(node, Ref):
        return "@" + translate(node)
    value = node.value
    return escape_literal(value) if isinstance(value, str) else value


def condition_to_wdl(node, translate: RefTranslator) -> dict:
    """Translate a condition into the expression object used by the Power Automate 'If' action."""

    def inner(n):
        if isinstance(n, BoolOp):
            return {n.op: [inner(i) for i in n.items]}
        if isinstance(n, Not):
            return {"not": inner(n.item)}
        if isinstance(n, Cmp):
            left, right = _operand_to_wdl(n.left, translate), _operand_to_wdl(n.right, translate)
            if n.op == "!=":
                return {"not": {"equals": [left, right]}}
            return {_WDL_OPS[n.op]: [left, right]}
        raise ExpressionError(f"not a condition: {n!r}")

    out = inner(node)
    return out if set(out) & {"and", "or"} else {"and": [out]}
