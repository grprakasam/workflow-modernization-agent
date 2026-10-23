# 0002: Deterministic rules first, bounded LLM second, flag otherwise

**Status:** accepted

**Context.** Most workflow steps have an obvious Power Automate equivalent. A few do not, and an unconstrained LLM can produce plausible but wrong actions.

**Decision.**
- Map known step kinds with deterministic rules.
- Send only the remaining steps to an LLM, which must choose from a closed catalog and pass validation, grounding and confidence gates (ADR 0005).
- Anything that fails becomes a visible TODO placeholder.

**Consequences.** Rule-mapped steps are reproducible and free. LLM use is limited to the hard cases, so cost and risk stay small. With `--llm none` the tool still works: it flags more steps, but it never guesses.
