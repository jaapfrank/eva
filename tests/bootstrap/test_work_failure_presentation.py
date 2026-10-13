"""Private and household task views share bounded failure explanations."""
from types import SimpleNamespace as NS

import pytest

from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.bootstrap.household_work import HouseholdWorkService


@pytest.mark.parametrize("code, expected", [
    ("provider_unavailable", "Een benodigde dienst was niet bereikbaar."),
    ("internal_error", "De uitvoering is technisch vastgelopen."),
    ("timed_out", "De uitvoering heeft de tijdslimiet bereikt."),
    (None, None),
    ("unknown_error", None),
])
def test_both_views_explain_same_failure_without_private_details(code, expected):
    work = NS(spec=NS(work_id="work:test", generation=1, task="Public question",
                     research=NS(question="Public question"), created_at="created", deadline="deadline"),
              title="Task", state="ambiguous", stop_requested_at=None,
              result=None, usage=None, error_code=code, error_fingerprint="private-fingerprint", call_id="call:test")
    private = object.__new__(DelegatedWorkOwnerService)
    private._private_delivery_view = lambda *args: {"state": "unknown"}
    household = object.__new__(HouseholdWorkService)
    checks = []
    household.current = lambda: checks.append("authorized")
    private_view = private._view(work)
    household_view = household.view(work)
    assert checks == ["authorized"]
    assert household_view["error_code"] == private_view["error_code"] == code
    assert household_view["failure_description"] == private_view["failure_description"]
    description = household_view["failure_description"]
    if expected is None:
        assert description is None
    else:
        assert description.startswith(expected)
        assert "voordat je hervat" in description
    assert "private-fingerprint" not in str(household_view)
    assert household_view["result"] is None


def test_household_authority_failure_prevents_failure_view():
    household = object.__new__(HouseholdWorkService)
    def revoked():
        raise ValueError("revoked")
    household.current = revoked
    with pytest.raises(ValueError, match="revoked"):
        household.view(NS())
