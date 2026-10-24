"""Safe HTTP failure evidence through the real SDK; no external network."""

import asyncio
from copy import deepcopy
import hashlib
import json
from datetime import datetime, timezone

import httpx2
from pydantic_ai import Agent
import pytest

from eva.adapters.model.openai_codex_provider import CodexProviderError, OpenAICodexProvider
from eva.adapters.model.pydantic_conversation import _log_model_failure
from tests.model.test_openai_codex_provider_shared import _settings


_AUTH_SECRET = "AUTH_SECRET_DO_NOT_LOG"
_ACCOUNT_SECRET = "ACCOUNT_SECRET_DO_NOT_LOG"
_PROMPT_SECRET = "PROMPT_SECRET_DO_NOT_LOG"
_MESSAGE_SECRET = "MESSAGE_SECRET_DO_NOT_LOG"
_BODY_SECRET = "BODY_SECRET_DO_NOT_LOG"


def _sdk_http_failure(body, headers):
    requests = []

    async def respond(request):
        requests.append(request)
        if isinstance(body, bytes):
            return httpx2.Response(503, content=body, headers=headers, request=request)
        return httpx2.Response(503, json=body, headers=headers, request=request)

    async def events(_context, stream):
        async for _event in stream:
            raise AssertionError("an HTTP failure cannot produce a model event")

    async def run():
        provider = OpenAICodexProvider(_settings(),
            credential_loader=lambda: (_AUTH_SECRET, _ACCOUNT_SECRET),
            network_transport=httpx2.MockTransport(respond))
        async with provider as session:
            with pytest.raises(CodexProviderError) as raised:
                await Agent(session.model, retries=0).run(_PROMPT_SECRET,
                    instructions="Keep the request private.", event_stream_handler=events)
            error = raised.value
            observation = deepcopy(session.observation)
            assert error.category == "unavailable"
            assert session.model_requests == 1
            assert observation["response_status"] == 503
            assert observation["sse_events"] == observation["completed_events"] == 0
            with pytest.raises(CodexProviderError):
                session.consume_raw_usage()
        assert session.local_stream_closed is True
        return error, observation

    error, observation = asyncio.run(run())
    assert len(requests) == 1  # A diagnostic cannot authorize another request.
    return error, observation


def _logged_failure(error, observation, caplog):
    _log_model_failure(error, "private-local-request", transport=observation)
    records = [record.getMessage() for record in caplog.records
        if record.getMessage().startswith("conversation_model_failure ")]
    assert records
    return json.loads(records[-1].split(" ", 1)[1])


def _assert_no_secrets(error, observation, caplog):
    retained = str(error) + json.dumps(error.diagnostic) + json.dumps(observation) + caplog.text
    for secret in (_AUTH_SECRET, _ACCOUNT_SECRET, _PROMPT_SECRET, _MESSAGE_SECRET, _BODY_SECRET):
        assert secret not in retained


def test_sdk_http_failure_preserves_observed_quota_labels_and_safe_headers_in_existing_logger(caplog):
    body = {"error": {"code": "subscription_sharing_usage_limit_exceeded", "type": "rate_limit_error",
        "message": _MESSAGE_SECRET, "private": {"body": _BODY_SECRET}}}
    error, observation = _sdk_http_failure(body, {"x-request-id": "req_fixture_123", "retry-after": "17",
        "authorization": "Bearer " + _AUTH_SECRET, "set-cookie": _BODY_SECRET})
    expected = {"event_kind": "http_error", "http_status": 503,
        "error_code": "subscription_sharing_usage_limit_exceeded", "error_code_sha256": None,
        "error_type": "rate_limit_error", "error_type_sha256": None,
        "provider_request_id": "req_fixture_123", "provider_request_id_sha256": None,
        "retry_after_seconds": 17, "retry_after_at": None, "retry_after_sha256": None}
    assert error.diagnostic == expected
    logged = _logged_failure(error, observation, caplog)
    assert logged["provider_category"] == "unavailable"  # HTTP503 remains the actual class.
    assert logged["provider_failure"] == expected
    _assert_no_secrets(error, observation, caplog)


def test_untrusted_sdk_labels_and_headers_are_fingerprinted_without_leaking_their_values(caplog):
    code, kind = _BODY_SECRET + "-code", _MESSAGE_SECRET + "-type"
    request_id, retry = "unsafe request ID " + _PROMPT_SECRET, "unsafe delay " + _AUTH_SECRET
    error, observation = _sdk_http_failure({"error": {"code": code, "type": kind,
        "message": _MESSAGE_SECRET}}, {"x-request-id": request_id, "retry-after": retry})
    diagnostic = error.diagnostic
    assert diagnostic is not None
    assert diagnostic["error_code"] == diagnostic["error_type"] == "unrecognized"
    assert diagnostic["error_code_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert diagnostic["error_type_sha256"] == hashlib.sha256(kind.encode()).hexdigest()
    assert diagnostic["provider_request_id"] is None
    assert diagnostic["provider_request_id_sha256"] == hashlib.sha256(request_id.encode()).hexdigest()
    assert diagnostic["retry_after_seconds"] is diagnostic["retry_after_at"] is None
    assert diagnostic["retry_after_sha256"] == hashlib.sha256(retry.encode()).hexdigest()
    assert _logged_failure(error, observation, caplog)["provider_failure"] == diagnostic
    _assert_no_secrets(error, observation, caplog)


def test_http_date_is_retained_as_evidence_without_waiting_or_dispatching_another_request(caplog):
    request_id = "c2d49484-ae32-4a37-8734-f11138cb0d50"
    error, observation = _sdk_http_failure({"error": {"code": "server_error", "type": "server_error",
        "message": _MESSAGE_SECRET}}, {"x-request-id": request_id,
        "retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    diagnostic = error.diagnostic
    assert diagnostic is not None
    assert diagnostic["error_code"] == diagnostic["error_type"] == "server_error"
    assert diagnostic["provider_request_id"] == request_id
    assert diagnostic["provider_request_id_sha256"] is None
    assert diagnostic["retry_after_seconds"] is None
    assert datetime.fromisoformat(diagnostic["retry_after_at"].replace("Z", "+00:00")) == datetime(
        2015, 10, 21, 7, 28, tzinfo=timezone.utc)
    assert diagnostic["retry_after_sha256"] is None
    assert _logged_failure(error, observation, caplog)["provider_failure"] == diagnostic
    _assert_no_secrets(error, observation, caplog)


def test_non_json_failure_has_no_invented_provider_reason_or_usage(caplog):
    error, observation = _sdk_http_failure(("not JSON: " + _BODY_SECRET).encode(), {})
    diagnostic = error.diagnostic
    assert diagnostic is not None
    assert diagnostic["event_kind"] == "http_error" and diagnostic["http_status"] == 503
    assert all(value is None for key, value in diagnostic.items() if key not in {"event_kind", "http_status"})
    assert _logged_failure(error, observation, caplog)["provider_failure"] == diagnostic
    _assert_no_secrets(error, observation, caplog)
