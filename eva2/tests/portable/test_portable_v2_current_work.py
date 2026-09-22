"""Real encrypted current-work round trips over isolated PostgreSQL databases."""
from datetime import timedelta
from pathlib import Path
import json
import os
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import TimelineScope, external_timeline_scope
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
from eva.application.conversation import ConversationTurn
from eva.application.delegated_work_delivery import PrivateWorkDestination
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
from eva.operations.install_schema import install_schema
from eva.providers.document_task_input import DocumentTaskInput, DocumentTaskPool, DocumentTaskSlot
from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
from tools.installer_portable_restore import InstallerPortableRestore
from tools.portable_v2_export import export_current_archive
from tools.portable_v2_reader import read_current_archive
from tools.portable_transport import seal_portable_archive
from tests.portable.restore_authority_fixture import restore_authority_for_archive
from tests.portable.test_portable_restore_full_schema import _exact_fixture_row, _synthetic_memory_command
from tests.support.noor_4d_repository import PostgresNoor4DRepository
from tests.postgres.test_delegated_work_store import _spec
from tests.postgres.test_delegated_delivery_store import NOW, succeed

ROOT = Path(__file__).resolve().parents[2]
PASSPHRASE = "synthetic-current-work-recovery-password"
RUNTIME = {"tenant_id": "portable-v2-runtime", "installation_id": "portable-v2-installation"}


@pytest.fixture(scope="module")
def current_archive(tmp_path_factory):
    raw_dsn = os.environ.get("EVA2_IN002_TEST_ADMIN_DSN")
    if not raw_dsn:
        pytest.skip("isolated IN002 PostgreSQL admin DSN required")
    admin = sa.create_engine(raw_dsn, isolation_level="AUTOCOMMIT")
    directory = tmp_path_factory.mktemp("portable-v2")
    databases = []
    engines = []

    def database():
        name = "eva2_portable_v2_" + uuid4().hex
        with admin.connect() as connection:
            connection.exec_driver_sql("CREATE DATABASE " + name)
        databases.append(name)
        engine = sa.create_engine(admin.url.set(database=name))
        engines.append(engine)
        receipt = install_schema(engine.url.render_as_string(hide_password=False), alembic_ini=ROOT / "alembic.ini")
        assert receipt.final_revision == "4e_0008_portable_current"
        return engine

    try:
        source = database()
        tenant = str(_exact_fixture_row("static/tenant").values["tenant_id"])
        installation = str(_exact_fixture_row("static/installation").values["installation_id"])
        owner = _exact_fixture_row("static/principal-owner").values["principal_id"]
        binding = uuid4()
        evidence, conversation, message, _, _, _ = _synthetic_memory_command(
            tenant_id=tenant, installation_id=installation, principal_id=str(owner))
        with source.begin() as connection:
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            connection.exec_driver_sql("SET LOCAL ROLE eva_dbos")
            repository = PostgresNoor4DRepository(connection, tenant, installation)
            for key in ("static/tenant", "static/installation", "static/identity-pack", "static/policy-pack", "static/principal-owner"):
                repository.upsert_exact(_exact_fixture_row(key))
            for table, row in (("eva_evidence.evidence_envelopes", evidence),
                               ("eva_core.conversations", conversation), ("eva_core.messages", message)):
                connection.execute(metadata.tables[table].insert().values(**row))
            connection.execute(metadata.tables["eva_core.principal_bindings"].insert().values(
                tenant_id=UUID(tenant), principal_binding_id=binding, principal_id=owner,
                provider_id="telegram", channel_id="private", external_subject_sha256=bytes.fromhex("11"*32),
                verification_evidence_id=evidence["evidence_id"], status="verified", verified_at=NOW,
                revision=1, retention_policy_id="retention:test", retention_until=NOW+timedelta(days=365),
                created_at=NOW, updated_at=NOW))
        scope = TimelineScope(UUID(tenant), UUID(installation))
        store = PostgresDelegatedWorkStore(source, scope)
        pairings = TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(owner), ("42",), str(binding)),))
        delivery = PostgresDelegatedWorkDeliveryStore(store, bindings_loader=lambda:pairings,
                                                      clock=lambda:NOW+timedelta(seconds=4))
        results = {}
        specs = {}
        for status in ("acknowledged", "ambiguous", "claimed", "stopped"):
            spec = _spec("portable-v2-"+status, scope, uuid4(), binding).model_copy(update={"owner_principal_id":str(owner)})
            turn = ConversationTurn.private(RUNTIME["tenant_id"], RUNTIME["installation_id"],
                "telegram:42", "telegram:42", "telegram:42:"+status, "A synthetic task", "2026-08-26T12:00:00Z")
            destination = PrivateWorkDestination.from_telegram_turn(turn, owner_principal_id=owner,
                principal_binding_id=binding, telegram_user_id=42, chat_id="42", bindings=pairings)
            store.admit(spec=spec, title="Synthetic work", summary="Synthetic recovery", worker_role="Research",
                retention_policy_id="retention:test", retention_until=NOW+timedelta(days=365),
                occurred_at=NOW, delivery_destination=destination)
            specs[status] = spec
            if status == "stopped":
                store.request_stop(spec.work_id, owner_principal_id=owner, principal_binding_id=binding,
                                   now=NOW+timedelta(seconds=1))
                continue
            results[status] = succeed(store, spec)
            claim = delivery.claim_next(now=NOW+timedelta(seconds=4))
            assert claim is not None and claim.work_id == spec.work_id
            if status == "acknowledged":
                delivery.acknowledge(claim, "42:7", now=NOW+timedelta(seconds=5))
            elif status == "ambiguous":
                delivery.mark_ambiguous(claim, "TimeoutError", now=NOW+timedelta(seconds=5))
        doc_turn = ConversationTurn.private(RUNTIME["tenant_id"], RUNTIME["installation_id"], str(owner),
            "dashboard:"+str(binding), "document-source", "Create synthetic documents", "2026-08-26T12:00:00Z")
        journal = PostgresConversationJournal(source, external_timeline_scope(**RUNTIME, missing_error="missing"))
        journal.admit(doc_turn)
        goals = PostgresDurableGoalStore(source, **RUNTIME)
        pool = DocumentTaskPool((DocumentTaskSlot("legacy", "a"*64),), "b"*64)
        document = pool.claim(goals, task=DocumentTaskInput("Retained project", "First", "Second"),
            turn=doc_turn, call_id="create-document", authority_sha256="c"*64,
            tenant_id=tenant, installation_id=installation, owner_principal_id=str(owner))
        plan = DocumentGoalPlan.from_json(json.dumps(document["plan"]))
        goal = PreparedDocumentGoal.from_json(json.dumps(document["definition"]), plan).goal
        goals.admit(goal)
        goals.cancel(goal.goal_id, str(owner), "Stopped before execution", NOW+timedelta(seconds=1))
        archive = directory / "archive"
        with source.connect() as connection:
            export_current_archive(connection, tenant_id=tenant, installation_id=installation,
                runtime_identity=RUNTIME, destination=archive, created_at="2026-09-13T00:00:00Z", export_sequence=1)
        sealed = directory / "current.eva"
        seal_portable_archive(archive, sealed, passphrase=PASSPHRASE, validator=read_current_archive)
        yield {"database":database, "archive":archive, "blob":sealed.read_bytes(), "scope":scope,
               "owner":owner, "binding":binding, "pairings":pairings, "results":results,
               "specs":specs, "document":document, "goal":goal}
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as connection:
            for name in reversed(databases):
                connection.exec_driver_sql("DROP DATABASE " + name)
        admin.dispose()


