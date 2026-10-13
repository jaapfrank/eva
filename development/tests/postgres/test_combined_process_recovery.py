"""Durable work/memory/private-return recovery across real process loss.

This uses packaged application services, not full HTTP core, Chromium or a
physical microphone. The controlled model profile is the existing worker fixture.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import sqlalchemy as sa
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.application.conversation import ConversationTurn
from eva.application.memory import ConversationMemoryDocument, MemoryRetentionReceipt
from eva.application.memory_audience import MemoryAudience
from eva.bootstrap import delegated_work_owner_service as owner_module
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.delegated_work import DelegatedWorkSpec
from tests.postgres.test_delegated_work_store import (
    delegated_engine, process_tmp_path, _seed_authority, _id, _process_spec,
    _process_environment, _start_process, _closed_process, _dsn_secret,
)
from eva.adapters.postgres.operational_timeline import external_timeline_scope

LABEL = os.environ.get("EVA_TEST_RECOVERY_LABEL", "combined-process-recovery")
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
MEMORY = "My corrected preference is green."


def _publish(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def _context(dsn, mode):
    engine = sa.create_engine(dsn)
    scope = external_timeline_scope("tenant:" + LABEL, "installation:" + LABEL, "test")
    owner, binding = _id(LABEL, "principal"), _id(LABEL, "binding")
    store = PostgresDelegatedWorkStore(engine, scope)
    at = NOW if mode == "prepare" else NOW + timedelta(seconds={"claim": 4, "recover": 6, "expired": 40}[mode])
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner,
        principal_binding_id=binding, clock=lambda: at)
    turn = ConversationTurn.private("tenant:" + LABEL, "installation:" + LABEL,
        str(owner), "voice:combined-recovery", "message:prepare", "Research a synthetic topic",
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    memory = PostgresMemoryOutbox(engine, tenant_id=turn.tenant_id, installation_id=turn.installation_id)
    sources = PostgresOwnerMemorySources(engine, tenant_id=turn.tenant_id,
        installation_id=turn.installation_id, owner=owner, binding=binding, authority_scope=scope)
    return engine, scope, service, turn, memory, sources, at


def _child(mode, directory):
    dsn = (directory / "database-dsn").read_text()
    engine, scope, service, turn, memory, sources, at = _context(dsn, mode)
    state_path = directory / "state.json"
    try:
        if mode == "prepare":
            # Substitute only the controlled worker profile, retaining normal
            # admission identity, episode, grant, task and owner authorization.
            original = owner_module.build_unadmitted_delegated_work_spec
            profile = _process_spec(LABEL, scope, _id(LABEL, "episode"), service.principal_binding_id)
            keys = ("model_profile_id", "model_profile_release_sha256", "worker_profile_id",
                    "worker_profile_release_sha256", "worker_program_sha256",
                    "engine_release_sha256", "output_schema_sha256")
            def controlled_profile(**kwargs):
                spec = original(**kwargs)
                return DelegatedWorkSpec.model_validate(spec.model_dump(mode="json") |
                    {key: getattr(profile, key) for key in keys})
            owner_module.build_unadmitted_delegated_work_spec = controlled_profile
            assert service.private_return(turn, "open")["status"] == "open"
            admitted = service.execute("start", turn, task=turn.text, request_key="prepare")
            document = ConversationMemoryDocument(tenant_id=turn.tenant_id,
                installation_id=turn.installation_id, principal_id=turn.principal_id,
                conversation_id=turn.conversation_id, document_id="combined:corrected-memory",
                source_ref="correction:1", evidence_ref="conversation-reply:correction:1",
                content=MEMORY, occurred_at=turn.occurred_at, privacy_class="internal",
                trust="direct_user", audience=MemoryAudience.private_person(principal_id=turn.principal_id))
            job = memory.begin(memory.enqueue(document), now=NOW)
            digest = hashlib.sha256(MEMORY.encode()).hexdigest()
            memory.succeed(job, MemoryRetentionReceipt("controlled-memory", document.document_id, digest), now=NOW)
            page = sources.page(now=NOW)
            assert len(page.sources) == 1 and page.sources[0].retention_state == "retained"
            state_path.write_text(json.dumps({"work_id": admitted["work"]["work_id"],
                "memory_source_id": page.sources[0].source_id, "memory_sha256": digest}))
        elif mode == "claim":
            state = json.loads(state_path.read_text())
            result = service.private_return(turn, "poll")["result"]
            assert result["work_id"] == state["work_id"]
            state.update(result_sha256=result["output_sha256"], operation_id=result["operation_id"],
                         claimant_pid=os.getpid())
            state_path.write_text(json.dumps(state))
            (directory / "claim-ready").write_text("committed claim; no acknowledgement")
            while True:
                time.sleep(1)
        else:
            state = json.loads(state_path.read_text())
            # The expired original voice session is never silently renewed after
            # process loss. This proves session authority, not physical mic state.
            if mode == "recover":
                assert service.private_return(turn, "poll")["result"] is None
            else:
                with pytest.raises(ValueError, match="expired"):
                    service.private_return(turn, "poll")
            with pytest.raises(ValueError, match="cannot be reopened"):
                service.private_return(turn, "open")
            replacement = replace(turn, conversation_id="voice:replacement-session:" + mode)
            assert service.private_return(replacement, "open")["status"] == "open"
            assert service.private_return(replacement, "poll")["result"] is None
            other = ConversationTurn.private(turn.tenant_id, turn.installation_id,
                "person:other", turn.conversation_id, "other-message", "Read result", turn.occurred_at)
            with pytest.raises(DelegatedWorkConflict, match="private owner scope"):
                service.execute("result", other, work_id=state["work_id"], request_key="other-read")
            result = service.execute("result", turn, work_id=state["work_id"], request_key="read")["work"]
            assert result["state"] == "succeeded"
            assert result["result"]["output_sha256"] == state["result_sha256"]
            document, status = sources.document(state["memory_source_id"], now=at)
            assert document.content == MEMORY and status == "retained"
            assert hashlib.sha256(document.content.encode()).hexdigest() == state["memory_sha256"]
            assert not memory.pending()
            cards = service.store.workboard(**service._owner, now=at).cards
            assert len(cards) == 1 and cards[0].column == "review"
            assert len(service.execute("list", turn, request_key="list")["jobs"]) == 1
            _publish(directory / (mode + ".json"), {
                "memory_sha256": state["memory_sha256"], "result_sha256": state["result_sha256"],
                "state": result["state"], "card_column": cards[0].column,
                "recovery_pid": os.getpid(), "claimant_pid": state["claimant_pid"],
                "old_voice_session": "expired" if mode == "expired" else "open_claim_not_reoffered", "replacement_voice_result": None})
    finally:
        engine.dispose()


def _run_process_recovery(delegated_engine, process_tmp_path, *, coordinated=False):
    label = LABEL + "-browser" if coordinated else LABEL
    scope, _, _ = _seed_authority(delegated_engine, label)
    dsn = _dsn_secret(delegated_engine, process_tmp_path / "database-dsn")
    counter = process_tmp_path / "provider-calls"
    environment = _process_environment(dsn_file=dsn, scope=scope, scope_label=label,
        count_file=counter, mode="success", now="2026-08-26T12:00:02Z")
    environment["EVA_TEST_RECOVERY_LABEL"] = label
    # Test helpers are available without shadowing the candidate's installed Eva
    # modules with /workspace/src. Both children inherit the same packaged code.
    import eva
    installed_parent = str(Path(eva.__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(
        [installed_parent, str(Path(__file__).resolve().parents[3]), *sys.path]))
    def core(mode):
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), mode, str(process_tmp_path)],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _closed_process(core("prepare"))
    _closed_process(_start_process(environment))
    claimant = core("claim")
    try:
        deadline = time.monotonic() + 15
        while not (process_tmp_path / "claim-ready").exists():
            if claimant.poll() is not None:
                _closed_process(claimant)
                pytest.fail("claim process exited without durable marker")
            assert time.monotonic() < deadline, "claim barrier timed out"
            time.sleep(.05)
        if coordinated:
            _browser_barrier(process_tmp_path, "browser-pending.json")
            assert claimant.poll() is None, "claimant must be alive in shared failure window"
        claimant.kill()
        claimant.communicate(timeout=10)
        assert claimant.returncode == -9
        if coordinated:
            _publish(process_tmp_path / "core-crashed.json", {
                "claimant_pid": claimant.pid, "signal": "SIGKILL", "returncode": -9})
            _browser_barrier(process_tmp_path, "browser-authority-restored.json")
    finally:
        if claimant.poll() is None:
            claimant.kill()
            claimant.communicate(timeout=10)
    _closed_process(_start_process(environment))
    _closed_process(core("recover"))
    _closed_process(core("expired"))
    receipt = json.loads((process_tmp_path / "expired.json").read_text())
    earlier = json.loads((process_tmp_path / "recover.json").read_text())
    assert earlier["old_voice_session"] == "open_claim_not_reoffered"
    assert receipt["recovery_pid"] != receipt["claimant_pid"]
    assert counter.read_text() == "provider-call\n"
    if coordinated:
        browser = _browser_barrier(process_tmp_path, "browser-complete.json")
        assert browser["lateClicks"] == 0
        assert browser["result_sha256"] == receipt["result_sha256"]
        assert browser["memory_sha256"] == receipt["memory_sha256"]
    print(json.dumps({"proof": "durable-application-process-recovery", **receipt,
        "controlled_provider_calls": 1, "full_runtime_restart": False,
        "browser_gateway_voice_hardware": (
            "coordinated real browser and logical gateway; no physical voice" if coordinated else "not exercised")}, sort_keys=True))


def _browser_barrier(directory, filename):
    deadline = time.monotonic() + 45
    while not (directory / filename).exists():
        error = directory / "browser-failed.json"
        assert not error.exists(), error.read_text() if error.exists() else ""
        assert time.monotonic() < deadline, "browser barrier timed out: " + filename
        time.sleep(.05)
    return json.loads((directory / filename).read_text())


def test_process_loss_preserves_memory_result_and_private_return_fence(delegated_engine, process_tmp_path):
    _run_process_recovery(delegated_engine, process_tmp_path)


@pytest.mark.skipif(not os.environ.get("EVA_TEST_CHROME_RECOVERY_DIRECTORY"),
                    reason="requires explicitly coordinated isolated Chrome fixture")
def test_shared_browser_and_durable_process_failure_window(delegated_engine):
    directory = Path(os.environ["EVA_TEST_CHROME_RECOVERY_DIRECTORY"])
    assert directory.is_absolute() and directory.is_dir() and not directory.is_symlink()
    assert directory.resolve() == directory
    assert not (directory / "database-dsn").exists(), "requires a fresh dedicated proof directory"
    _browser_barrier(directory, "browser-ready.json")
    try:
        _run_process_recovery(delegated_engine, directory, coordinated=True)
    except BaseException as error:
        _publish(directory / "core-failed.json", {"error": repr(error)})
        raise


if __name__ == "__main__":
    _child(sys.argv[1], Path(sys.argv[2]))
