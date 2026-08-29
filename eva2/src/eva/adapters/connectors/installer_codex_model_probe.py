"""Bounded Codex model-generation probe for the local Eva2 installer."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_provider_verification import (
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
)


_EXPECTED_TEXT = "EVA2_MODEL_READY"
_LEASE_KEYS = frozenset(
    {"access_token", "account_id", "contract", "source_sha256"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MAX_RESPONSE_BYTES = 256 * 1024


def _model_error(
    reason_code: Literal[
        "unauthorized",
        "unreachable",
        "timeout",
        "invalid_response",
        "provider_error",
    ],
    *,
    request_attempted: bool,
) -> InstallerProviderProbeError:
    return InstallerProviderProbeError(
        reason_code,
        provider_operation="bounded_generation",
        usage_measurement="unmeasured",
        billable_request_attempted=request_attempted,
    )


class CodexInstallerModelProbe:
    """Make one tool-free generation using an access-only Codex lease.

    This is intentionally not described as a read-only identity lookup: a
    successful probe consumes one bounded model generation.  It never accepts
    a complete Codex login cache or refresh authority.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-5.4-mini",
        endpoint: str = "https://chatgpt.com/backend-api/codex/responses",
        allow_insecure_loopback_test: bool = False,
    ) -> None:
        if _MODEL_RE.fullmatch(model) is None:
            raise ValueError("Codex installer verification model is invalid")
        parsed = urlsplit(endpoint)
        if (
            parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/backend-api/codex/responses"
        ):
            raise ValueError("Codex installer verification endpoint is invalid")
        if parsed.scheme == "https":
            if parsed.hostname.casefold() != "chatgpt.com" or parsed.port not in {
                None,
                443,
            }:
                raise ValueError(
                    "Codex installer verification endpoint is not the expected service"
                )
        elif parsed.scheme == "http" and allow_insecure_loopback_test:
            try:
                if not ipaddress.ip_address(parsed.hostname).is_loopback:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    "Codex installer test endpoint must be a literal loopback address"
                ) from exc
        else:
            raise ValueError("Codex installer verification endpoint must use HTTPS")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._path = parsed.path
        self._model = model

    @staticmethod
    def _lease(secret: memoryview) -> tuple[str, str]:
        try:
            payload = json.loads(bytes(secret).decode("ascii", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _model_error("unauthorized", request_attempted=False) from None
        if (
            not isinstance(payload, dict)
            or set(payload) != _LEASE_KEYS
            or payload.get("contract") != "EvaModelAccessLease/v1"
            or _SHA256_RE.fullmatch(str(payload.get("source_sha256", ""))) is None
        ):
            raise _model_error("unauthorized", request_attempted=False)
        access_token = payload.get("access_token")
        account_id = payload.get("account_id")
        if (
            not isinstance(access_token, str)
            or not 16 <= len(access_token) <= 8_192
            or not access_token.isascii()
            or any(character.isspace() or ord(character) == 0x7F for character in access_token)
            or not isinstance(account_id, str)
            or not 3 <= len(account_id) <= 256
            or not account_id.isascii()
            or any(character.isspace() or ord(character) == 0x7F for character in account_id)
        ):
            raise _model_error("unauthorized", request_attempted=False)
        return access_token, account_id

    def read_only_probe(
        self, *, secret: memoryview, timeout_seconds: float
    ) -> InstallerProviderProbeObservation:
        access_token, account_id = self._lease(secret)
        body = canonical_json_bytes(
            {
                "include": ["reasoning.encrypted_content"],
                "input": [
                    {
                        "content": "Verify this model connection.",
                        "role": "user",
                        "type": "message",
                    }
                ],
                "instructions": (
                    f"Return exactly {_EXPECTED_TEXT} and nothing else. "
                    "Do not call tools."
                ),
                "model": self._model,
                "parallel_tool_calls": False,
                "reasoning": {"effort": "low", "summary": "auto"},
                "store": False,
                "stream": True,
                "tool_choice": "none",
                "tools": [],
            }
        )
        connection_type = (
            http.client.HTTPSConnection
            if self._scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            self._host,
            port=self._port,
            timeout=timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                self._path,
                body=body,
                headers={
                    "Accept": "text/event-stream",
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/json",
                    "OpenAI-Account-ID": account_id,
                    "OpenAI-Beta": "responses=experimental",
                    "Origin": "https://chatgpt.com",
                    "User-Agent": "codex_cli_rs/0.0.0 (Eva2 Installer)",
                    "originator": "codex_cli_rs",
                    "session_id": str(uuid4()),
                },
            )
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader("Content-Type", "")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, socket.timeout):
            raise _model_error("timeout", request_attempted=True) from None
        except (OSError, http.client.HTTPException):
            raise _model_error("unreachable", request_attempted=True) from None
        finally:
            connection.close()
        if status in {401, 403}:
            raise _model_error("unauthorized", request_attempted=True)
        if status >= 500:
            raise _model_error("unreachable", request_attempted=True)
        if status != 200:
            raise _model_error("provider_error", request_attempted=True)
        if len(raw) > _MAX_RESPONSE_BYTES or not content_type.casefold().startswith(
            "text/event-stream"
        ):
            raise _model_error("invalid_response", request_attempted=True)
        try:
            text, input_tokens, output_tokens = self._completed_response(raw)
        except InstallerProviderProbeError as exc:
            raise _model_error(exc.reason_code, request_attempted=True) from None
        if text.strip() != _EXPECTED_TEXT:
            raise _model_error("invalid_response", request_attempted=True)
        return InstallerProviderProbeObservation(
            check_id="model.response.generate",
            outcome="ready",
            reason_code="verified",
            provider_operation="bounded_generation",
            usage_measurement="measured",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            billable_request_attempted=True,
        )

    @staticmethod
    def _completed_response(raw: bytes) -> tuple[str, int, int]:
        try:
            lines = raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError:
            raise InstallerProviderProbeError("invalid_response") from None
        deltas: list[str] = []
        completed = False
        input_tokens: int | None = None
        output_tokens: int | None = None
        for line in lines:
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                raise InstallerProviderProbeError("invalid_response") from None
            if not isinstance(event, dict):
                raise InstallerProviderProbeError("invalid_response")
            kind = event.get("type")
            if kind == "response.output_text.delta":
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise InstallerProviderProbeError("invalid_response")
                deltas.append(delta)
            elif kind == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    raise InstallerProviderProbeError("invalid_response")
            elif kind == "response.completed":
                if completed:
                    raise InstallerProviderProbeError("invalid_response")
                response = event.get("response")
                usage = response.get("usage") if isinstance(response, dict) else None
                if (
                    not isinstance(response, dict)
                    or response.get("status") != "completed"
                    or response.get("error") is not None
                    or not isinstance(usage, dict)
                ):
                    raise InstallerProviderProbeError("invalid_response")
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                total_tokens = usage.get("total_tokens")
                if (
                    not isinstance(input_tokens, int)
                    or isinstance(input_tokens, bool)
                    or input_tokens <= 0
                    or not isinstance(output_tokens, int)
                    or isinstance(output_tokens, bool)
                    or output_tokens <= 0
                    or not isinstance(total_tokens, int)
                    or isinstance(total_tokens, bool)
                    or total_tokens != input_tokens + output_tokens
                ):
                    raise InstallerProviderProbeError("invalid_response")
                completed = True
            elif kind in {"error", "response.failed", "response.incomplete"}:
                raise InstallerProviderProbeError("provider_error")
        if not completed or input_tokens is None or output_tokens is None:
            raise InstallerProviderProbeError("invalid_response")
        return "".join(deltas), input_tokens, output_tokens


__all__ = ["CodexInstallerModelProbe"]
