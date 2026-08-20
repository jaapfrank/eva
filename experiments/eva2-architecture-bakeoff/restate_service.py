from __future__ import annotations

import argparse
import asyncio
import os
from datetime import timedelta
from typing import Callable, Literal, TypeVar
from urllib.parse import urlsplit

import restate
from hypercorn.asyncio import serve
from hypercorn.config import Config
from pydantic import RootModel, model_validator

from common import (
    DefinitiveEffectError,
    EffectIntent,
    EffectReceipt,
    EpisodeContract,
    EpisodeFailureOutcome,
    EpisodeOutcome,
    EpisodePlan,
    ExactModel,
    FixtureClient,
    FixtureResponseError,
    ModelPlanResult,
    effect_intent,
    execute_or_reconcile,
    render_definitive_failure_reply,
    render_reply,
    run_deterministic_model,
)


T = TypeVar("T")
FIXTURE_URL_ENV = "EVA_BAKEOFF_FIXTURE_URL"
WORKFLOW_NAME = "EvaNoorWorkflow"
EffectOperation = Literal["calendar.create", "document.create", "delivery.send"]


class DurableEffectResult(ExactModel):
    contract: Literal["EvaRestateEffectResult/v1"] = "EvaRestateEffectResult/v1"
    receipt: EffectReceipt | None = None
    rejected_operation: EffectOperation | None = None

    @model_validator(mode="after")
    def exactly_one_result(self) -> DurableEffectResult:
        if (self.receipt is None) == (self.rejected_operation is None):
            raise ValueError(
                "effectresultaat vereist exact success of definitieve reject"
            )
        return self


class EpisodeResult(RootModel[EpisodeOutcome | EpisodeFailureOutcome]):
    pass


def fixture_url() -> str:
    value = os.environ.get(FIXTURE_URL_ENV, "")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RuntimeError(
            f"{FIXTURE_URL_ENV} moet een expliciete localhost-http-URL zijn"
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError(
            f"{FIXTURE_URL_ENV} mag geen pad, query of fragment bevatten"
        )
    if parsed.port is None:
        raise RuntimeError(f"{FIXTURE_URL_ENV} moet een expliciete poort bevatten")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f"{FIXTURE_URL_ENV} mag geen credentials bevatten")
    return value.rstrip("/")


def caused_by_fixture_status(error: BaseException, status: int) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, FixtureResponseError) and current.status == status:
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def execute_effect(fixture: FixtureClient, intent: EffectIntent) -> DurableEffectResult:
    try:
        return DurableEffectResult(receipt=execute_or_reconcile(fixture, intent))
    except DefinitiveEffectError as error:
        return DurableEffectResult(rejected_operation=error.operation)
    except FixtureResponseError as error:
        if 400 <= error.status < 500:
            raise restate.TerminalError(str(error), status_code=error.status) from error
        raise
    except RuntimeError as error:
        if caused_by_fixture_status(error, 429):
            raise restate.TerminalError(
                f"begrensde rate-limitafhandeling faalde gesloten: {error}",
                status_code=429,
            ) from error
        raise


def completed_receipt(
    result: DurableEffectResult,
    expected_operation: EffectOperation,
) -> EffectReceipt:
    if result.rejected_operation is not None or result.receipt is None:
        raise restate.TerminalError("duurzaam effectresultaat mist successreceipt")
    if result.receipt.operation != expected_operation:
        raise restate.TerminalError(
            "duurzaam effectresultaat hoort bij andere operatie"
        )
    return result.receipt


def definitive_failure(
    contract: EpisodeContract,
    mail: dict,
    customer: dict,
    operation: EffectOperation,
    completed: tuple[EffectReceipt, ...],
) -> EpisodeResult:
    return EpisodeResult(
        EpisodeFailureOutcome(
            episode_id=contract.episode_id,
            status="partial" if completed else "failed",
            mail_id=mail["mail_id"],
            customer_id=customer["customer_id"],
            completed_receipts=completed,
            failed_operation=operation,
            failure_class="definitive_reject",
            retryable=False,
            reply=render_definitive_failure_reply(contract, operation, completed),
        )
    )


async def durable(
    ctx: restate.WorkflowContext,
    name: str,
    result_type: type[T],
    action: Callable[[], T],
) -> T:
    """Journal one non-deterministic boundary and recover its typed result on replay."""
    return await ctx.run_typed(
        name,
        action,
        restate.RunOptions(type_hint=result_type),
    )


