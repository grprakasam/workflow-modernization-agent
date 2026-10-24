"""Command line: `python -m wma convert <file>` and `python -m wma evaluate`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluate import convert, evaluate_samples, scorecard_markdown
from .llm_mapper import BoundedLLMMapper, make_provider
from .parsers import parse_file
from .solution import pack_solution, prepare_solution
from .validate import validate_definition

ROOT = Path(__file__).resolve().parent.parent


def _mapper(args) -> BoundedLLMMapper | None:
    provider = make_provider(args.llm, args.fixtures)
    return BoundedLLMMapper(provider, min_confidence=args.min_confidence) if provider else None


def cmd_convert(args) -> int:
    wf = parse_file(args.file)
    decisions, definition, connectors = convert(wf, _mapper(args))
    out = Path(args.out) / wf.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "definition.json").write_text(json.dumps(definition, indent=2), encoding="utf-8")
    issues = validate_definition(definition)
    flagged = [d for d in decisions.values() if d.method == "unsupported"]
    print(f"{wf.name}: {len(decisions)} steps, {len(flagged)} flagged, connectors: {', '.join(connectors) or 'none'}")
    for d in flagged:
        print(f"  FLAGGED {d.step_id} ({d.source_kind}): {'; '.join(d.issues)}")
    for i in issues:
        print(f"  STATIC  {i}")
    print(f"wrote {out / 'definition.json'}")
    return 1 if issues or (flagged and not args.allow_drafts) else 0


def cmd_evaluate(args) -> int:
    report = evaluate_samples(Path(args.samples), _mapper(args), Path(args.flows) if args.flows else None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / "latest_scorecard.md").write_text(scorecard_markdown(report, args.llm), encoding="utf-8")
    s = report["summary"]
    print(json.dumps(s, indent=2))
    failed = []
    if s["silent_failures"]:
        failed.append(f"{s['silent_failures']} workflow(s) fail behavioral tests without a flagged step")
    if s["static_issues"]:
        failed.append(f"{s['static_issues']} static issue(s) in generated flows")
    if s["auto_converted_rate"] < args.min_auto:
        failed.append(f"auto-conversion {s['auto_converted_rate']:.0%} is below {args.min_auto:.0%}")
    for f in failed:
        print(f"QUALITY GATE FAILED: {f}", file=sys.stderr)
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wma", description="Convert legacy XML/JSON workflows to Power Automate drafts.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("convert", "evaluate"):
        sp = sub.add_parser(name)
        sp.add_argument(
            "--llm",
            choices=["none", "fixture", "anthropic", "openai"],
            default="fixture",
            help="provider for steps without a deterministic rule (default: offline fixtures)",
        )
        sp.add_argument("--fixtures", default=str(ROOT / "samples" / "llm_fixtures.json"))
        sp.add_argument("--min-confidence", type=float, default=0.7)
        if name == "convert":
            sp.add_argument("file")
            sp.add_argument(
                "--allow-drafts", action="store_true", help="allow unresolved flagged steps in draft output"
            )
            sp.add_argument("--out", default="out")
        else:
            sp.add_argument("--samples", default=str(ROOT / "samples" / "workflows"))
            sp.add_argument("--out", default=str(ROOT / "results"))
            sp.add_argument("--flows", default=None, help="also write each generated definition here")
            sp.add_argument("--min-auto", type=float, default=0.0, help="fail if auto-conversion rate is lower")
    sp = sub.add_parser("prepare-solution", help="copy an exported solution and bind one reviewed definition")
    sp.add_argument("definition")
    sp.add_argument("--template", required=True)
    sp.add_argument("--workflow", required=True, help="relative Workflows/name-guid.json")
    sp.add_argument("--out", required=True)
    sp.add_argument("--bindings", help="JSON mapping connector names to existing reference keys")
    sp.add_argument("--zipfile", help="optional new ZIP path; invokes installed PAC to pack, never import")
    args = p.parse_args(argv)
    if args.cmd == "prepare-solution":
        definition = json.loads(Path(args.definition).read_text(encoding="utf-8"))
        bindings = json.loads(Path(args.bindings).read_text(encoding="utf-8")) if args.bindings else {}
        manifest = prepare_solution(args.template, args.workflow, definition, args.out, bindings)
        if args.zipfile:
            pack_solution(args.out, args.zipfile)
        print(json.dumps(manifest, indent=2))
        return 0
    return cmd_convert(args) if args.cmd == "convert" else cmd_evaluate(args)


if __name__ == "__main__":
    sys.exit(main())
