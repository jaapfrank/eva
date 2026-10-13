from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from common import (
    EpisodeContract,
    EpisodeFailureOutcome,
    EpisodeOutcome,
    canonical_json,
    deterministic_plan,
    load_contract,
    render_definitive_failure_reply,
    sha256,
)


class OracleFailure(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OracleFailure(message)


def _effect_map(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    effects = evidence.get("effects")
    _require(isinstance(effects, list), "fixturebewijs mist effectlijst")
    mapped: dict[str, dict[str, Any]] = {}
    for effect in effects:
        _require(isinstance(effect, dict), "fixturebewijs bevat geen objecteffect")
        operation = effect.get("operation")
        _require(isinstance(operation, str), "fixture-effect mist operation")
        _require(operation not in mapped, f"dubbel extern effect voor {operation}")
        mapped[operation] = effect
    return mapped


def validate_episode(
    contract: EpisodeContract,
    outcome: EpisodeOutcome,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    _require(outcome.episode_id == contract.episode_id, "outcome hoort bij andere episode")
    _require(outcome.mail_id == contract.expected.mail_id, "verkeerde mail-id")
    _require(outcome.customer_id == contract.expected.customer_id, "verkeerde ERP-id")
    _require(len(outcome.receipts) == 3, "outcome heeft niet exact drie effectreceipts")

    receipts = {receipt.operation: receipt for receipt in outcome.receipts}
    expected_operations = {"calendar.create", "document.create", "delivery.send"}
    _require(set(receipts) == expected_operations, "outcome heeft verkeerde effectset")
    effects = _effect_map(evidence)
    _require(set(effects) == expected_operations, "fixture heeft verkeerde externe effectset")

    for operation, receipt in receipts.items():
        effect = effects[operation]
        expected_key = f"{contract.episode_id}:{operation}"
        _require(receipt.idempotency_key == expected_key, f"verkeerde receiptkey voor {operation}")
        _require(effect.get("idempotency_key") == expected_key, f"verkeerde externe key voor {operation}")
        request = effect.get("request")
        response = effect.get("response")
        _require(isinstance(request, dict), f"{operation} mist requestbytes")
        _require(isinstance(response, dict), f"{operation} mist responsebytes")
        _require(request.get("operation") == operation, f"{operation} requestlabel wijkt af")
        _require(effect.get("request_sha256") == sha256(request), f"{operation} requesthash wijkt af")
        _require(effect.get("response_sha256") == sha256(response), f"{operation} responsehash wijkt af")
        _require(receipt.request_sha256 == effect["request_sha256"], f"{operation} receipt requesthash wijkt af")
        _require(receipt.response_sha256 == effect["response_sha256"], f"{operation} receipt responsehash wijkt af")
        _require(receipt.readback_sha256 == sha256(effect), f"{operation} receipt bindt niet aan readback")
        _require(receipt.resource_id == response.get("resource_id"), f"{operation} resource-id wijkt af")
        try:
            datetime.fromisoformat(str(effect["committed_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise OracleFailure(f"{operation} mist canonieke committijd") from error

    calendar = effects["calendar.create"]["request"]["data"]
    _require(calendar == {
        "customer_id": contract.expected.customer_id,
        "end": contract.expected.meeting_end,
        "start": contract.expected.meeting_start,
    }, "agenda-effect heeft verkeerde inhoud")

    document = effects["document.create"]["request"]["data"]
    _require(document.get("customer_id") == contract.expected.customer_id, "document hoort bij verkeerde klant")
    _require(document.get("calendar_resource_id") == receipts["calendar.create"].resource_id, "document mist agenda-readback")
    _require(contract.expected.meeting_start in str(document.get("text", "")), "document mist actuele afspraaktijd")
    _require(contract.expected.meeting_end in str(document.get("text", "")), "document mist eindtijd")

    delivery = effects["delivery.send"]["request"]["data"]
    _require(delivery.get("calendar_resource_id") == receipts["calendar.create"].resource_id, "delivery mist agenda-readback")
    _require(delivery.get("document_resource_id") == receipts["document.create"].resource_id, "delivery mist document-readback")
    _require(delivery.get("recipient") == "planning@noorbv.example", "delivery heeft verkeerde ontvanger")

    calls = evidence.get("calls")
    _require(isinstance(calls, list), "fixturebewijs mist calllog")
    effect_attempts = [
        call for call in calls
        if call.get("method") == "POST" and call.get("path") == "/v1/effects"
    ]
    accepted_posts = [call for call in effect_attempts if call.get("status") == 201]
    _require(len(accepted_posts) == 3, "extern effect is dubbel of ontbreekt")
    for index, call in enumerate(effect_attempts):
        if call.get("status") != 429:
            continue
        retry = next(
            (
                later for later in effect_attempts[index + 1:]
                if later.get("idempotency_key") == call.get("idempotency_key")
            ),
            None,
        )
        _require(retry is not None, "rate-limit kreeg geen begrensde vervolgpoging")
        limited_at = datetime.fromisoformat(str(call["happened_at"]).replace("Z", "+00:00"))
        retried_at = datetime.fromisoformat(str(retry["happened_at"]).replace("Z", "+00:00"))
        _require(
            (retried_at - limited_at).total_seconds() >= 0.25,
            "rate-limit Retry-After is niet gerespecteerd",
        )
    model_calls = evidence.get("model_calls")
    _require(isinstance(model_calls, list), "fixturebewijs mist modelcalllog")
    _require(len(model_calls) == contract.limits.max_model_calls, "modelcallbudget is niet exact bewaakt")
    for model_call in model_calls:
        _require(model_call.get("episode_id") == contract.episode_id, "modelcall hoort bij andere episode")
        _require(model_call.get("request_sha256") == sha256(contract.user_text), "modelcall kreeg andere opdracht")
        _require(
            model_call.get("response_sha256")
            == sha256(deterministic_plan(contract).model_dump(mode="json")),
            "modelcall leverde een ander plan",
        )
    candidate_calls = [
        call for call in calls
        if call.get("path") not in {"/v1/evidence", "/v1/faults", "/v1/model-calls"}
    ]
    _require(len(candidate_calls) <= contract.limits.max_connector_calls, "connectorcallbudget overschreden")

    _require("—" not in outcome.reply and "–" not in outcome.reply, "reply bevat verboden gedachtestreep")
    _require(contract.identity.name not in outcome.reply, "reply noemt zichzelf onnatuurlijk bij naam")
    _require("receipt" not in outcome.reply.lower() and "idempot" not in outcome.reply.lower(), "reply lekt systeemjargon")
    _require(contract.memory.preferred_time in outcome.reply, "reply mist gecorrigeerde voorkeur")
    _require(contract.memory.supersedes_preferred_time not in outcome.reply, "reply gebruikt oude voorkeur")

    return {
        "contract": "EvaArchitectureOracleResult/v1",
        "episode_id": contract.episode_id,
        "outcome_sha256": sha256(outcome.model_dump(mode="json")),
        "evidence_sha256": sha256(evidence),
        "connector_calls": len(candidate_calls),
        "model_calls": len(model_calls),
        "effect_attempts": len(effect_attempts),
        "accepted_effects": len(accepted_posts),
        "hard_evidence_ok": True,
    }


def validate_definitive_failure(
    contract: EpisodeContract,
    outcome: EpisodeFailureOutcome,
    evidence: dict[str, Any],
    expected_operation: str,
) -> dict[str, Any]:
    order = ["calendar.create", "document.create", "delivery.send"]
    _require(expected_operation in order, "oracle kreeg onbekende foutgrens")
    expected_completed = order[:order.index(expected_operation)]
    _require(outcome.episode_id == contract.episode_id, "failure hoort bij andere episode")
    _require(outcome.failure_class == "definitive_reject", "definitieve reject is anders geclassificeerd")
    _require(outcome.failed_operation == expected_operation, "failure noemt andere operatie")
    _require(outcome.retryable is False, "definitieve reject is ten onrechte retryable")
    _require(outcome.status == ("partial" if expected_completed else "failed"), "onjuiste deelresultaatstatus")
    _require(outcome.mail_id == contract.expected.mail_id, "failure mist de gelezen mail")
    _require(outcome.customer_id == contract.expected.customer_id, "failure mist de ERP-klant")

    receipts = {receipt.operation: receipt for receipt in outcome.completed_receipts}
    _require(list(receipts) == expected_completed, "failure heeft verkeerde voltooide effectset")
    effects = _effect_map(evidence)
    _require(list(sorted(effects, key=order.index)) == expected_completed, "fixture-effecten passen niet bij deelresultaat")
    for operation, receipt in receipts.items():
        effect = effects[operation]
        _require(receipt.idempotency_key == effect.get("idempotency_key"), "deelreceiptkey wijkt af")
        _require(receipt.request_sha256 == effect.get("request_sha256"), "deelreceipt requesthash wijkt af")
        _require(receipt.response_sha256 == effect.get("response_sha256"), "deelreceipt responsehash wijkt af")
        _require(receipt.readback_sha256 == sha256(effect), "deelreceipt bindt niet aan readback")

    calls = evidence.get("calls")
    _require(isinstance(calls, list), "fixturebewijs mist calllog")
    effect_attempts = [
        call for call in calls
        if call.get("method") == "POST" and call.get("path") == "/v1/effects"
    ]
    rejects = [call for call in effect_attempts if call.get("status") == 422]
    _require(len(rejects) == 1, "definitieve reject is niet exact eenmaal aangeboden")
    _require(
        rejects[0].get("idempotency_key") == f"{contract.episode_id}:{expected_operation}",
        "reject hoort bij andere effectintentie",
    )
    accepted = [call for call in effect_attempts if call.get("status") == 201]
    _require(len(accepted) == len(expected_completed), "deelresultaat heeft verkeerde acceptatiecount")
    model_calls = evidence.get("model_calls")
    _require(isinstance(model_calls, list) and len(model_calls) == 1, "failure deed niet exact één modelcall")
    _require(model_calls[0].get("request_sha256") == sha256(contract.user_text), "failuremodel kreeg andere opdracht")
    _require(
        model_calls[0].get("response_sha256")
        == sha256(deterministic_plan(contract).model_dump(mode="json")),
        "failuremodel leverde een ander plan",
    )
    _require("—" not in outcome.reply and "–" not in outcome.reply, "failure reply bevat gedachtestreep")
    _require("receipt" not in outcome.reply.lower() and "retry" not in outcome.reply.lower(), "failure reply lekt systeemjargon")
    _require(
        outcome.reply == render_definitive_failure_reply(
            contract,
            outcome.failed_operation,
            outcome.completed_receipts,
        ),
        "failure reply wijkt af van het gedeelde menselijke deelresultaatcontract",
    )

    return {
        "contract": "EvaArchitectureFailureOracleResult/v1",
        "episode_id": contract.episode_id,
        "failure_sha256": sha256(outcome.model_dump(mode="json")),
        "evidence_sha256": sha256(evidence),
        "completed_effects": len(expected_completed),
        "hard_evidence_ok": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    contract = load_contract(args.contract)
    outcome = EpisodeOutcome.model_validate_json(Path(args.outcome).read_text(encoding="utf-8"))
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    print(canonical_json(validate_episode(contract, outcome, evidence)))


if __name__ == "__main__":
    main()