def workflow(fixture, tmp_path):
    target = fixture["database"]()
    authority = [restore_authority_for_archive((fixture["archive"] / "archive.json").read_bytes())]
    workspace = tmp_path / "private"
    workspace.mkdir(mode=0o700)
    wizard = InstallerPortableRestore(engine=target, authority_reader=lambda:authority[0],
        workspace=workspace, object_store_root=tmp_path / "inactive-cas", runtime_identity=RUNTIME)
    return wizard, target, authority


def test_encrypted_roundtrip_preserves_results_stop_and_delivery_fences(current_archive, tmp_path):
    fixture = current_archive
    wizard, target, _ = workflow(fixture, tmp_path)
    preview = wizard.prepare(fixture["blob"], PASSPHRASE)
    assert preview["summary"]["target_activation"] == "closed"
    result = wizard.execute(preview["confirmation"])
    assert result["rebuilt_checkpoints"] > 0
    store = PostgresDelegatedWorkStore(target, fixture["scope"])
    authority = {"owner_principal_id":fixture["owner"], "principal_binding_id":fixture["binding"]}
    for name, expected in fixture["results"].items():
        assert store.load(fixture["specs"][name].work_id, **authority).result == expected
    assert store.load(fixture["specs"]["stopped"].work_id, **authority).state == "stopped"
    delivery = PostgresDelegatedWorkDeliveryStore(store, bindings_loader=lambda:fixture["pairings"],
                                                 clock=lambda:NOW+timedelta(seconds=6))
    for name in fixture["results"]:
        assert delivery.status(fixture["specs"][name].work_id, **authority) == "delivery_" + name
    assert delivery.claim_next(now=NOW+timedelta(minutes=5)) is None
    goals = PostgresDurableGoalStore(target, **RUNTIME)
    assert goals.document_slot(fixture["document"]["slot_id"]) == fixture["document"]
    assert goals.state(fixture["goal"].goal_id, str(fixture["owner"])).status == "cancelled"
    with pytest.raises(ValueError):
        wizard.prepare(fixture["blob"], PASSPHRASE)


