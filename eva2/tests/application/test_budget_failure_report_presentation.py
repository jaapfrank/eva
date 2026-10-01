"""Failure reports remain failure notices, with no fabricated success output."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.application.work_failure_notice import delivery_outcome, WorkFailureNotice
from eva.application.work_result_presentation import work_failure_description


def test_budget_description_retains_complete_report_without_marking_success():
    report = "Saved configuration.\nThe result still needs review."
    text = work_failure_description("budget_exceeded", report)
    assert text.endswith(report)
    assert "niet als afgerond resultaat bevestigd" in text
    assert "budget" in text
    assert report not in work_failure_description("internal_error", report)


@pytest.mark.parametrize("error_code", ["budget_exceeded", "incomplete_work"])
def test_failure_delivery_reuses_report_and_respects_stop(error_code):
    work = SimpleNamespace(state="failed", error_code=error_code, failure_report="Saved draft.",
        title="Example", stop_requested_at=None, result=None, updated_at=datetime(2026,9,24,tzinfo=UTC),
        spec=SimpleNamespace(work_id="work:one", sha256="a"*64))
    notice = delivery_outcome(work)
    assert isinstance(notice, WorkFailureNotice) and notice.state == "failed"
    assert notice.output.endswith("Saved draft.")
    assert "niet als afgerond resultaat bevestigd" in notice.output
    assert work.result is None
    work.stop_requested_at = work.updated_at
    assert delivery_outcome(work) is None
