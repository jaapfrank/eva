from __future__ import annotations

import argparse
from importlib.metadata import version
from typing import Any, Literal
from urllib.parse import urlparse

from dbos import DBOS, DBOSConfig, SetWorkflowID, WorkflowSerializationFormat

from common import (
    DefinitiveEffectError,
    EffectIntent,
    EffectReceipt,
    EpisodeContract,
    EpisodeFailureOutcome,
    EpisodeOutcome,
    EpisodePlan,
    FixtureClient,
    ModelPlanResult,
    canonical_json,
    effect_intent,
    execute_or_reconcile,
    load_contract,
    render_definitive_failure_reply,
    render_reply,
    run_deterministic_model,
    sha256,
)
from dbos_state import DBOSState, load_state


APPLICATION_NAME = "eva2-architecture-bakeoff-dbos"
APPLICATION_VERSION = "episode-contract-v1-dbos-2.30.0"
EpisodeResult = EpisodeOutcome | EpisodeFailureOutcome


def _contract(value: dict[str, Any]) -> EpisodeContract:
    return EpisodeContract.model_validate(value)


def _fixture_client(base_url: str) -> FixtureClient:
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("fixture moet een ongeauthenticeerde localhost-http-URL zijn")
    return FixtureClient(base_url)


@DBOS.step(name="eva2.model.plan")
def model_plan_step(
    contract_body: dict[str, Any],
    fixture_url: str,
) -> dict[str, Any]:
    result = run_deterministic_model(
        _contract(contract_body),
        _fixture_client(fixture_url),
    )
    if result.requests != 1:
        raise RuntimeError("deterministische modellane deed niet exact één request")
    return result.model_dump(mode="json")


@DBOS.step(name="eva2.mail.read")
def read_mail_step(fixture_url: str) -> dict[str, Any]:
    return _fixture_client(fixture_url).read_latest_mail()


@DBOS.step(name="eva2.erp.read")
def read_customer_step(fixture_url: str) -> dict[str, Any]:
    return _fixture_client(fixture_url).read_customer()


@DBOS.step(name="eva2.calendar.freebusy")
def read_availability_step(fixture_url: str) -> dict[str, Any]:
    return _fixture_client(fixture_url).read_availability()


@DBOS.step(
    name="eva2.effect.execute-or-reconcile",
)
def effect_step(
    fixture_url: str,
    intent_body: dict[str, Any],
) -> dict[str, Any]:
    # A DBOS step is at-least-once: every attempt first reconciles by the stable
    # idempotency key, including an attempt after a crash following remote accept.
    intent = EffectIntent.model_validate(intent_body)
    try:
        receipt = execute_or_reconcile(_fixture_client(fixture_url), intent)
    except DefinitiveEffectError as error:
        if error.operation != intent.operation:
            raise RuntimeError("definitieve fout hoort bij andere operatie") from error
        return {
            "contract": "EvaDBOSEffectStepResult/v1",
            "kind": "definitive_reject",
            "operation": intent.operation,
        }
    return {
        "contract": "EvaDBOSEffectStepResult/v1",
        "kind": "receipt",
        "receipt": receipt.model_dump(mode="json"),
    }


def _effect_result(
    body: dict[str, Any],
    expected_operation: str,
) -> EffectReceipt | None:
    if body.get("contract") != "EvaDBOSEffectStepResult/v1":
        raise RuntimeError("effect-step gaf onbekend contract terug")
    if body.get("kind") == "definitive_reject":
        if set(body) != {"contract", "kind", "operation"}:
            raise RuntimeError("definitieve effectuitkomst heeft extra velden")
        if body.get("operation") != expected_operation:
            raise RuntimeError("definitieve effectuitkomst hoort bij andere operatie")
        return None
    if body.get("kind") != "receipt" or set(body) != {"contract", "kind", "receipt"}:
        raise RuntimeError("effect-step gaf ongeldige receiptuitkomst terug")
    receipt = EffectReceipt.model_validate(body["receipt"])
    if receipt.operation != expected_operation:
        raise RuntimeError("effectreceipt hoort bij andere operatie")
    return receipt


def _definitive_failure(
    contract: EpisodeContract,
    mail: dict[str, Any],
    customer: dict[str, Any],
    failed_operation: Literal["calendar.create", "document.create", "delivery.send"],
    completed_receipts: tuple[EffectReceipt, ...],
) -> EpisodeFailureOutcome:
    return EpisodeFailureOutcome(
        episode_id=contract.episode_id,
        status="partial" if completed_receipts else "failed",
        mail_id=mail["mail_id"],
        customer_id=customer["customer_id"],
        completed_receipts=completed_receipts,
        failed_operation=failed_operation,
        failure_class="definitive_reject",
        retryable=False,
        reply=render_definitive_failure_reply(
            contract,
            failed_operation,
            completed_receipts,
        ),
    )


