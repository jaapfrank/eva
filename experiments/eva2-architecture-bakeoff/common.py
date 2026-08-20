from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel


class ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentityPack(ExactModel):
    contract: Literal["EvaIdentityPack/v1"]
    name: str = Field(min_length=1, max_length=80)
    language: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    style: str = Field(min_length=1, max_length=240)


class MemorySeed(ExactModel):
    meeting_duration_minutes: int = Field(ge=5, le=480)
    preferred_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    supersedes_preferred_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    confirmation_style: str = Field(min_length=1, max_length=160)


class ExpectedOutcome(ExactModel):
    mail_id: str
    customer_id: str
    meeting_start: str
    meeting_end: str
    calendar_effects: int = 1
    document_effects: int = 1
    delivery_effects: int = 1


class EpisodeLimits(ExactModel):
    max_model_calls: int = Field(ge=1, le=10)
    max_connector_calls: int = Field(ge=1, le=100)
    max_visible_deliveries: int = Field(ge=0, le=10)
    max_wall_clock_ms: int = Field(ge=1_000, le=3_600_000)


class EpisodeContract(ExactModel):
    contract: Literal["EvaArchitectureEpisode/v1"]
    episode_id: str
    conversation_id: str
    principal_id: str
    identity: IdentityPack
    memory: MemorySeed
    user_text: str = Field(min_length=1, max_length=4_000)
    expected: ExpectedOutcome
    limits: EpisodeLimits


class EpisodePlan(ExactModel):
    contract: Literal["EvaArchitecturePlan/v1"] = "EvaArchitecturePlan/v1"
    mail_sender: Literal["Noor BV"] = "Noor BV"
    customer_email: Literal["planning@noorbv.example"] = "planning@noorbv.example"
    meeting_start: str
    meeting_end: str
    document_title: Literal["Afspraakbevestiging Noor BV"] = "Afspraakbevestiging Noor BV"


class EffectIntent(ExactModel):
    contract: Literal["EvaEffectIntent/v1"] = "EvaEffectIntent/v1"
    episode_id: str
    operation: Literal["calendar.create", "document.create", "delivery.send"]
    idempotency_key: str
    payload: dict[str, Any]
    payload_sha256: str


class EffectReceipt(ExactModel):
    contract: Literal["EvaEffectReceipt/v1"] = "EvaEffectReceipt/v1"
    operation: str
    idempotency_key: str
    resource_id: str
    request_sha256: str
    response_sha256: str
    readback_sha256: str
    reconciled: bool


class EpisodeOutcome(ExactModel):
    contract: Literal["EvaArchitectureOutcome/v1"] = "EvaArchitectureOutcome/v1"
    episode_id: str
    mail_id: str
    mail_sha256: str
    customer_id: str
    receipts: tuple[EffectReceipt, ...]
    reply: str


class EpisodeFailureOutcome(ExactModel):
    contract: Literal["EvaArchitectureFailureOutcome/v1"] = "EvaArchitectureFailureOutcome/v1"
    episode_id: str
    status: Literal["partial", "failed"]
    mail_id: str
    customer_id: str
    completed_receipts: tuple[EffectReceipt, ...]
    failed_operation: Literal["calendar.create", "document.create", "delivery.send"]
    failure_class: Literal["definitive_reject", "retry_exhausted", "uncertain_commit"]
    retryable: bool
    reply: str


class ModelPlanResult(ExactModel):
    contract: Literal["EvaModelPlanResult/v1"] = "EvaModelPlanResult/v1"
    plan: EpisodePlan
    requests: int
    input_tokens: int
    output_tokens: int


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_contract(path: str | Path) -> EpisodeContract:
    return EpisodeContract.model_validate_json(Path(path).read_text(encoding="utf-8"))


def deterministic_plan(contract: EpisodeContract) -> EpisodePlan:
    return EpisodePlan(
        meeting_start=contract.expected.meeting_start,
        meeting_end=contract.expected.meeting_end,
    )