noor_workflow = restate.Workflow(
    WORKFLOW_NAME,
    description="Sealed EvaArchitectureEpisode/v1 Noor-flow",
    inactivity_timeout=timedelta(seconds=30),
    abort_timeout=timedelta(seconds=30),
    journal_retention=timedelta(days=1),
)


@noor_workflow.main(name="run", workflow_retention=timedelta(days=1))
async def run_noor_episode(
    ctx: restate.WorkflowContext,
    contract: EpisodeContract,
) -> EpisodeResult:
    if ctx.key() != contract.episode_id:
        raise restate.TerminalError("workflow-id wijkt af van contract episode_id")

    fixture = FixtureClient(fixture_url())
    model_result = await durable(
        ctx,
        "model.plan",
        ModelPlanResult,
        lambda: run_deterministic_model(contract, fixture),
    )
    if model_result.requests != 1:
        raise restate.TerminalError(
            "deterministische modellane deed niet exact een request"
        )
    plan: EpisodePlan = model_result.plan

    mail = await durable(ctx, "mail.read", dict, fixture.read_latest_mail)
    customer = await durable(ctx, "erp.read", dict, fixture.read_customer)
    freebusy = await durable(
        ctx,
        "calendar.freebusy",
        dict,
        fixture.read_availability,
    )
    if mail["mail_id"] != contract.expected.mail_id:
        raise restate.TerminalError("verkeerde mail gelezen")
    if customer["customer_id"] != contract.expected.customer_id:
        raise restate.TerminalError("verkeerde ERP-klant gelezen")
    if not any(slot["start"] == plan.meeting_start for slot in freebusy["available"]):
        raise restate.TerminalError("gekozen tijd is niet extern beschikbaar")

    calendar_intent = effect_intent(
        contract,
        "calendar.create",
        {
            "customer_id": customer["customer_id"],
            "start": plan.meeting_start,
            "end": plan.meeting_end,
        },
    )
    calendar_result = await durable(
        ctx,
        "calendar.create",
        DurableEffectResult,
        lambda: execute_effect(fixture, calendar_intent),
    )
    if calendar_result.rejected_operation is not None:
        if calendar_result.rejected_operation != "calendar.create":
            raise restate.TerminalError("reject hoort bij andere operatie")
        return definitive_failure(
            contract,
            mail,
            customer,
            "calendar.create",
            (),
        )
    calendar = completed_receipt(calendar_result, "calendar.create")

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
    document_result = await durable(
        ctx,
        "document.create",
        DurableEffectResult,
        lambda: execute_effect(fixture, document_intent),
    )
    if document_result.rejected_operation is not None:
        if document_result.rejected_operation != "document.create":
            raise restate.TerminalError("reject hoort bij andere operatie")
        return definitive_failure(
            contract,
            mail,
            customer,
            "document.create",
            (calendar,),
        )
    document = completed_receipt(document_result, "document.create")

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
    delivery_result = await durable(
        ctx,
        "delivery.send",
        DurableEffectResult,
        lambda: execute_effect(fixture, delivery_intent),
    )
    if delivery_result.rejected_operation is not None:
        if delivery_result.rejected_operation != "delivery.send":
            raise restate.TerminalError("reject hoort bij andere operatie")
        return definitive_failure(
            contract,
            mail,
            customer,
            "delivery.send",
            (calendar, document),
        )
    delivery = completed_receipt(delivery_result, "delivery.send")

    receipts = (calendar, document, delivery)
    return EpisodeResult(
        EpisodeOutcome(
            episode_id=contract.episode_id,
            mail_id=mail["mail_id"],
            mail_sha256=mail["content_sha256"],
            customer_id=customer["customer_id"],
            receipts=receipts,
            reply=render_reply(contract, customer, receipts),
        )
    )


app = restate.app([noor_workflow], protocol="bidi")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start de Restate SDK-service-endpoint."
    )
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19080)
    args = parser.parse_args()

    os.environ[FIXTURE_URL_ENV] = args.fixture
    fixture_url()
    config = Config()
    config.bind = [f"{args.host}:{args.port}"]
    config.h2_max_concurrent_streams = 2_147_483_647
    config.keep_alive_max_requests = 2_147_483_647
    config.keep_alive_timeout = 2_147_483_647
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()
