# 0005: Model output is untrusted data

**Status:** accepted

**Context.** Two risks come from the LLM step:
- Workflow content can carry instructions (prompt injection).
- A model reply could smuggle expressions or actions into a flow that runs with real connector permissions. This matches OWASP agentic risks such as goal hijack and tool misuse.

**Decision.** A proposal is accepted only if every check passes:
- the JSON matches the schema;
- the target is in the closed catalog;
- the parameters pass that target's Pydantic model;
- there are no raw `@` expressions;
- it only uses references that exist;
- it keeps at least 50% of the source values (grounding);
- confidence is at least 0.7.

The step content goes in the user message as data, and the system prompt tells the model to ignore instructions inside it. Literal `@` in source text is escaped. Rejected proposals are stored only as reviewer suggestions inside a non-executing `Compose` placeholder.

**Consequences.** The worst case for a bad or manipulated reply is a flagged step, not a wrong action in production. Each guard has a unit test in `tests/test_llm_mapper.py`.