def run_deterministic_model(
    contract: EpisodeContract, evidence_client: "FixtureClient | None" = None,
) -> ModelPlanResult:
    expected = deterministic_plan(contract)
    agent = Agent(
        TestModel(custom_output_args=expected.model_dump(mode="json")),
        output_type=EpisodePlan,
        instructions=(
            "Projecteer uitsluitend het sealed architectuurplan. Voer geen connectoractie uit."
        ),
    )
    result = agent.run_sync(contract.user_text)
    usage = result.usage
    projected = ModelPlanResult(
        plan=result.output,
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    if evidence_client is not None:
        evidence_client.record_model_call(
            contract.episode_id,
            sha256(contract.user_text),
            sha256(projected.plan.model_dump(mode="json")),
        )
    delay_ms = int(os.environ.get("EVA_BAKEOFF_MODEL_DELAY_MS", "0"))
    if delay_ms < 0 or delay_ms > 5_000:
        raise RuntimeError("ongeldige deterministische modelvertraging")
    if delay_ms:
        time.sleep(delay_ms / 1_000)
    return projected


def maybe_crash(point: str) -> None:
    if os.environ.get("EVA_BAKEOFF_CRASH_AT", "") == point:
        os._exit(86)


def effect_intent(
    contract: EpisodeContract,
    operation: Literal["calendar.create", "document.create", "delivery.send"],
    payload: dict[str, Any],
) -> EffectIntent:
    body = json.loads(canonical_json({"operation": operation, "data": payload}))
    return EffectIntent(
        episode_id=contract.episode_id,
        operation=operation,
        idempotency_key=f"{contract.episode_id}:{operation}",
        payload=body,
        payload_sha256=sha256(body),
    )


class FixtureResponseError(RuntimeError):
    def __init__(
        self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None,
    ):
        super().__init__(f"fixture HTTP {status}: {canonical_json(body)}")
        self.status = status
        self.body = body
        self.headers = headers or {}


class DefinitiveEffectError(RuntimeError):
    def __init__(self, operation: str, body: dict[str, Any]):
        super().__init__(f"definitief geweigerd effect {operation}: {canonical_json(body)}")
        self.operation = operation
        self.body = body


class FixtureClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = None if body is None else canonical_json(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=payload, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            raw = error.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {"error": "empty"}
            raise FixtureResponseError(
                error.code,
                parsed,
                {name.lower(): value for name, value in error.headers.items()},
            ) from error

    def read_latest_mail(self) -> dict[str, Any]:
        return self._request("GET", "/v1/mail/latest?sender=Noor%20BV")

    def read_customer(self) -> dict[str, Any]:
        return self._request(
            "GET", "/v1/erp/customer?email=planning%40noorbv.example",
        )

    def read_availability(self, day: str = "2026-08-26") -> dict[str, Any]:
        return self._request("GET", f"/v1/calendar/freebusy?day={day}")

    def reconcile(self, intent: EffectIntent) -> dict[str, Any] | None:
        encoded = urllib.parse.quote(intent.idempotency_key, safe="")
        try:
            return self._request("GET", f"/v1/effects/{encoded}")
        except FixtureResponseError as error:
            if error.status == 404:
                return None
            raise

    def execute(self, intent: EffectIntent) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/effects", intent.payload, intent.idempotency_key,
        )

    def evidence(self) -> dict[str, Any]:
        return self._request("GET", "/v1/evidence")

    def set_fault(self, operation: str, mode: str, remaining: int = 1) -> None:
        self._request("POST", "/v1/faults", {
            "operation": operation,
            "mode": mode,
            "remaining": remaining,
        })

    def record_model_call(
        self, episode_id: str, request_sha256: str, response_sha256: str,
    ) -> None:
        self._request("POST", "/v1/model-calls", {
            "episode_id": episode_id,
            "request_sha256": request_sha256,
            "response_sha256": response_sha256,
        })


def _receipt_from_readback(
    intent: EffectIntent, readback: dict[str, Any], *, reconciled: bool,
) -> EffectReceipt:
    if readback.get("operation") != intent.operation:
        raise RuntimeError("readback operation wijkt af")
    if readback.get("idempotency_key") != intent.idempotency_key:
        raise RuntimeError("readback idempotency key wijkt af")
    if readback.get("request_sha256") != intent.payload_sha256:
        raise RuntimeError("readback requesthash wijkt af")
    response = readback.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("resource_id"), str):
        raise RuntimeError("readback mist resource-id")
    return EffectReceipt(
        operation=intent.operation,
        idempotency_key=intent.idempotency_key,
        resource_id=response["resource_id"],
        request_sha256=intent.payload_sha256,
        response_sha256=sha256(response),
        readback_sha256=sha256(readback),
        reconciled=reconciled,
    )


