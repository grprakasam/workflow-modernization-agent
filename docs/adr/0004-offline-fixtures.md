# 0004: Offline fixtures for CI; live providers optional

**Status:** accepted

**Context.** CI must be deterministic and must not need API keys. The LLM path still needs end-to-end coverage.

**Decision.** `FixtureProvider` returns hand-written replies keyed by `<workflow>/<step id>` from `samples/llm_fixtures.json`. `AnthropicProvider` and `OpenAIProvider` are optional extras chosen with `--llm`.

**Consequences.**
- CI exercises the full pipeline, including every guard.
- Fixture results show how the pipeline behaves, not how good a model is. Live numbers have to be measured with a real provider and stored separately, for example in `results/live/`.
