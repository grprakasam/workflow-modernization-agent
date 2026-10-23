# 0003: Differential testing for behavioral fidelity

**Status:** accepted

**Context.** "The flow looks right" is not evidence. Reviewers need to know whether the converted flow does what the old one did.

**Decision.**
- Ship behavioral test cases with each workflow: trigger payloads plus scripted approval outcomes.
- Run every case through a simulator of the legacy platform and a simulator of the generated definition.
- Compare normalized side-effect events, final variables and run status.
- Track **silent failures**: cases that fail in a workflow where nothing was flagged. CI fails if there are any.

**Consequences.**
- The harness itself is checked by mutation tests: changing a condition operator or deleting an action must be detected.
- The simulators cover observable effects, not connector edge cases such as throttling or paging. Those still need testing in a tenant.
