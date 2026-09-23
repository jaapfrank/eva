"""Bind dashboard work controls to the configured private owner and shared service."""
from datetime import UTC, datetime
from uuid import uuid4

from eva.application.conversation import ConversationTurn


def dashboard_delegated_work(service, *, tenant_id, installation_id,
                             owner_principal_id, principal_binding_id,
                             operation, work_id=None, operation_id=None, search=None, revision=None):
    if (service is None or tenant_id is None or installation_id is None
            or owner_principal_id is None or principal_binding_id is None):
        raise RuntimeError("owner work unavailable")
    if operation not in {"related", "revise_digital", "list", "search", "get", "result", "stop", "resume", "return_poll", "return_ack", "schedule_cancel", "schedule_cancel_chain"}:
        raise ValueError("invalid dashboard work operation")
    # Same fixed principal and conversation namespace as Runtime.dashboard_turn.
    # No model call: this is a button invoking the existing owner service.
    turn = ConversationTurn.private(
        tenant_id, installation_id, str(owner_principal_id),
        f"dashboard:{principal_binding_id}", f"dashboard:{uuid4()}",
        "Werkbediening", datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    service.validate_authority(turn)
    if operation.startswith("return_"):
        result = {"contract": "EvaPrivateWorkReturn/v1", **service.private_return(
            turn, operation.removeprefix("return_"), work_id=work_id, operation_id=operation_id)}
    elif operation in {"schedule_cancel", "schedule_cancel_chain"}:
        result = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                  **service.execute(operation, turn, schedule_id=work_id, request_key=turn.message_id)}
    elif operation == "revise_digital":
        result = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                  **service.execute(operation, turn, revision=revision, request_key=turn.message_id)}
    elif operation == "search":
        result = service.execute(operation, turn, search=search, request_key=turn.message_id)
    else:
        result = service.execute(operation, turn, work_id=work_id, request_key=turn.message_id)
    service.validate_authority(turn)
    return result
