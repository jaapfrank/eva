"""Offline urllib failure metadata: preserve behavior, retain only a valid status."""
import io
import threading
import urllib.error

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel


def model():
    value = object.__new__(PydanticConversationModel)
    value._endpoint = "https://example.invalid/offline-only"
    value._timeout_seconds = 1
    value._credential_lock = threading.Lock()
    value._rejected_token_sha256 = None
    value._resource_budget = None
    value._admit_request = lambda *args, **kwargs: None
    value._load_auth = lambda: ("OFFLINE_ACCESS", "OFFLINE_ACCOUNT")
    value._jwt_expiry = lambda token: None
    return value


@pytest.mark.parametrize("status", [100, 429, 503, 599, 99, 600, True, False, 503.0, "503", None])
def test_actual_http_error_only_retains_valid_integer_status(monkeypatch, status):
    value = model()
    original = urllib.error.HTTPError(value._endpoint, status, "PRIVATE_REASON",
        {"Authorization": "PRIVATE_HEADER"}, io.BytesIO(b"PRIVATE_EXISTING_DETAIL"))
    calls = []

    def fail(request, timeout):
        calls.append(request)
        raise original

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(ConversationModelError) as caught:
        value._request_using({}, lambda stream: None)
    failure = caught.value
    assert failure.__cause__ is original
    assert str(failure) == f"conversation provider returned HTTP {status}: PRIVATE_EXISTING_DETAIL"
    assert failure.model_usage is None
    # Existing exception text remains intact; the new structured field is status only.
    expected = {"model_usage": None}
    if type(status) is int and 100 <= status <= 599:
        expected["failure_http_status_code"] = status
    assert vars(failure) == expected
    assert len(calls) == 1


def test_non_http_failure_never_receives_invented_status(monkeypatch):
    value = model()
    original = urllib.error.URLError("PRIVATE_CAUSE")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(original))
    with pytest.raises(ConversationModelError) as caught:
        value._request_using({}, lambda stream: None)
    assert str(caught.value) == "conversation provider is unavailable"
    assert caught.value.__cause__ is original
    assert vars(caught.value) == {"model_usage": None}


def test_first_auth_rejection_preserves_existing_same_credential_stop(monkeypatch):
    value = model()
    original = urllib.error.HTTPError(value._endpoint, 401, "PRIVATE_REASON", {}, io.BytesIO(b"PRIVATE_DETAIL"))
    calls = []

    def fail(request, timeout):
        calls.append(request)
        raise original

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(ConversationModelError, match="^conversation credentials were rejected$") as caught:
        value._request_using({}, lambda stream: None)
    assert len(calls) == 1
    assert vars(caught.value) == {"model_usage": None}
    # No new retry, credential mutation or status invented on the separate final branch.


@pytest.mark.parametrize("status", [401, 403])
def test_second_auth_rejection_uses_existing_retry_and_transfers_real_status(monkeypatch, status):
    value = model()
    auth = iter([("OFFLINE_OLD", "OFFLINE_ACCOUNT"), ("OFFLINE_NEW", "OFFLINE_ACCOUNT")])
    value._load_auth = lambda: next(auth)
    calls = []
    errors = []

    def fail(request, timeout):
        calls.append(request)
        original = urllib.error.HTTPError(value._endpoint, status, "PRIVATE_REASON", {}, io.BytesIO(b"PRIVATE_DETAIL"))
        errors.append(original)
        raise original

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(ConversationModelError) as caught:
        value._request_using({}, lambda stream: None)
    assert len(calls) == 2
    assert caught.value.__cause__ is errors[1]
    assert str(caught.value) == f"conversation provider returned HTTP {status}: PRIVATE_DETAIL"
    assert vars(caught.value) == {"model_usage": None, "failure_http_status_code": status}
