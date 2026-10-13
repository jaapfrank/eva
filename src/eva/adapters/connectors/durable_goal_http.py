"""Bounded HTTP evidence, work and delivery adapter for durable goals."""

from __future__ import annotations

import asyncio
import json
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from eva.application.durable_goals import (
    DurableGoalError,
    DurableGoalSpec,
    GoalArtifact,
    GoalEvidence,
    GoalWakeCondition,
)


class HttpDurableGoalConnector:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 5.0,
    ) -> None:
        parsed = urllib_parse.urlparse(base_url)
        if parsed.scheme not in ({"https", "http"} if allow_insecure_http else {"https"}):
            raise ValueError("durable goal provider URL is not admitted")
        if not parsed.netloc or parsed.username or parsed.password or parsed.query:
            raise ValueError("durable goal provider URL is invalid")
        if not provider.strip() or len(provider) > 80:
            raise ValueError("durable goal provider is invalid")
        if not 0.2 <= timeout_seconds <= 30:
            raise ValueError("durable goal provider timeout is outside 0.2..30")
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def read(self, condition: GoalWakeCondition) -> GoalEvidence:
        if condition.provider != self._provider:
            raise DurableGoalError("durable goal evidence provider differs")
        query = urllib_parse.urlencode({"resource": condition.resource})
        raw = await asyncio.to_thread(self._json, "GET", f"/v1/evidence?{query}", None)
        evidence = GoalEvidence.from_payload(raw)
        if evidence.provider != self._provider or evidence.resource != condition.resource:
            raise DurableGoalError("durable goal evidence response crossed scope")
        return evidence

    async def readback(
        self,
        *,
        goal: DurableGoalSpec | None = None,
        recipient: str | None = None,
        idempotency_key: str,
    ) -> GoalArtifact | tuple[str, str | None] | None:
        if goal is not None and recipient is not None:
            raise ValueError("durable goal readback scope is ambiguous")
        encoded = urllib_parse.quote(idempotency_key, safe="")
        if goal is not None:
            raw = await asyncio.to_thread(
                self._json_or_none, "GET", f"/v1/effects/{encoded}", None
            )
            return None if raw is None else GoalArtifact.from_payload(raw)
        if recipient is None:
            raise ValueError("durable goal delivery recipient is required")
        query = urllib_parse.urlencode({"recipient": recipient})
        raw = await asyncio.to_thread(
            self._json_or_none,
            "GET",
            f"/v1/deliveries/{encoded}?{query}",
            None,
        )
        return None if raw is None else self._delivery_result(raw, recipient)

    async def execute(
        self,
        *,
        goal: DurableGoalSpec,
        instruction: str,
        evidence: GoalEvidence,
        idempotency_key: str,
        previous_artifact: GoalArtifact | None = None,
    ) -> GoalArtifact:
        raw = await asyncio.to_thread(
            self._json,
            "POST",
            "/v1/effects",
            {
                **({"previous_artifact": previous_artifact.to_payload()}
                   if previous_artifact is not None else {}),
                "evidence": evidence.to_payload(),
                "goal_id": goal.goal_id,
                "goal_sha256": goal.sha256,
                "idempotency_key": idempotency_key,
                "instruction": instruction,
            },
        )
        return GoalArtifact.from_payload(raw)

    async def deliver(
        self,
        *,
        recipient: str,
        content: str,
        content_sha256: str,
        idempotency_key: str,
    ) -> tuple[str, str | None]:
        raw = await asyncio.to_thread(
            self._json,
            "POST",
            "/v1/deliveries",
            {
                "content": content,
                "content_sha256": content_sha256,
                "idempotency_key": idempotency_key,
                "recipient": recipient,
            },
        )
        return self._delivery_result(raw, recipient)

    def _json_or_none(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> object | None:
        try:
            return self._json(method, path, payload)
        except urllib_error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def _json(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> object:
        if not path.startswith("/v1/"):
            raise DurableGoalError("durable goal provider path is outside v1")
        body = None
        headers = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            if len(body) > 131_072:
                raise DurableGoalError("durable goal provider request is too large")
            headers["content-type"] = "application/json"
        request = urllib_request.Request(
            self._base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read(131_073)
        except urllib_error.HTTPError:
            raise
        except (OSError, TimeoutError, urllib_error.URLError) as error:
            raise DurableGoalError("durable goal provider is unavailable") from error
        if len(raw) > 131_072:
            raise DurableGoalError("durable goal provider response is too large")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DurableGoalError("durable goal provider response is invalid") from error

    @staticmethod
    def _delivery_result(raw: object, recipient: str) -> tuple[str, str | None]:
        if not isinstance(raw, dict) or raw.get("contract") != (
            "EvaGoalDeliveryReceipt/v1"
        ):
            raise DurableGoalError("durable goal delivery receipt is malformed")
        status = raw.get("status")
        reason = raw.get("reason")
        if raw.get("recipient") != recipient or status not in {"accepted", "rejected"}:
            raise DurableGoalError("durable goal delivery receipt differs")
        if (status == "accepted" and reason is not None) or (
            status == "rejected" and not isinstance(reason, str)
        ):
            raise DurableGoalError("durable goal delivery receipt reason differs")
        return status, reason


__all__ = ["HttpDurableGoalConnector"]
