"""SDK-native HTTP header evidence at Eva's streaming adapter; no network."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_graph.graph_builder import _unwrap_exception_groups

from eva.adapters.model.openai_codex_provider import CodexProviderError, _CodexStreamingModel
from eva.adapters.model.pydantic_conversation import _log_model_failure


_PRIVATE = "PRIVATE_SDK_HEADER_DO_NOT_LOG"


class _NativeSdkFailure(Model):
    """A genuine supported SDK exception crossing the existing stream adapter."""

    def __init__(self, error, grouped):
        super().__init__()
        self.error = error
        self.grouped = grouped
        self.requests = 0
        self.closed = False

    @property
    def model_name(self):
        return "offline-sdk-error"

    @property
    def system(self):
        return "offline"

    async def request(self, *_args, **_kwargs):
        raise AssertionError("the streaming route must be used")

    @asynccontextmanager
    async def request_stream(self, *_args, **_kwargs):
        self.requests += 1
        try:
            if self.grouped:
                # Use the installed graph SDK's actual single-error handoff.
                with _unwrap_exception_groups():
                    raise ExceptionGroup("private wrapper " + _PRIVATE, [self.error])
            raise self.error
            yield  # pragma: no cover
        finally:
            self.closed = True


def _run_native_headers(headers, *, grouped, caplog):
    sdk_error = ModelHTTPError(503, "offline-sdk-error", {
        "error": {"code": "server_error", "type": "server_error", "message": _PRIVATE}},
        headers=headers)
    # This supported SDK DTO has no response object or lower-level HTTP cause.
    assert not hasattr(sdk_error, "response")
    assert sdk_error.__cause__ is None
    delegate = _NativeSdkFailure(sdk_error, grouped)

    async def run():
        with pytest.raises(CodexProviderError) as raised:
            async with _CodexStreamingModel(delegate).request_stream(
                    [], None, ModelRequestParameters()):
                raise AssertionError("HTTP failure cannot yield a model response")
        return raised.value

    error = asyncio.run(run())
    assert delegate.requests == 1 and delegate.closed
    assert error.category == "unavailable"
    diagnostic = error.diagnostic
    assert diagnostic is not None and diagnostic["http_status"] == 503
    assert diagnostic["error_code"] == diagnostic["error_type"] == "server_error"
    assert not any("usage" in key for key in diagnostic)
    _log_model_failure(error, "offline-private-local-request")
    records = [r.getMessage() for r in caplog.records
        if r.getMessage().startswith("conversation_model_failure ")]
    assert records
    logged = json.loads(records[-1].split(" ", 1)[1])
    assert logged["provider_category"] == "unavailable"
    assert logged["provider_failure"] == diagnostic
    assert _PRIVATE not in str(error) + json.dumps(diagnostic) + caplog.text
    return diagnostic


@pytest.mark.parametrize("grouped", [False, True], ids=["direct-sdk", "sdk-group-unwrapped"])
def test_available_native_sdk_headers_survive_stream_adapter_and_existing_logger(grouped, caplog):
    diagnostic = _run_native_headers({"X-Request-ID": "req_native_sdk_17", "Retry-After": "17",
        "Authorization": "Bearer " + _PRIVATE, "Set-Cookie": _PRIVATE},
        grouped=grouped, caplog=caplog)
    assert diagnostic["provider_request_id"] == "req_native_sdk_17"
    assert diagnostic["provider_request_id_sha256"] is None
    assert diagnostic["retry_after_seconds"] == 17
    assert diagnostic["retry_after_at"] is diagnostic["retry_after_sha256"] is None


def test_suspicious_native_sdk_headers_are_fingerprinted_after_real_group_handoff(caplog):
    request_id, delay = "unsafe request " + _PRIVATE, "unsafe delay " + _PRIVATE
    diagnostic = _run_native_headers({"x-request-id": request_id, "retry-after": delay,
        "authorization": "Bearer " + _PRIVATE}, grouped=True, caplog=caplog)
    assert diagnostic["provider_request_id"] is None
    assert diagnostic["provider_request_id_sha256"] == hashlib.sha256(request_id.encode()).hexdigest()
    assert diagnostic["retry_after_seconds"] is diagnostic["retry_after_at"] is None
    assert diagnostic["retry_after_sha256"] == hashlib.sha256(delay.encode()).hexdigest()


def test_malformed_native_header_values_do_not_replace_category_or_invent_metadata(caplog):
    diagnostic = _run_native_headers({"x-request-id": [_PRIVATE], "retry-after": {_PRIVATE: 1}},
        grouped=True, caplog=caplog)
    assert diagnostic["provider_request_id"] is diagnostic["provider_request_id_sha256"] is None
    assert diagnostic["retry_after_seconds"] is diagnostic["retry_after_at"] is diagnostic["retry_after_sha256"] is None
