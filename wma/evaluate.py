"""Convert every sample workflow, check it statically, and test behavioral fidelity by
running each behavioral test case through both simulators."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .emitter import emit
from .ir import Workflow
from .llm_mapper import BoundedLLMMapper
from .mapper import Decision, map_workflow
from .parsers import parse_file
from .simulate import FlowSimulator, SourceSimulator, diff
from .validate import validate_definition

# Heuristic review minutes per converted step, by how it was mapped, plus one-off connection setup.
# They are planning estimates, not measurements; change them to match your team.
REVIEW_MINUTES = {"control": 1.0, "rule": 1.0, "llm": 4.0, "unsupported": 20.0}
CONNECTION_SETUP_MINUTES = 3.0
MANUAL_REBUILD_MINUTES_PER_STEP = 8.0


@dataclass
class WorkflowResult:
    name: str
    source: str
    steps: int
    by_method: dict[str, int]
    connectors: list[str]
    static_issues: list[str]
    cases: int
    cases_passed: int
    case_diffs: dict[str, list[str]] = field(default_factory=dict)
    unsupported: list[dict] = field(default_factory=list)
    llm_mapped: list[dict] = field(default_factory=list)
    review_minutes: float = 0.0
    manual_minutes: float = 0.0

    @property
    def equivalent(self) -> bool:
        return self.cases > 0 and self.cases_passed == self.cases and not self.static_issues


def convert(wf: Workflow, llm: BoundedLLMMapper | None):
    decisions = map_workflow(wf, llm)
    definition, connectors = emit(wf, decisions)
    return decisions, definition, connectors


def evaluate_workflow(path: Path, llm: BoundedLLMMapper | None, out_dir: Path | None = None) -> WorkflowResult:
    wf = parse_file(path)
    decisions, definition, connectors = convert(wf, llm)
    issues = validate_definition(definition)
    by_method: dict[str, int] = {}
    for d in decisions.values():
        by_method[d.method] = by_method.get(d.method, 0) + 1

    passed, diffs = 0, {}
    src, flow = SourceSimulator(wf), FlowSimulator(definition)
    for case in wf.cases:
        a, b = src.run(case), flow.run(case)
        if a.signature() == b.signature():
            passed += 1
        else:
            diffs[case.name] = diff(a, b)

    review = sum(REVIEW_MINUTES[d.method] for d in decisions.values()) + CONNECTION_SETUP_MINUTES * len(connectors)
    manual = MANUAL_REBUILD_MINUTES_PER_STEP * len(decisions) + CONNECTION_SETUP_MINUTES * len(connectors)
    res = WorkflowResult(
        name=wf.name,
        source=path.name,
        steps=len(decisions),
        by_method=by_method,
        connectors=connectors,
        static_issues=issues,
        cases=len(wf.cases),
        cases_passed=passed,
        case_diffs=diffs,
        unsupported=[_brief(d) for d in decisions.values() if d.method == "unsupported"],
        llm_mapped=[_brief(d) for d in decisions.values() if d.method == "llm"],
        review_minutes=review,
        manual_minutes=manual,
    )

    if out_dir:
        target = out_dir / wf.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "definition.json").write_text(json.dumps(definition, indent=2), encoding="utf-8")
        (target / "mapping.json").write_text(
            json.dumps({k: asdict(v) for k, v in decisions.items()}, indent=2, default=str), encoding="utf-8"
        )
    return res


def _brief(d: Decision) -> dict:
    return {
        "step": d.step_id,
        "kind": d.source_kind,
        "target": d.target,
        "confidence": d.confidence,
        "issues": d.issues,
        "rationale": d.rationale,
    }


def evaluate_samples(sample_dir: Path, llm: BoundedLLMMapper | None, out_dir: Path | None = None) -> dict:
    files = sorted([*sample_dir.glob("*.xml"), *sample_dir.glob("*.json")])
    results = [evaluate_workflow(f, llm, out_dir) for f in files]
    steps = sum(r.steps for r in results)
    count = lambda m: sum(r.by_method.get(m, 0) for r in results)  # noqa: E731
    action_steps = steps - count("control")
    cases = sum(r.cases for r in results)
    passed = sum(r.cases_passed for r in results)
    review, manual = sum(r.review_minutes for r in results), sum(r.manual_minutes for r in results)
    summary = {
        "workflows": len(results),
        "steps": steps,
        "action_steps": action_steps,
        "rule_mapped": count("rule"),
        "llm_mapped": count("llm"),
        "unsupported": count("unsupported"),
        "auto_converted_rate": round((count("rule") + count("llm")) / action_steps, 4) if action_steps else 0.0,
        "workflows_fully_automatic": sum(1 for r in results if not r.unsupported),
        "workflows_equivalent": sum(1 for r in results if r.equivalent),
        "cases": cases,
        "cases_passed": passed,
        "case_fidelity": round(passed / cases, 4) if cases else 0.0,
        # a failing case in a workflow with nothing flagged would mean the converter hid a problem
        "silent_failures": sum(
            1 for r in results if r.cases_passed < r.cases and not r.unsupported and not r.static_issues
        ),
        "static_issues": sum(len(r.static_issues) for r in results),
        "review_minutes": review,
        "manual_rebuild_minutes": manual,
        "review_effort_saved": round(1 - review / manual, 4) if manual else 0.0,
    }
    return {"summary": summary, "workflows": [asdict(r) | {"equivalent": r.equivalent} for r in results]}


def scorecard_markdown(report: dict, provider: str) -> str:
    s = report["summary"]
    pct = lambda x: f"{x * 100:.0f}%"  # noqa: E731
    lines = [
        "# Workflow Modernization Agent: scorecard",
        "",
        f"LLM provider for steps without a rule: `{provider}`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sample workflows | {s['workflows']} |",
        f"| Steps (actions + control flow) | {s['steps']} ({s['action_steps']} actions) |",
        f"| Actions converted automatically | {s['rule_mapped'] + s['llm_mapped']} of {s['action_steps']} ({pct(s['auto_converted_rate'])}): {s['rule_mapped']} by rule, {s['llm_mapped']} by bounded LLM |",
        f"| Actions flagged for manual rebuild | {s['unsupported']} |",
        f"| Workflows converted with no flagged step | {s['workflows_fully_automatic']} of {s['workflows']} |",
        f"| Behavioral test cases passed | {s['cases_passed']} of {s['cases']} ({pct(s['case_fidelity'])}) |",
        f"| Workflows behavior-equivalent on every case | {s['workflows_equivalent']} of {s['workflows']} |",
        f"| Failing cases without a flagged step (silent failures) | {s['silent_failures']} |",
        f"| Static issues in generated flows | {s['static_issues']} |",
        f"| Estimated review effort | {s['review_minutes']:.0f} min vs {s['manual_rebuild_minutes']:.0f} min manual rebuild ({pct(s['review_effort_saved'])} less) |",
        "",
        "Review-effort figures use the heuristic weights in `wma/evaluate.py`; they are estimates, not measurements.",
        "",
        "## Per workflow",
        "",
        "| Workflow | Source | Steps | Rule | LLM | Flagged | Cases passed | Equivalent |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for w in report["workflows"]:
        m = w["by_method"]
        lines.append(
            f"| {w['name']} | {w['source']} | {w['steps']} | {m.get('rule', 0)} | {m.get('llm', 0)} | "
            f"{m.get('unsupported', 0)} | {w['cases_passed']}/{w['cases']} | {'yes' if w['equivalent'] else 'no'} |"
        )
    lines += ["", "## Flagged steps and differences", ""]
    for w in report["workflows"]:
        for u in w["unsupported"]:
            lines.append(f"- **{w['name']} / {u['step']} ({u['kind']})**: {'; '.join(u['issues'])}")
        for case, d in w["case_diffs"].items():
            lines.append(f"  - case *{case}*: {d[0] if d else 'differs'}")
    lines += ["", "## Steps mapped by the bounded LLM", ""]
    for w in report["workflows"]:
        for m in w["llm_mapped"]:
            lines.append(
                f"- {w['name']} / {m['step']} ({m['kind']}) -> `{m['target']}` "
                f"(confidence {m['confidence']:.2f}): {m['rationale']}"
            )
    return "\n".join(lines) + "\n"
