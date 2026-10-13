"""Opt-in digital admission retains the original owner ingress across resume."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict, DelegatedWorkNotFound
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.canonical_json import canonical_sha256


OWNER = UUID("00000000-0000-0000-0000-000000000001")
BINDING = UUID("00000000-0000-0000-0000-000000000002")


class Store:
    _engine = None
    scope = external_timeline_scope("tenant", "installation", "test scope")

    def __init__(self):
        self.rows = {}

    def validate_admission_authority(self, **kwargs):
        assert kwargs == {"owner_principal_id": OWNER, "principal_binding_id": BINDING}

    def load(self, work_id, **kwargs):
        self.validate_admission_authority(**kwargs)
        if work_id not in self.rows:
            raise DelegatedWorkNotFound("missing")
        return self.rows[work_id]

    def admit(self, *, spec, **kwargs):
        row = SimpleNamespace(spec=spec, state="queued", result=None, call_id=None,
            stop_requested_at=None, usage=None, error_code=None, error_fingerprint=None,
            **{name: kwargs[name] for name in ("title", "summary", "worker_role",
                                              "retention_policy_id", "retention_until")})
        self.rows[spec.work_id] = row
        return SimpleNamespace(disposition="inserted", work=row)


@pytest.fixture(autouse=True)
def controlled_progress(monkeypatch):
    import eva.adapters.postgres.work_corrections as corrections
    import eva.adapters.postgres.background_work_progress as progress
    monkeypatch.setattr(corrections, "work_corrections", lambda *args: ())
    monkeypatch.setattr(progress, "load_work_progress", lambda *args: None)


def setup(*, digital=True):
    path = Path(__file__).resolve().parents[3] / "src/eva/application/background_execution.py"
    profile = DelegatedDigitalProfile("model:configured", "a" * 64, "b" * 64,
        ("discover_connector_tools", "use_connector_tool"),
        (("application/background_execution.py", hashlib.sha256(path.read_bytes()).hexdigest()),),
        timeout_seconds=120)
    service = DelegatedWorkOwnerService(Store(), owner_principal_id=OWNER,
        principal_binding_id=BINDING, digital_profile=profile if digital else None,
        clock=lambda: datetime(2026, 9, 23, 12, tzinfo=UTC))
    turn = ConversationTurn.private("tenant", "installation", str(OWNER), "chat", "message",
                                    "Compare my documents", "2026-09-23T12:00:00Z")
    return service, turn


def test_digital_admission_binds_original_source_and_resume_keeps_it():
    service, turn = setup()
    result = service.execute("digital", turn, task="Compare my documents", request_key="action:first")
    work = service.store.rows[result["work"]["work_id"]]
    assert result["work"]["scope"] == "private_digital_worker"
    assert work.spec.source.message_id == turn.message_id
    assert work.spec.source_ingress_sha256 == canonical_sha256(PostgresConversationJournal._ingress_data(turn))
    assert work.spec.deadline == "2026-09-23T12:02:00Z"
    work.state = "stopped"
    later = replace(turn, message_id="resume-message", text="Continue that task",
                    occurred_at="2026-09-23T12:03:00Z")
    resumed = service.execute("resume", later, work_id=work.spec.work_id, request_key="action:resume")
    successor = service.store.rows[resumed["work"]["work_id"]].spec
    assert successor.source == work.spec.source
    assert successor.source_ingress_sha256 == work.spec.source_ingress_sha256
    assert successor.task == work.spec.task
    assert successor.execution_id == work.spec.work_id
    assert successor.work_id != work.spec.work_id
    assert successor.generation == 2
    assert successor.deadline == "2026-09-23T12:05:00Z"


def test_digital_tool_is_absent_by_default_and_existing_start_remains_tool_free():
    service, turn = setup(digital=False)
    actions = PostgresDelegatedWorkActions(None, service)
    assert "start_digital_work" not in actions.action_names
    assert "start_digital_work" not in {tool.name for tool in actions.conversation_tools()}
    with pytest.raises(DelegatedWorkConflict, match="not composed"):
        service.execute("digital", turn, task="Compare documents", request_key="action:first")
    result = service.execute("start", turn, task="Think about this", request_key="action:first")
    assert service.store.rows[result["work"]["work_id"]].spec.contract == "DelegatedWork/v1"
    assert result["work"]["scope"] == "private_tool_free_worker"


def test_digital_tool_and_descriptor_require_explicit_composition():
    service, turn = setup()
    actions = PostgresDelegatedWorkActions(None, service)
    assert "start_digital_work" in actions.action_names
    descriptor = next(tool for tool in actions.conversation_tools(include_digital=True)
                      if tool.name == "start_digital_work")
    assert descriptor is not None
    result = service.execute("digital", turn, task="Compare documents", request_key="action:first")
    service.digital_profile = None
    with pytest.raises(DelegatedWorkConflict, match="configured profile"):
        service.execute("resume", turn, work_id=result["work"]["work_id"], request_key="action:resume")