def execute_or_reconcile(client: FixtureClient, intent: EffectIntent) -> EffectReceipt:
    for attempt in range(3):
        existing = client.reconcile(intent)
        if existing is not None:
            return _receipt_from_readback(intent, existing, reconciled=True)
        try:
            client.execute(intent)
        except FixtureResponseError as error:
            if error.status == 422:
                raise DefinitiveEffectError(intent.operation, error.body) from error
            if error.status != 429:
                raise
            body_delay = error.body.get("retry_after_ms")
            header_delay = error.headers.get("retry-after")
            if not isinstance(body_delay, int) or body_delay < 1 or body_delay > 5_000:
                raise RuntimeError("rate-limit mist begrensde retry-after") from error
            if header_delay is None:
                raise RuntimeError("rate-limit mist Retry-After-header") from error
            try:
                header_delay_ms = round(float(header_delay) * 1_000)
            except ValueError as parse_error:
                raise RuntimeError("rate-limit heeft ongeldige Retry-After-header") from parse_error
            if header_delay_ms != body_delay:
                raise RuntimeError("rate-limit header en body spreken elkaar tegen") from error
            if attempt == 2:
                raise RuntimeError("rate-limitpogingen zijn begrensd uitgeput") from error
            time.sleep(body_delay / 1_000)
            continue
        maybe_crash(f"{intent.operation}:after_remote_accept")
        readback = client.reconcile(intent)
        if readback is None:
            raise RuntimeError("extern effect is na acceptatie niet terugleesbaar")
        return _receipt_from_readback(intent, readback, reconciled=False)
    raise RuntimeError("onbereikbare effectbrokerstatus")


def render_reply(
    contract: EpisodeContract,
    customer: dict[str, Any],
    receipts: tuple[EffectReceipt, ...],
) -> str:
    resources = {receipt.operation: receipt.resource_id for receipt in receipts}
    required = {"calendar.create", "document.create", "delivery.send"}
    if set(resources) != required:
        raise RuntimeError("reply mag niet worden gemaakt zonder alle effectreceipts")
    return (
        f"Ik heb {customer['name']} ingepland op woensdag om "
        f"{contract.memory.preferred_time} voor {contract.memory.meeting_duration_minutes} "
        "minuten. De afspraak staat in de agenda, de bevestiging is gemaakt en eenmaal "
        "verstuurd."
    )


def render_definitive_failure_reply(
    contract: EpisodeContract,
    failed_operation: Literal["calendar.create", "document.create", "delivery.send"],
    completed_receipts: tuple[EffectReceipt, ...],
) -> str:
    completed = {receipt.operation for receipt in completed_receipts}
    if failed_operation == "calendar.create" and not completed:
        return (
            "Ik heb de mail en de klant gevonden, maar de afspraak kon niet worden "
            "ingepland. Daarom heb ik geen bevestiging gemaakt en niets verstuurd."
        )
    if failed_operation == "document.create" and completed == {"calendar.create"}:
        return (
            f"De afspraak staat woensdag om {contract.memory.preferred_time} in de agenda. "
            "De bevestiging kon niet worden gemaakt, dus ik heb niets verstuurd."
        )
    if failed_operation == "delivery.send" and completed == {
        "calendar.create", "document.create",
    }:
        return (
            f"De afspraak staat woensdag om {contract.memory.preferred_time} in de agenda en "
            "de bevestiging is gemaakt, maar het versturen is geweigerd. Ik meld het daarom "
            "niet als verzonden."
        )
    raise RuntimeError("deelresultaat past niet bij de definitieve foutgrens")