@DBOS.workflow(
    name="eva2.episode.noor.v1",
    max_recovery_attempts=20,
    serialization_type=WorkflowSerializationFormat.PORTABLE,
)
def episode_workflow(
    contract_body: dict[str, Any],
    fixture_url: str,
) -> dict[str, Any]:
    contract = _contract(contract_body)
    model_result = ModelPlanResult.model_validate(
        model_plan_step(contract_body, fixture_url)
    )
    plan: EpisodePlan = model_result.plan

    mail = read_mail_step(fixture_url)
    customer = read_customer_step(fixture_url)
    freebusy = read_availability_step(fixture_url)
    if mail["mail_id"] != contract.expected.mail_id:
        raise RuntimeError("verkeerde mail gelezen")
    if customer["customer_id"] != contract.expected.customer_id:
        raise RuntimeError("verkeerde ERP-klant gelezen")
    if not any(slot["start"] == plan.meeting_start for slot in freebusy["available"]):
        raise RuntimeError("gekozen tijd is niet extern beschikbaar")

    calendar_intent = effect_intent(
        contract,
        "calendar.create",
        {
            "customer_id": customer["customer_id"],
            "start": plan.meeting_start,
            "end": plan.meeting_end,
        },
    )
    calendar = _effect_result(
        effect_step(fixture_url, calendar_intent.model_dump(mode="json")),
        "calendar.create",
    )
    if calendar is None:
        return _definitive_failure(
            contract,
            mail,
            customer,
            "calendar.create",
            (),
        ).model_dump(mode="json")

    document_intent = effect_intent(
        contract,
        "document.create",
        {
            "calendar_resource_id": calendar.resource_id,
            "customer_id": customer["customer_id"],
            "title": plan.document_title,
            "text": (
                f"Bevestiging voor {customer['name']}: "
                f"{plan.meeting_start} tot {plan.meeting_end}."
            ),
        },
    )
    document = _effect_result(
        effect_step(fixture_url, document_intent.model_dump(mode="json")),
        "document.create",
    )
    if document is None:
        return _definitive_failure(
            contract,
            mail,
            customer,
            "document.create",
            (calendar,),
        ).model_dump(mode="json")

    delivery_intent = effect_intent(
        contract,
        "delivery.send",
        {
            "calendar_resource_id": calendar.resource_id,
            "document_resource_id": document.resource_id,
            "recipient": customer["email"],
            "text": (
                f"De afspraak staat op {plan.meeting_start}; "
                f"bevestiging {document.resource_id}."
            ),
        },
    )
    delivery = _effect_result(
        effect_step(fixture_url, delivery_intent.model_dump(mode="json")),
        "delivery.send",
    )
    if delivery is None:
        return _definitive_failure(
            contract,
            mail,
            customer,
            "delivery.send",
            (calendar, document),
        ).model_dump(mode="json")

    receipts = (calendar, document, delivery)
    outcome = EpisodeOutcome(
        episode_id=contract.episode_id,
        mail_id=mail["mail_id"],
        mail_sha256=mail["content_sha256"],
        customer_id=customer["customer_id"],
        receipts=receipts,
        reply=render_reply(contract, customer, receipts),
    )
    return outcome.model_dump(mode="json")


def run(
    contract: EpisodeContract,
    fixture_url: str,
    state: DBOSState,
) -> EpisodeResult:
    _fixture_client(fixture_url)
    if version("dbos") != "2.30.0":
        raise RuntimeError("de kandidaat vereist exact DBOS Python 2.30.0")
    database_url = state.database_url
    system_schema = state.system_schema
    contract_body = contract.model_dump(mode="json")
    executor_id = f"eva2-dbos-{sha256({'state_id': state.state_id, 'schema': system_schema})[:16]}"
    config: DBOSConfig = {
        "name": APPLICATION_NAME,
        "application_version": APPLICATION_VERSION,
        "executor_id": executor_id,
        "system_database_url": database_url,
        "dbos_system_schema": system_schema,
        "log_level": "ERROR",
        "console_log_level": "ERROR",
        "enable_otlp": False,
        "run_admin_server": False,
    }
    DBOS(config=config)
    try:
        DBOS.launch()
        workflow_id = f"eva2-dbos:{contract.episode_id}"
        with SetWorkflowID(workflow_id):
            handle = DBOS.start_workflow(episode_workflow, contract_body, fixture_url)
        body = handle.get_result(polling_interval_sec=0.05)
        if not isinstance(body, dict):
            raise RuntimeError("workflowresultaat is geen object")
        if body.get("contract") == "EvaArchitectureOutcome/v1":
            return EpisodeOutcome.model_validate(body)
        if body.get("contract") == "EvaArchitectureFailureOutcome/v1":
            return EpisodeFailureOutcome.model_validate(body)
        raise RuntimeError("workflowresultaat heeft onbekend contract")
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voer één Noor-episode uit tegen voorbereide DBOS/PostgreSQL-state.",
        epilog=(
            "Bereid state voor met dbos/prepare_state.sh STATE_DIR en ruim haar "
            "op met dbos/cleanup_state.sh STATE_DIR. Dezelfde state-dir hervat "
            "dezelfde workflow na een procescrash."
        ),
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    outcome = run(
        load_contract(args.contract),
        args.fixture,
        load_state(args.state_dir),
    )
    print(canonical_json(outcome.model_dump(mode="json")), flush=True)


if __name__ == "__main__":
    main()