def test_forgotten_archive_rejected_after_preview_without_target_rows(current_archive, tmp_path):
    wizard, target, authority = workflow(current_archive, tmp_path)
    preview = wizard.prepare(current_archive["blob"], PASSPHRASE)
    authority[0] = restore_authority_for_archive((current_archive["archive"] / "archive.json").read_bytes(), admit=False, generation=2)
    with pytest.raises(ValueError, match="not authorized"):
        wizard.execute(preview["confirmation"])
    with target.connect() as connection:
        for table in ("eva_core.installations", "eva_core.operational_events", "eva_core.operational_checkpoints", "eva_core.delegated_work_runs"):
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM " + table).scalar_one() == 0


@pytest.mark.parametrize("damage", ["foreign_tenant", "unused_binding", "missing_evidence", "pending_attention", "missing_return", "missing_document_source", "missing_goal", "orphan_waiting_goal"])
def test_rehashed_invalid_current_dependencies_are_rejected(current_archive, tmp_path, damage):
    import shutil
    import hashlib
    from eva.application.portable_archive import canonical_json_bytes
    archive = tmp_path / "damaged"
    shutil.copytree(current_archive["archive"], archive)
    if damage in {"foreign_tenant", "unused_binding", "missing_evidence"}:
        path = archive / "current/principal_bindings.jsonl"
    elif damage == "pending_attention":
        path = archive / "current/attention_items.jsonl"
    else:
        path = archive / "current/operational_events.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    if damage == "foreign_tenant":
        records[0]["row"]["tenant_id"] = str(uuid4())
        records[0]["primary_key"]["tenant_id"] = records[0]["row"]["tenant_id"]
    elif damage == "unused_binding":
        extra = json.loads(json.dumps(records[0]))
        extra["row"]["principal_binding_id"] = str(uuid4())
        extra["primary_key"]["principal_binding_id"] = extra["row"]["principal_binding_id"]
        records.append(extra)
    elif damage == "missing_evidence":
        records[0]["row"]["verification_evidence_id"] = str(uuid4())
    elif damage == "pending_attention":
        from tests.portable.test_portable_restore_full_schema import _fixture_fact_rows
        records = [json.loads(json.dumps(next(row for row in _fixture_fact_rows().values()
                   if row["source_table"] == "eva_core.attention_items")))]
        records[0]["row"]["kind"] = "delegated_work"
        records[0]["row"]["state"] = "pending"
    elif damage == "missing_return":
        records = [record for record in records if not record["row"]["thread_id"].startswith("delegated-delivery:")]
    elif damage == "missing_document_source":
        records = [record for record in records if record["row"]["event_type"] != "INGRESS_COMMITTED"]
    elif damage == "missing_goal":
        records = [record for record in records if not record["row"]["thread_id"].startswith("goal:")]
    elif damage == "orphan_waiting_goal":
        extra = json.loads(json.dumps(next(record for record in records if record["row"]["event_type"] == "GOAL_ADMITTED")))
        extra["row"]["thread_id"] = "goal:" + "f" * 64
        extra["primary_key"]["thread_id"] = extra["row"]["thread_id"]
        records.append(extra)
    for record in records:
        record["row_sha256"] = hashlib.sha256(canonical_json_bytes(record["row"])).hexdigest()
    path.write_bytes(b"".join(canonical_json_bytes(record) + b"\n" for record in records))
    manifest_path = archive / "archive.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][str(path.relative_to(archive))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    # Rehashing the envelope must not bypass semantic privacy/activation checks.
    expected = {
        "foreign_tenant": "identity tenant differs",
        "unused_binding": "dependency closure is not exact",
        "missing_evidence": "verification evidence is not in the same installation",
        "pending_attention": "settled attention",
        "missing_return": "unreturned Telegram result",
        "missing_document_source": "source ingress dependency missing",
        "missing_goal": "terminal, admitted document work",
        "orphan_waiting_goal": "all durable goals to be terminal",
    }
    with pytest.raises(ValueError, match=expected[damage]):
        read_current_archive(archive)


def test_archive_needs_trusted_runtime_identity(current_archive, tmp_path):
    wizard, target, _ = workflow(current_archive, tmp_path)
    wizard.runtime_identity = {**RUNTIME, "installation_id": "different-host-identity"}
    with pytest.raises(ValueError, match="trusted runtime"):
        wizard.prepare(current_archive["blob"], PASSPHRASE)
    with target.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM eva_core.installations").scalar_one() == 0
