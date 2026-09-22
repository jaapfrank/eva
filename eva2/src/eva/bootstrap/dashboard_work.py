"""Bind dashboard work controls to the configured private owner and shared service."""
from datetime import UTC, datetime
from uuid import uuid4

from eva.application.conversation import ConversationTurn


def dashboard_delegated_work(service, *, tenant_id, installation_id,
                             owner_principal_id, principal_binding_id,
                             operation, work_id=None):
    if (service is None or tenant_id is None or installation_id is None
            or owner_principal_id is None or principal_binding_id is None):
        raise RuntimeError("owner work unavailable")
    if operation not in {"list", "get", "result", "stop", "resume"}:
        raise ValueError("invalid dashboard work operation")
    # Same fixed principal and conversation namespace as Runtime.dashboard_turn.
    # No model call: this is a button invoking the existing owner service.
    turn = ConversationTurn.private(
        tenant_id, installation_id, str(owner_principal_id),
        f"dashboard:{principal_binding_id}", f"dashboard:{uuid4()}",
        "Werkbediening", datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    service.validate_authority(turn)
    result = service.execute(operation, turn, work_id=work_id, request_key=turn.message_id)
    service.validate_authority(turn)
    return result
