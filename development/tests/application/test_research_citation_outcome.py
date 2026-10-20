"""A completed-response citation failure must not become a task-budget claim."""
import asyncio
from eva.adapters.model.contracts import ResearchCitationError
from tests.application.test_delegated_digital_runner import fixture, controlled_persistence


def test_runner_preserves_citation_failure_without_a_fake_budget_or_result(monkeypatch):
    source, spec, store, tools, journal, runner, identity = fixture()
    class InvalidSourceModel:
        def generate(self, *args, **kwargs):
            raise ResearchCitationError("research citation URL is invalid")
    runner.model = InvalidSourceModel()
    outcome = asyncio.run(runner.run(spec, call_id="invalid-citation"))
    assert outcome.status == "incomplete_work" and outcome.result is None
    assert "ongeldige bronverwijzing" in outcome.failure_report
    assert "niet automatisch herhaald" in outcome.failure_report
    assert not tools.calls
