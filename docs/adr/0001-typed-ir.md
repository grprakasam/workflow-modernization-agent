# 0001: A typed IR between parsers and emitter

**Status:** accepted

**Context.** Legacy workflows arrive in more than one format, and the emitter, simulators and evaluator should not care which.

**Decision.** Parse every format into one Pydantic IR: `Workflow`, `Trigger`, `Variable`, a tree of `Step` and `TestCase`. Validate step parameters with per-kind models and check every reference at parse time.

**Consequences.** A new source format needs only a parser. Errors such as an undeclared variable or `item:` outside a loop are caught before mapping, with the step id in the message.
