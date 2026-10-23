import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLES = ROOT / "samples" / "workflows"
FIXTURES = ROOT / "samples" / "llm_fixtures.json"


@pytest.fixture
def fixture_mapper():
    from wma.llm_mapper import BoundedLLMMapper, FixtureProvider

    return BoundedLLMMapper(FixtureProvider(FIXTURES))


class StubProvider:
    """Returns one canned reply; records what it was sent."""

    name = "stub"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user, key):
        self.calls.append((system, user, key))
        return self.reply
