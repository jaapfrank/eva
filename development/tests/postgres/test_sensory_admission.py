"""RT-001: durable cost waiting, without model or memory-provider I/O."""

from datetime import UTC, datetime, timedelta
import hashlib
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker
from eva.application.sensory import SensoryEvent


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("boundary", ["source", "recall"])
@pytest.mark.parametrize("delay_seconds", [59, 60, 61])
def test_worker_checks_actual_time_after_blocking_io(postgres_engine, boundary, delay_seconds):
    from dataclasses import replace
    tenant = f"elapsed-{boundary}-{delay_seconds}"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    event = _event(tenant)
    store.retain(event)
    at = NOW + timedelta(minutes=6) if boundary == "source" else NOW
    current = [at]
    calls = []

    class Ports:
        def revalidate(self, original, *, now):
            calls.append("source")
            current[0] += timedelta(seconds=delay_seconds)
            return replace(original, occurred_at=now, event_type="observation_revalidated", payload={
                "entity_id": "binary_sensor.door", "current_state": "off",
                "historical_observation": {"payload": original.payload}})

        def recall_for_presence(self, query):
            calls.append("recall")
            if boundary == "recall":
                current[0] += timedelta(seconds=delay_seconds)

        def evaluate_sensory_event(self, *args):
            calls.append("model")
            return FreeWillDecision("silence", "Fixture", 0, "fixture")

    ports = Ports()
    worker = SensoryDecisionWorker(store, ports, ports,
        IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
        tenant_id=tenant, installation_id="home", principal_id="owner",
        revalidator=ports, clock=lambda: current[0])
    result = worker.run_once(now=at)
    history = store._timeline.events(f"sensory:{event.source_event_id}")
    assert store._event_from_thread(f"sensory:{event.source_event_id}") == event
    if delay_seconds < 60:
        assert result == "silence"
        assert calls == (["source"] if boundary == "source" else []) + ["recall", "model"]
        started = next(e for e in history if e.event_type == "WILL_MODEL_STARTED")
        assert started.occurred_at == current[0]
    else:
        assert result == ("failed" if boundary == "source" else "idle")
        assert calls == [boundary]
        assert not any(e.event_type in {"WILL_MODEL_STARTED", "SENSORY_EVENT_REVALIDATED",
            "WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED"} for e in history)


@pytest.mark.parametrize("offset", [-1, 31])
def test_revalidation_rejects_observation_outside_claim_window(postgres_engine, offset):
    from dataclasses import replace
    tenant = f"observation-window-{offset}"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    original = _event(tenant)
    store.retain(original)
    at = NOW + timedelta(minutes=6)
    assert store.claim_next(now=at) is None
    claim = store.claim_next_revalidation(now=at)
    observed = replace(claim.event, event_type="observation_revalidated",
        occurred_at=at + timedelta(seconds=offset))
    before = store._timeline.events(claim.thread_id)
    with pytest.raises(ValueError):
        store.complete_revalidation(claim, observed, now=at + timedelta(seconds=30))
    assert store._timeline.events(claim.thread_id) == before


def _group_store(engine, *, size=16, tenant="grouped-history"):
    return PostgresSensoryEventStore(
        engine, tenant_id=tenant, installation_id="home", historical_group_size=size,
    )


def _seed_group_history(engine, *, count=3, tenant="grouped-history", entity="binary_sensor.door"):
    from dataclasses import replace
    target = _group_store(engine, tenant=tenant)
    originals = []
    for index in range(count):
        event = replace(_event(f"{tenant}-{entity}-{index}", entity), source="home_assistant",
            occurred_at=NOW + timedelta(seconds=index),
            payload={"entity_id": entity, "old_state": "off" if index % 2 == 0 else "on",
                     "new_state": "on" if index % 2 == 0 else "off"})
        target.retain(event)
        originals.append(event)
    return originals


@pytest.mark.parametrize("group_size", [1, 16])
def test_grouped_history_one_read_model_and_delivery(postgres_engine, group_size):
    from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
    from eva.application.conversation import ConversationModelUsage
    calls = []
    tenant = f"grouped-history-{group_size}"
    originals = _seed_group_history(postgres_engine, tenant=tenant)
    at = NOW + timedelta(minutes=6)
    source = HomeAssistantEventStream(base_url="http://127.0.0.1:9", token="fixture", admitted_domains=("binary_sensor",))
    def read(*, entity_id):
        calls.append("source")
        assert entity_id == "binary_sensor.door"
        return {"state": "off", "last_updated": (at - timedelta(seconds=1)).isoformat(), "attributes": {}}
    source._states.read_entity_state = read
    class Ports:
        def recall_for_presence(self, query):
            calls.append("memory")
            assert query.query_text.endswith("actuele toestand: off")
        def evaluate_sensory_event(self, event, *args):
            calls.append("model")
            assert event.payload["current_state"] == "off"
            history = event.payload["historical_observation"]
            if group_size == 16:
                members = history["payload"]["historical_observations"]
                assert [member["source_event_id"] for member in members] == [e.source_event_id for e in originals]
                assert [member["payload"]["new_state"] for member in members] == ["on", "off", "on"]
            return FreeWillDecision("respond", "Synthetic alert", 1, "fixture", "Fixturemelding.",
                usage=ConversationModelUsage.measured(model_attempts=1, tool_calls=1, input_tokens=100, output_tokens=10))
    def store():
        return _group_store(postgres_engine, size=group_size, tenant=tenant)
    def worker():
        return SensoryDecisionWorker(store(), Ports(), Ports(), IdentityProfile("Eva", "Fixture", ("Be honest",), 80),
            tenant_id=tenant, installation_id="home", principal_id="owner", revalidator=source)
    assert worker().run_once(now=at) == "respond"
    expression = store().claim_next_expression(now=at)
    assert expression is not None
    store().acknowledge_expression(expression, channel="test", provider_message_id="fixture-delivered", now=at)
    for tick in range(1, 4):
        worker().run_once(now=at + timedelta(seconds=tick))
    assert calls == ["source", "memory", "model"] * (3 if group_size == 1 else 1)
    assert store().claim_next_expression(now=at + timedelta(seconds=4)) is None
    for original in originals:
        thread = f"sensory:{original.source_event_id}"
        assert store()._event_from_thread(thread) == original
        state = store()._timeline.load(thread)
        assert store()._timeline.rebuild_projection(thread) == state
        if group_size == 16:
            assert state.phase == "will_group_decided"
            covered = store()._timeline.events(thread)[-1].payload["data"]
            assert covered["usage_accounting"] == "group_only" and "usage" not in covered


def test_grouping_atomic_ownership_scope_and_claim_restart(postgres_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import eva.adapters.postgres.sensory_events as module
    def target(size=16):
        return _group_store(postgres_engine, tenant="grouped-atomic", size=size)
    originals = _seed_group_history(postgres_engine, count=6, tenant="grouped-atomic")
    others = _seed_group_history(postgres_engine, tenant="other-group-scope")
    at = NOW + timedelta(minutes=6)
    assert target().claim_next(now=at) is None
    assert _group_store(postgres_engine, tenant="other-group-scope").claim_next(now=at) is None
    transition = module.transition_timeline_thread
    def crash(connection, scope, **kwargs):
        result = transition(connection, scope, **kwargs)
        if kwargs["event_type"] == "SENSORY_EVENT_GROUPED":
            raise RuntimeError("injected grouping transaction failure")
        return result
    with monkeypatch.context() as patcher:
        patcher.setattr(module, "transition_timeline_thread", crash)
        with pytest.raises(RuntimeError, match="injected"):
            target()._group_waiting_history(now=at)
    for event in originals:
        assert target()._timeline.load(f"sensory:{event.source_event_id}").phase == "will_waiting_revalidation"
    with ThreadPoolExecutor(max_workers=3) as pool:
        roots = list(pool.map(lambda _: target(2)._group_waiting_history(now=at), range(3)))
    assert len(set(roots)) == 3 and None not in roots
    members = [m["source_event_id"] for root in roots
        for m in target()._event_from_thread(root).payload["historical_observations"]]
    assert len(set(members)) == len(members) == 6
    assert set(members) == {e.source_event_id for e in originals}
    for event in others:
        assert _group_store(postgres_engine, tenant="other-group-scope")._timeline.load(f"sensory:{event.source_event_id}").phase == "will_waiting_revalidation"
    # The group survives a fresh store and an expired read claim. No model
    # has started yet; the existing read-only recovery is still allowed.
    claim = target().claim_next_revalidation(now=at)
    target().claim_next_revalidation(now=at + timedelta(seconds=1))
    target().claim_next_revalidation(now=at + timedelta(seconds=2))
    reclaimed = target().claim_next_revalidation(now=at + timedelta(seconds=61))
    assert claim.thread_id == reclaimed.thread_id and reclaimed.revision > claim.revision
    assert claim.event.event_type == reclaimed.event.event_type == "historical_observation_batch"


def test_routine_group_needs_no_source_memory_or_model(postgres_engine):
    _seed_group_history(postgres_engine, tenant="routine-group", entity="light.fixture")
    class Never:
        def __getattr__(self, name):
            pytest.fail(f"routine group unexpectedly invoked {name}")
    worker = SensoryDecisionWorker(_group_store(postgres_engine, tenant="routine-group"), Never(), Never(),
        IdentityProfile("Eva", "Fixture", ("Be honest",), 80), tenant_id="routine-group",
        installation_id="home", principal_id="owner", revalidator=Never())
    assert worker.run_once(now=NOW + timedelta(minutes=6)) == "silence"


@pytest.mark.parametrize("failure", ["expired_model", "partial_completion"])
def test_group_recovery_never_repeats_members_or_partial_delivery(postgres_engine, monkeypatch, failure):
    from dataclasses import replace
    import eva.adapters.postgres.sensory_events as module
    tenant = f"group-recovery-{failure}"
    def store():
        return _group_store(postgres_engine, tenant=tenant)
    originals = _seed_group_history(postgres_engine, tenant=tenant)
    at = NOW + timedelta(minutes=6)
    assert store().claim_next(now=at) is None
    read_claim = store().claim_next_revalidation(now=at)
    current = replace(read_claim.event, event_type="observation_revalidated", occurred_at=at,
        payload={"entity_id": "binary_sensor.door", "current_state": "off",
                 "historical_observation": {"payload": read_claim.event.payload}})
    store().complete_revalidation(read_claim, current, now=at)
    claim = store().mark_model_started(store().claim_revalidated(read_claim, now=at), now=at)
    assert claim is not None
    if failure == "partial_completion":
        transition = module.transition_timeline_thread
        def crash(connection, scope, **kwargs):
            result = transition(connection, scope, **kwargs)
            if kwargs["event_type"] == "WILL_GROUP_DECIDED":
                raise RuntimeError("injected completion transaction failure")
            return result
        with monkeypatch.context() as patcher:
            patcher.setattr(module, "transition_timeline_thread", crash)
            with pytest.raises(RuntimeError, match="injected"):
                store().complete(claim, FreeWillDecision("respond", "Fixture", 1, "test", "Fixture."), now=at)
        assert store()._timeline.load(claim.thread_id).phase == "will_model_started"
        assert not any(e.event_type == "PROACTIVE_EXPRESSION_PREPARED" for e in store()._timeline.events(claim.thread_id))
    assert store().claim_next(now=at + timedelta(seconds=61)) is None
    assert store()._timeline.load(claim.thread_id).phase == "will_usage_uncertain"
    assert store().claim_next_revalidation(now=at + timedelta(minutes=10)) is None
    assert store().claim_next_expression(now=at + timedelta(minutes=10)) is None
    for event in originals:
        thread = f"sensory:{event.source_event_id}"
        assert store()._event_from_thread(thread) == event
        assert store()._timeline.load(thread).phase == "will_grouped"
        assert store()._timeline.events(thread)[-1].payload["data"]["group_thread_id"] == claim.thread_id


@pytest.mark.parametrize("size", [0, 17, True])
def test_group_size_is_bounded(postgres_engine, size):
    with pytest.raises(ValueError, match="group size"):
        _group_store(postgres_engine, size=size)


def test_group_never_truncates_an_oversized_member(postgres_engine):
    from dataclasses import replace
    target = _group_store(postgres_engine, tenant="group-byte-limit")
    originals = [replace(_event(f"large-{i}"), payload={
        "entity_id": "binary_sensor.door", "old_state": "off", "new_state": "on",
        "context": "a" * 4000,
    }) for i in range(3)]
    for event in originals:
        target.retain(event)
    at = NOW + timedelta(minutes=6)
    assert target.claim_next(now=at) is None
    assert target._group_waiting_history(now=at) is None
    for event in originals:
        assert target._event_from_thread(f"sensory:{event.source_event_id}") == event
    assert target.claim_next_revalidation(now=at).event in originals


def test_group_excludes_other_sources_devices_installations_and_fresh_claims(postgres_engine):
    from dataclasses import replace
    tenant = "group-isolation"
    target = _group_store(postgres_engine, tenant=tenant)
    originals = _seed_group_history(postgres_engine, tenant=tenant)
    at = NOW + timedelta(minutes=6)
    outsiders = [replace(_event("other-source"), source="another-provider", occurred_at=NOW + timedelta(seconds=10)),
        replace(_event("other-device", "binary_sensor.other"), occurred_at=NOW + timedelta(seconds=10)),
        replace(_event("fresh-event"), source="home_assistant", occurred_at=at)]
    for event in outsiders:
        target.retain(event)
    other = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="other", historical_group_size=16)
    for event in originals:
        other.retain(event)
    assert other.claim_next(now=at) is None
    fresh = target.claim_next(now=at)
    assert fresh.event == outsiders[-1]
    root = target._group_waiting_history(now=at)
    members = target._event_from_thread(root).payload["historical_observations"]
    assert [member["source_event_id"] for member in members] == [e.source_event_id for e in originals]
    for event in outsiders:
        assert target._timeline.load(f"sensory:{event.source_event_id}").phase != "will_grouped"
    for event in originals:
        assert other._timeline.load(f"sensory:{event.source_event_id}").phase == "will_waiting_revalidation"


@pytest.mark.parametrize("outcome", ["pending", "delivered", "silent", "ambiguous", "revalidation", "uncertain"])
def test_legacy_group_result_reconciles_without_root_rewrite_or_second_call(postgres_engine, outcome):
    from dataclasses import replace
    from eva.application.free_will import SensoryDecisionClaim
    tenant = f"group-old-result-{outcome}"
    def store():
        return _group_store(postgres_engine, tenant=tenant)
    originals = _seed_group_history(postgres_engine, tenant=tenant)
    at = NOW + timedelta(minutes=6)
    assert store().claim_next(now=at) is None
    read = store().claim_next_revalidation(now=at)
    current = replace(read.event, event_type="observation_revalidated", occurred_at=at,
        payload={"entity_id": "binary_sensor.door", "current_state": "off",
                 "historical_observation": {"payload": read.event.payload}})
    store().complete_revalidation(read, current, now=at)
    claim = store().claim_revalidated(read, now=at)
    # Exact pre-group completion shape: one root result, no member transitions.
    silent = outcome == "silent"
    store()._timeline.transition(thread_id=claim.thread_id, expected_revision=claim.revision,
        checkpoint_id=f"{claim.thread_id}:legacy-decided", operation_id=claim.operation_id,
        phase="will_decided" if silent else "expression_pending", status="completed",
        event_type="WILL_DECIDED" if silent else "PROACTIVE_EXPRESSION_PREPARED", occurred_at=at,
        data={"disposition": "silence" if silent else "respond", "entity_id": "binary_sensor.door",
              "expression": None if silent else "Fixturemelding.", "provider": "fixture",
              "reason": "Fixture", "source_event_id": claim.event.source_event_id,
              "shadow_mode": True, "urgency": 0 if silent else 1,
              "usage": {"measurement": "measured", "model_attempts": 1, "tool_calls": 1,
                        "input_tokens": 100, "output_tokens": 10}}, retry_count=0)
    if outcome in {"delivered", "ambiguous"}:
        expression = store().claim_next_expression(now=at)
        assert expression is not None
        if outcome == "delivered":
            store().acknowledge_expression(expression, channel="test", provider_message_id="old-fixture-receipt", now=at)
        else:
            store().fail_expression(expression, TimeoutError("fixture acceptance unknown"), now=at)
    if outcome in {"revalidation", "uncertain"}:
        state = store()._timeline.load(claim.thread_id)
        if outcome == "revalidation":
            store()._require_revalidation(SensoryDecisionClaim(current, state.thread_id,
                state.operation_id, state.revision, state.retry_count), now=at)
        else:
            store()._timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
                checkpoint_id=f"{state.thread_id}:uncertain", operation_id=state.operation_id,
                phase="will_usage_uncertain", status="needs_help", event_type="WILL_USAGE_UNCERTAIN",
                occurred_at=at, data={"usage": None})
    root_before = store()._timeline.events(claim.thread_id)
    assert store().claim_next(now=at) is None
    assert store()._timeline.events(claim.thread_id) == root_before
    member_histories = []
    for event in originals:
        thread = f"sensory:{event.source_event_id}"
        state = store()._timeline.load(thread)
        history = store()._timeline.events(thread)
        member_histories.append(history)
        assert store()._event_from_thread(thread) == event
        assert store()._timeline.rebuild_projection(thread) == state
        if outcome in {"revalidation", "uncertain"}:
            assert state.phase == "will_grouped"
        else:
            assert state.phase == "will_group_decided"
            assert history[-1].payload["data"]["group_decision_revision"] == claim.revision + 1
            assert "usage" not in history[-1].payload["data"]
    assert store().claim_next(now=at + timedelta(seconds=1)) is None
    assert [store()._timeline.events(f"sensory:{e.source_event_id}") for e in originals] == member_histories
    if outcome == "silent":
        # A later old-version decision must refresh coverage to that exact
        # revision; competing reconcilers must not append duplicate coverage.
        from concurrent.futures import ThreadPoolExecutor
        state = store()._timeline.load(claim.thread_id)
        newer = store()._timeline.transition(thread_id=state.thread_id, expected_revision=state.revision,
            checkpoint_id=f"{state.thread_id}:later-legacy-decision", operation_id=state.operation_id,
            phase="expression_pending", status="completed", event_type="PROACTIVE_EXPRESSION_PREPARED",
            occurred_at=at + timedelta(seconds=2),
            data={"disposition": "respond", "entity_id": "binary_sensor.door", "expression": "Later fixture.",
                  "provider": "fixture", "reason": "Later fixture", "source_event_id": claim.event.source_event_id,
                  "urgency": 1, "usage": None})
        updated_root = store()._timeline.events(claim.thread_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _: store().claim_next(now=at + timedelta(seconds=2)), range(2))) == [None, None]
        assert store()._timeline.events(claim.thread_id) == updated_root
        for index, original in enumerate(originals):
            history = store()._timeline.events(f"sensory:{original.source_event_id}")
            assert len(history) == len(member_histories[index]) + 1
            assert history[-1].payload["data"]["group_decision_revision"] == newer.revision
            assert history[-1].payload["data"]["disposition"] == "respond"


def _event(label, entity="binary_sensor.door"):
    return SensoryEvent(
        "home-assistant", f"home-assistant:{label}", "state_changed", NOW,
        {"entity_id": entity, "old_state": "off", "new_state": "on"},
    )


def _legacy_claim(store, *, now):
    # Reproduce the pre-repair caller to seed genuine old failure histories.
    # The recovery under test runs without this patch on a fresh store below.
    with patch.object(store, "_restore_cost_waits"):
        return store.claim_next(now=now)


@pytest.mark.parametrize("receipt", ["valid", "local_owner", "wrong_chat", "lost_after_acceptance", "sigkill_after_acceptance"])
def test_private_owner_delivery_reaches_http_and_never_resends(postgres_engine, receipt):
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    from threading import Thread
    from eva.adapters.connectors.telegram import TelegramProactiveDelivery
    from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient
    from eva.application.free_will import ProactiveExpressionWorker
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
    from tests.postgres.test_delegated_work_store import _id, _seed_authority

    label = f"private-outbound-{receipt}"
    scope, _, binding = _seed_authority(postgres_engine, label)
    if receipt == "local_owner":
        import sqlalchemy as sa
        from eva.adapters.postgres.current_catalog import metadata
        table = metadata.tables["eva_core.principal_bindings"]
        with postgres_engine.begin() as connection:
            connection.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id).values(provider_id="eva-dashboard", channel_id="owner-local"))
    owner = _id(label, "principal")
    at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(owner), ("42",), str(binding)),))
    recipient = PostgresPrivateTelegramRecipient(postgres_engine, scope, owner=owner, binding=binding, bindings=lambda: bindings)
    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id=f"tenant:{label}", installation_id=f"installation:{label}")
    original = replace(_event("private-outbound"), occurred_at=at)
    store().retain(original)
    claim = store().claim_next(now=at)
    assert claim is not None
    store().complete(claim, FreeWillDecision("respond", "Outbound fixture", 1, "test", "Gecontroleerde testmelding."), now=at)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if receipt == "lost_after_acceptance":
                self.close_connection = True
                return
            raw = json.dumps({"ok": True, "result": {
                "message_id": 123, "chat": {"id": 43 if receipt == "wrong_chat" else 42, "type": "private"},
                "text": body["text"],
            }}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        delivery = TelegramProactiveDelivery(
            bot_token="123456:disposable-token", recipient_resolver=lambda: recipient.current_chat_id(now=at),
            api_base_url=f"http://127.0.0.1:{server.server_port}",
        )
        if receipt == "sigkill_after_acceptance":
            # Real separate process and HTTP acceptance. Kill only after the
            # installed adapter validates the provider receipt, before the
            # expression worker can commit it. The parent has no receipt to
            # inject and must not retry this ambiguous external effect.
            script = r'''
import json, os, signal, sys
from datetime import datetime
from uuid import UUID
import sqlalchemy as sa
from eva.adapters.connectors.telegram import TelegramProactiveDelivery
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient
from eva.application.free_will import ProactiveExpressionWorker
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
p = json.load(sys.stdin)
at = datetime.fromisoformat(p['at'])
engine = sa.create_engine(p['dsn'])
scope = TimelineScope(UUID(p['tenant']), UUID(p['installation']))
bindings = TelegramPrincipalBindings((TelegramPrincipalBinding(42, p['owner'], ('42',), p['binding']),))
recipient = PostgresPrivateTelegramRecipient(engine, scope, owner=UUID(p['owner']), binding=UUID(p['binding']), bindings=lambda: bindings)
delivery = TelegramProactiveDelivery(bot_token='123456:disposable-token', recipient_resolver=lambda: recipient.current_chat_id(now=at), api_base_url=p['url'])
class CrashAfterAcceptance:
    def deliver(self, claim):
        receipt = delivery.deliver(claim)
        assert receipt
        os.kill(os.getpid(), signal.SIGKILL)
store = PostgresSensoryEventStore(engine, tenant_id='tenant:' + p['label'], installation_id='installation:' + p['label'])
result = ProactiveExpressionWorker(store, CrashAfterAcceptance(), channel='telegram').run_once(now=at)
history = store._timeline.events('sensory:home-assistant:private-outbound')
raise AssertionError((result, history[-1].payload.get('data')))
'''
            child = subprocess.run(
                [sys.executable, "-B", "-c", script],
                input=json.dumps({
                    "at": at.isoformat(), "label": label,
                    "dsn": postgres_engine.url.render_as_string(hide_password=False),
                    "tenant": str(scope.tenant_id), "installation": str(scope.installation_id),
                    "owner": str(owner), "binding": str(binding),
                    "url": f"http://127.0.0.1:{server.server_port}",
                }),
                capture_output=True, text=True, timeout=20,
            )
            assert child.returncode == -9, child.stderr
            state = store()._timeline.load(claim.thread_id)
            assert state.phase == "expression_claimed" and state.status == "active"
            assert store()._timeline.rebuild_projection(claim.thread_id) == state
            assert ProactiveExpressionWorker(store(), delivery, channel="telegram").run_once(now=at + timedelta(seconds=1)) == "idle"
        else:
            assert ProactiveExpressionWorker(store(), delivery, channel="telegram").run_once(now=at) == (
                "delivered" if receipt in {"valid", "local_owner"} else "failed"
            )
        assert ProactiveExpressionWorker(store(), delivery, channel="telegram").run_once(now=at + timedelta(seconds=61)) == "idle"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert len(calls) == 1
    assert calls[0][0].endswith("/sendMessage")
    assert calls[0][1] == {"chat_id": "42", "text": "Gecontroleerde testmelding.", "disable_notification": True}
    checkpoint = store()._timeline.load(claim.thread_id)
    assert checkpoint.phase == ("expression_delivered" if receipt in {"valid", "local_owner"} else "expression_ambiguous")
    assert checkpoint.status == ("completed" if receipt in {"valid", "local_owner"} else "needs_help")
    if receipt == "sigkill_after_acceptance":
        history = store()._timeline.events(claim.thread_id)
        assert sum(e.event_type == "PROACTIVE_EXPRESSION_CLAIMED" for e in history) == 1
        assert not any(e.event_type == "PROACTIVE_EXPRESSION_DELIVERED" for e in history)


@pytest.mark.parametrize("problem", ["revoked", "expired_binding", "expired_person", "future", "group", "provider", "inactive", "removed_map", "group_map", "ambiguous_map", "other_tenant"])
def test_private_recipient_rechecks_current_authority(postgres_engine, problem):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import metadata
    from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient
    from eva.adapters.postgres.operational_timeline import TimelineScope
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
    from tests.postgres.test_delegated_work_store import _id, _seed_authority

    label = f"outbound-authority-{problem}"
    scope, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    trusted = TelegramPrincipalBinding(42, str(owner), ("42",), str(binding))
    mappings = [TelegramPrincipalBindings((trusted,))]
    recipient = PostgresPrivateTelegramRecipient(postgres_engine, scope, owner=owner, binding=binding, bindings=lambda: mappings[0])
    assert recipient.current_chat_id(now=at) == "42"
    if problem == "removed_map":
        mappings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(43, str(_id(label, "other")), ("43",), str(binding)),))
    elif problem == "group_map":
        mappings[0] = TelegramPrincipalBindings((TelegramPrincipalBinding(42, str(owner), ("-42",), str(binding)),))
    elif problem == "ambiguous_map":
        mappings[0] = TelegramPrincipalBindings((trusted, TelegramPrincipalBinding(43, str(owner), ("43",), str(binding))))
    elif problem == "other_tenant":
        recipient = PostgresPrivateTelegramRecipient(postgres_engine, TimelineScope(_id(label, "other-tenant"), scope.installation_id), owner=owner, binding=binding, bindings=lambda: mappings[0])
    else:
        table_name, values = {
            "revoked": ("principal_bindings", {"status": "revoked"}),
            "expired_binding": ("principal_bindings", {"retention_until": at}),
            "expired_person": ("principals", {"retention_until": at}),
            "future": ("principal_bindings", {"verified_at": at + timedelta(seconds=1)}),
            "group": ("principal_bindings", {"channel_id": "group"}),
            "provider": ("principal_bindings", {"provider_id": "other"}),
            "inactive": ("principals", {"status": "disabled"}),
        }[problem]
        table = metadata.tables[f"eva_core.{table_name}"]
        with postgres_engine.begin() as connection:
            connection.execute(sa.update(table).where(table.c.tenant_id == scope.tenant_id).values(**values))
    with pytest.raises((RuntimeError, ValueError)):
        recipient.current_chat_id(now=at)


@pytest.mark.parametrize("prepared_before_restart", [False, True])
def test_historical_event_reads_current_http_state_then_redecides_once(postgres_engine, prepared_before_restart):
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    from threading import Thread
    from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream

    at = NOW + timedelta(days=2)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path))
            assert self.headers["Authorization"] == "Bearer disposable-token"
            assert self.path == "/api/states/binary_sensor.door"
            body = json.dumps({
                "entity_id": "binary_sensor.door", "state": "off",
                "last_updated": (at - timedelta(minutes=2)).isoformat(),
                "attributes": {"friendly_name": "Door", "device_class": "door"},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    def store(recovery=None):
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id=f"current-source-{prepared_before_restart}",
            installation_id="home", recovery_started_at=recovery,
        )

    original = replace(_event("historical-current-state"), source="home_assistant")
    old = store()
    old.retain(original)
    claim = old.claim_next(now=NOW)
    assert claim is not None
    if prepared_before_restart:
        old.complete(claim, FreeWillDecision("respond", "Old fixture", 1, "fixture", "De deur gaat open!"), now=NOW)
    else:
        old.wait_for_admission(claim, "unverified_model_cost", now=NOW)
    thread_id = claim.thread_id
    before = old._timeline.events(thread_id)
    restored = store(at)
    # Both outbox consumers must reject the historical prepared message.
    assert restored.claim_next_expression(now=at) is None

    class Model:
        def evaluate_sensory_event(self, event, *args):
            calls.append("model")
            assert event.event_type == "observation_revalidated"
            assert event.payload["current_state"] == "off"
            assert event.payload["historical_observation"]["payload"]["new_state"] == "on"
            return FreeWillDecision("respond", "Current-state fixture", 1, "test", "De deur staat nu dicht.")
    class Memory:
        def recall_for_presence(self, query):
            calls.append("memory")
            assert query.query_text.endswith("actuele toestand: off")
            return object()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        source = HomeAssistantEventStream(
            base_url=f"http://127.0.0.1:{server.server_port}", token="disposable-token",
            admitted_domains=("binary_sensor",),
        )
        def worker(current):
            return SensoryDecisionWorker(
                current, Model(), Memory(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
                tenant_id="test", installation_id="home", principal_id="owner", revalidator=source,
            )
        # The normal idle cadence exceeds the 300s observation freshness once
        # processing time is included. Revalidation must decide in this cycle.
        assert worker(restored).run_once(now=at) == "respond"
        assert worker(store(at)).run_once(now=at + timedelta(seconds=1)) == "idle"
        expression = store(at).claim_next_expression(now=at + timedelta(seconds=1))
        assert expression is not None and expression.text == "De deur staat nu dicht."
        store(at).acknowledge_expression(expression, channel="test", provider_message_id="current-receipt", now=at + timedelta(seconds=1))
        assert worker(store(at)).run_once(now=at + timedelta(seconds=2)) == "idle"
        assert store(at + timedelta(seconds=2)).claim_next_expression(now=at + timedelta(seconds=2)) is None
        assert worker(store(at)).run_once(now=at + timedelta(seconds=301)) == "idle"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert calls == [("GET", "/api/states/binary_sensor.door"), "memory", "model"]
    history = store()._timeline.events(thread_id)
    assert history[:len(before)] == before
    assert sum(e.event_type == "SENSORY_EVENT_REVALIDATED" for e in history) == 1
    assert store()._event_from_thread(thread_id) == original


def test_revalidation_claim_concurrency_stale_proof_and_expiry(postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from eva.adapters.postgres.operational_timeline import CheckpointConflict

    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id="revalidation-claims", installation_id="home")
    original = _event("concurrent-current-read")
    store().retain(original)
    at = NOW + timedelta(minutes=6)
    assert store().claim_next(now=at) is None  # A stale observation needs a read, not a model call.
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store().claim_next_revalidation(now=at), range(4)))
    claim, = [c for c in claims if c is not None]
    assert store().claim_next_revalidation(now=at + timedelta(seconds=1)) is None
    # Read-only recovery can retry after a dead reader; stale readers cannot commit.
    later = at + timedelta(seconds=61)
    replacement = store().claim_next_revalidation(now=later)
    assert replacement is not None
    current = replace(original, occurred_at=later, event_type="observation_revalidated")
    with pytest.raises(ValueError):
        store().complete_revalidation(replacement, replace(current, source="other"), now=later)
    with pytest.raises(CheckpointConflict):
        store().complete_revalidation(claim, current, now=later)
    store().complete_revalidation(replacement, current, now=later)
    with pytest.raises(CheckpointConflict):
        store().complete_revalidation(replacement, current, now=later)
    ready = store().claim_next(now=later)
    assert ready is not None and ready.event == current


@pytest.mark.parametrize("condition", ["concurrent", "already_claimed", "expired"])
def test_exact_revalidated_claim_is_single_owner_and_keeps_freshness(postgres_engine, condition):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id=f"exact-read-{condition}", installation_id="home")
    original = _event("exact-revalidated")
    store().retain(original)
    at = NOW + timedelta(minutes=10)
    assert store().claim_next(now=at) is None
    source_claim = store().claim_next_revalidation(now=at)
    assert source_claim is not None
    current = replace(original, event_type="observation_revalidated", occurred_at=at)
    store().complete_revalidation(source_claim, current, now=at)
    if condition == "already_claimed":
        assert store().claim_next(now=at) is not None
    other = replace(_event("unrelated-latest"), occurred_at=at + timedelta(seconds=1))
    store().retain(other)
    tick = at + timedelta(seconds=301 if condition == "expired" else 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store().claim_revalidated(source_claim, now=tick), range(4)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == (1 if condition == "concurrent" else 0)
    if winners:
        assert winners[0].event == current and winners[0].thread_id == source_claim.thread_id
    assert store()._timeline.load(f"sensory:{other.source_event_id}").phase == "sensory_observed"
    assert store().claim_revalidated(source_claim, now=tick) is None
    if condition == "expired":
        assert store()._timeline.load(source_claim.thread_id).phase == "will_revalidated"


def test_revalidated_continuation_rechecks_admission_before_recall(postgres_engine):
    from dataclasses import replace
    blocker = None
    original = _event("revalidation-admission-change")
    store = PostgresSensoryEventStore(postgres_engine, tenant_id="revalidated-admission", installation_id="home")
    store.retain(original)
    at = NOW + timedelta(minutes=10)
    class Source:
        def revalidate(self, event, *, now):
            nonlocal blocker
            blocker = "unverified_model_cost"
            return replace(event, event_type="observation_revalidated", occurred_at=now)
    class NoIO:
        def recall_for_presence(self, query):
            pytest.fail("admission changed during source read; no recall permitted")
        def evaluate_sensory_event(self, *args):
            pytest.fail("admission changed during source read; no model permitted")
    worker = SensoryDecisionWorker(
        store, NoIO(), NoIO(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
        tenant_id="test", installation_id="home", principal_id="owner",
        revalidator=Source(), admission_blocker=lambda: blocker,
    )
    assert worker.run_once(now=at) == "waiting_for_admission"
    assert store._timeline.load(f"sensory:{original.source_event_id}").phase == "will_waiting_admission"


def test_existing_subscription_admission_reaches_durable_expression_once(
    postgres_engine, monkeypatch, tmp_path,
):
    from types import SimpleNamespace
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import ConversationActionCall, ConversationModelUsage
    from tests.postgres.test_runtime_resource_usage import _budget

    budget = _budget()
    calls = []
    model = PydanticConversationModel(
        auth_file=tmp_path / "unused-by-synthetic-provider", resource_budget=budget,
    )

    usage = ConversationModelUsage.measured(
        model_attempts=1, tool_calls=1, input_tokens=321, output_tokens=45,
    )

    def provider(body, parser, *, autonomous, include_usage):
        assert autonomous is True and include_usage is True
        calls.append("model")
        return ("", ConversationActionCall("synthetic-decision", body["tools"][0]["name"], {
            "disposition": "respond", "expression": "De deur ging open.",
            "reason": "Synthetic integration result.", "urgency": 1,
        })), usage

    monkeypatch.setattr(model, "_request_using", provider)

    class Memory:
        def recall_for_presence(self, query):
            calls.append("memory")
            return SimpleNamespace(confirmed_facts=())

    def store():
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id=budget.tenant_id, installation_id=budget.installation_id,
        )

    current = store()
    event = _event("subscription-admission")
    current.retain(event)
    worker = SensoryDecisionWorker(
        current, model, Memory(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
        tenant_id=budget.tenant_id, installation_id=budget.installation_id, principal_id="owner",
        admission_blocker=model.autonomous_admission_blocker,
    )
    assert model.autonomous_admission_blocker() is None
    assert worker.run_once(now=NOW) == "respond"
    assert calls == ["memory", "model"]
    restored = store()
    state = restored._timeline.load(f"sensory:{event.source_event_id}")
    assert state.phase == "expression_pending"
    assert restored._timeline.events(state.thread_id)[-1].payload["data"]["usage"]["input_tokens"] == 321
    assert restored.claim_next(now=NOW) is None
    expression = restored.claim_next_expression(now=NOW)
    assert expression is not None and expression.text == "De deur ging open."
    restored.acknowledge_expression(expression, channel="test", provider_message_id="synthetic-receipt", now=NOW)
    assert store().claim_next_expression(now=NOW) is None
    assert calls == ["memory", "model"]


@pytest.mark.parametrize("outcome", ["respond", "silence", "cooldown", "legacy_unmeasured"])
def test_decision_usage_survives_restart_and_cooldown(postgres_engine, outcome):
    from dataclasses import asdict
    from eva.application.conversation import ConversationModelUsage

    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id="usage", installation_id="home")

    usage = None if outcome == "legacy_unmeasured" else ConversationModelUsage.measured(
        model_attempts=1, tool_calls=1, input_tokens=321, output_tokens=45,
    )
    current = store()
    event = _event(f"usage-{outcome}")
    current.retain(event)
    claim = current.claim_next(now=NOW)
    responds = outcome in {"respond", "cooldown"}
    decision = FreeWillDecision(
        "respond" if responds else "silence", "Fixture decision", 1 if responds else 0,
        "test-model", "De deur ging open." if responds else None, usage=usage,
    )
    with patch.object(current, "_cooldown_applies", return_value=outcome == "cooldown"):
        current.complete(claim, decision, now=NOW)
    restored = store()
    data = restored._timeline.events(claim.thread_id)[-1].payload["data"]
    assert data["usage"] == (None if usage is None else asdict(usage))
    assert data["disposition"] == ("respond" if outcome == "respond" else "silence")
    assert restored.claim_next(now=NOW) is None


def test_failed_current_reads_are_bounded_and_do_not_reach_model(postgres_engine):
    at = NOW + timedelta(minutes=6)
    calls = []
    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id="failed-current-read", installation_id="home")
    original = _event("unavailable-current-state")
    store().retain(original)
    class Source:
        def revalidate(self, event, *, now):
            calls.append("read")
            raise TimeoutError("current state unavailable")
    class NoModel:
        def evaluate_sensory_event(self, *args):
            pytest.fail("unverified historical event must not reach model")
        def recall_for_presence(self, query):
            pytest.fail("unverified historical event must not reach memory")
    def worker(blocker=None):
        return SensoryDecisionWorker(
            store(), NoModel(), NoModel(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
            tenant_id="test", installation_id="home", principal_id="owner", revalidator=Source(),
            admission_blocker=lambda: blocker,
        )
    # A closed admission does not spend source/network work either.
    assert worker("unverified_model_cost").run_once(now=at) == "idle"
    assert calls == []
    for tick in range(3):
        assert worker().run_once(now=at + timedelta(seconds=tick)) == "failed"
    checkpoint = store()._timeline.load(f"sensory:{original.source_event_id}")
    assert checkpoint.phase == "will_revalidation_claimed"
    assert checkpoint.status == "needs_help" and checkpoint.retry_count == 3
    assert worker().run_once(now=at + timedelta(seconds=5)) == "idle"
    assert store().claim_next_expression(now=at + timedelta(seconds=5)) is None
    assert calls == ["read"] * 3


def test_revalidation_backlog_does_not_starve_under_new_arrivals(postgres_engine):
    from dataclasses import replace

    def store(tenant="revalidation-fairness"):
        return PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")

    def waiting(label, at, tenant="revalidation-fairness"):
        current = store(tenant)
        event = replace(_event(label), occurred_at=at - timedelta(days=1))
        current.retain(event)
        # Seed the exact durable waiting phase, retaining its original event.
        state = current._timeline.load(f"sensory:{event.source_event_id}")
        from eva.application.free_will import SensoryDecisionClaim
        current._require_revalidation(SensoryDecisionClaim(
            event, state.thread_id, state.operation_id, state.revision, state.retry_count,
        ), now=at)
        return event

    oldest = [waiting(f"old-{i}", NOW + timedelta(seconds=i)) for i in range(3)]
    # An older item from another scope must never enter this queue.
    waiting("other-person", NOW - timedelta(seconds=1), "other-revalidation-scope")
    for cycle, expected in enumerate(oldest):
        tick = NOW + timedelta(seconds=100 + cycle * 100)
        for arrival in range(65):
            waiting(f"arrival-{cycle}-{arrival}", tick + timedelta(seconds=arrival))
        now = tick + timedelta(seconds=70)
        # Rebuild the store every cycle: fairness must not rely on a RAM cursor.
        current = store()
        claim = current.claim_next_revalidation(now=now)
        assert claim is not None
        assert claim.event == expected
        current.complete_revalidation(
            claim, replace(expected, event_type="observation_revalidated", occurred_at=now), now=now,
        )
        state = store()._timeline.load(claim.thread_id)
        assert store()._timeline.rebuild_projection(claim.thread_id) == state
        assert store()._event_from_thread(claim.thread_id) == expected


def test_stale_routine_telemetry_needs_neither_source_read_nor_model(postgres_engine):
    store = PostgresSensoryEventStore(postgres_engine, tenant_id="old-routine", installation_id="home")
    event = _event("old-light", "light.kitchen")
    store.retain(event)
    class NoIO:
        def revalidate(self, *args, **kwargs):
            pytest.fail("routine telemetry does not need a current-source read")
        def evaluate_sensory_event(self, *args):
            pytest.fail("routine telemetry does not need a model")
        def recall_for_presence(self, query):
            pytest.fail("routine telemetry does not need memory")
    worker = SensoryDecisionWorker(
        store, NoIO(), NoIO(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
        tenant_id="old-routine", installation_id="home", principal_id="owner", revalidator=NoIO(),
    )
    at = NOW + timedelta(days=2)
    assert worker.run_once(now=at) == "silence"
    assert worker.run_once(now=at) == "idle"
    assert store.claim_next_expression(now=at) is None
    assert store._timeline.load(f"sensory:{event.source_event_id}").phase == "will_decided"


def test_budget_wait_survives_restart_then_requires_revalidation(postgres_engine):
    calls = []
    blocker = ["unverified_model_cost"]

    class Memory:
        def recall_for_presence(self, query):
            calls.append("memory")
            return object()

    class Model:
        def evaluate_sensory_event(self, *args):
            calls.append("model")
            return FreeWillDecision("silence", "Geen melding nodig.", 0, "test")

    def store():
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id="admission-test", installation_id="home"
        )

    def worker(current):
        return SensoryDecisionWorker(
            current, Model(), Memory(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
            tenant_id="admission-test", installation_id="home", principal_id="owner",
            admission_blocker=lambda: blocker[0],
        )

    first = store()
    event = _event("waiting")
    first.retain(event)
    thread = f"sensory:{event.source_event_id}"
    assert worker(first).run_once(now=NOW) == "waiting_for_admission"
    checkpoint = first._timeline.load(thread)
    assert checkpoint.phase == "will_waiting_admission"
    assert checkpoint.status == "waiting_for_proof"
    assert checkpoint.retry_count == 0 and checkpoint.deadline is None
    before = first._timeline.events(thread)
    assert before[-1].event_type == "WILL_ADMISSION_BLOCKED"
    assert before[-1].payload["data"]["reason"] == "unverified_model_cost"
    assert calls == []

    # Fresh process objects and repeated scheduler ticks do not reclaim waiting work.
    restored = store()
    restarted = worker(restored)
    for tick in range(1, 6):
        assert restarted.run_once(now=NOW + timedelta(seconds=tick)) == "idle"
    assert restored._timeline.events(thread) == before
    assert calls == []
    fresh_process = subprocess.run(
        [sys.executable, "-c", """
import os
from datetime import UTC, datetime
import sqlalchemy as sa
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
engine = sa.create_engine(os.environ['EVA2_TEST_POSTGRES_DSN'])
store = PostgresSensoryEventStore(engine, tenant_id='admission-test', installation_id='home')
assert store.claim_next(now=datetime(2026, 9, 7, 12, 0, 5, tzinfo=UTC), include_waiting=False) is None
assert store._timeline.load('sensory:home-assistant:waiting').status == 'waiting_for_proof'
engine.dispose()
print('durable-wait-ok')
"""],
        env=os.environ.copy(), capture_output=True, text=True, timeout=20,
        check=True,
    )
    assert fresh_process.stdout.strip() == "durable-wait-ok"
    assert restored._timeline.events(thread) == before

    # A new eligible event can still be classified locally while cost admission waits.
    light = _event("routine-during-wait", "light.kitchen")
    restored.retain(light)
    assert restarted.run_once(now=NOW + timedelta(seconds=6)) == "silence"
    assert calls == []

    blocker[0] = None
    assert restarted.run_once(now=NOW + timedelta(seconds=7)) == "idle"
    revalidation = store()._timeline.load(thread)
    assert revalidation.status == "waiting_for_proof"
    assert revalidation.phase == "will_waiting_revalidation"
    assert revalidation.last_error_fingerprint is None
    assert revalidation.retry_count == 0
    after = store()._timeline.events(thread)
    assert after[:-1] == before
    assert after[-1].event_type == "WILL_REVALIDATION_REQUIRED"
    assert worker(store()).run_once(now=NOW + timedelta(seconds=8)) == "idle"
    assert store()._timeline.events(thread) == after
    assert calls == []

    # Recovery is not a global budget block: newly observed work can proceed.
    fresh = SensoryEvent(
        "home-assistant", "home-assistant:after-admission", "state_changed",
        NOW + timedelta(seconds=9), event.payload,
    )
    restored.retain(fresh)
    assert worker(store()).run_once(now=NOW + timedelta(seconds=9)) == "silence"
    assert calls == ["memory", "model"]


def test_wait_rejects_unknown_reasons_and_stale_claims(postgres_engine):
    from eva.adapters.postgres.operational_timeline import CheckpointConflict

    store = PostgresSensoryEventStore(
        postgres_engine, tenant_id="admission-conflict", installation_id="home"
    )
    store.retain(_event("conflict"))
    claim = store.claim_next(now=NOW)
    assert claim is not None
    with pytest.raises(ValueError, match="blocker is invalid"):
        store.wait_for_admission(claim, "private provider response", now=NOW)
    store.wait_for_admission(claim, "unverified_model_cost", now=NOW)
    with pytest.raises(CheckpointConflict):
        store.wait_for_admission(claim, "unverified_model_cost", now=NOW)
    assert store.claim_next(now=NOW, include_waiting=False) is None


@pytest.mark.parametrize("crash_at", ["during_model", "before_commit"])
def test_interrupted_model_is_not_dispatched_again(postgres_engine, crash_at):
    """Physically kill a worker; no fail() callback or graceful cleanup."""
    calls = []
    def store():
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id=f"dispatch-{crash_at}", installation_id="home",
        )
    class Ports:
        def recall_for_presence(self, query):
            calls.append("memory")
            return None
        def evaluate_sensory_event(self, *args):
            calls.append("model")
            return FreeWillDecision("silence", "Synthetic decision", 0, "test-model")
    def worker(target):
        return SensoryDecisionWorker(
            target, Ports(), Ports(), IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
            tenant_id=f"dispatch-{crash_at}", installation_id="home", principal_id="owner",
        )
    first = store()
    event = _event(f"dispatch-{crash_at}")
    first.retain(event)
    child = subprocess.run([sys.executable, "-c", '''
import os, signal, sys
from datetime import datetime
import sqlalchemy as sa
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import FreeWillDecision, SensoryDecisionWorker
from eva.application.conversation import IdentityProfile
engine = sa.create_engine(os.environ["EVA2_TEST_POSTGRES_DSN"])
target = PostgresSensoryEventStore(engine, tenant_id="dispatch-" + sys.argv[1], installation_id="home")
def crash(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)
class Ports:
    def recall_for_presence(self, query):
        print("memory", flush=True)
    def evaluate_sensory_event(self, *args):
        print("model", flush=True)
        if sys.argv[1] == "during_model":
            crash()
        return FreeWillDecision("silence", "Synthetic decision", 0, "test-model")
if sys.argv[1] == "before_commit":
    target.complete = crash
SensoryDecisionWorker(target, Ports(), Ports(),
    IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
    tenant_id="dispatch-" + sys.argv[1], installation_id="home", principal_id="owner",
).run_once(now=datetime.fromisoformat(sys.argv[2]))
''', crash_at, NOW.isoformat()], capture_output=True, text=True, timeout=20)
    assert child.returncode == -9, child.stderr
    assert child.stdout.splitlines() == ["memory", "model"]
    # Fresh store/worker after the lease expires, not an exception retry.
    assert worker(store()).run_once(now=NOW + timedelta(seconds=61)) == "idle"
    assert worker(store()).run_once(now=NOW + timedelta(minutes=10)) == "idle"
    assert calls == []
    state = store()._timeline.load(f"sensory:{event.source_event_id}")
    assert state.phase == "will_usage_uncertain" and state.status == "needs_help"
    assert store()._event_from_thread(state.thread_id) == event
    assert store().claim_next_expression(now=NOW + timedelta(minutes=10)) is None


def test_pre_model_claim_can_recover_but_started_claim_cannot(postgres_engine):
    store = PostgresSensoryEventStore(postgres_engine, tenant_id="dispatch-fence", installation_id="home")
    event = _event("dispatch-fence")
    store.retain(event)
    first = store.claim_next(now=NOW)
    assert store.mark_model_started(first, now=NOW + timedelta(seconds=60)) is None
    # This claim explicitly recorded no model start: retry before dispatch is safe.
    second = store.claim_next(now=NOW + timedelta(seconds=61))
    assert second is not None and second.revision > first.revision
    assert store.mark_model_started(first, now=NOW + timedelta(seconds=61)) is None
    started = store.mark_model_started(second, now=NOW + timedelta(seconds=61))
    assert started is not None
    assert store.mark_model_started(second, now=NOW + timedelta(seconds=61)) is None
    store.fail(started, TimeoutError("synthetic interrupted model"), now=NOW + timedelta(seconds=62))
    assert store.claim_next(now=NOW + timedelta(minutes=10)) is None
    state = store._timeline.load(first.thread_id)
    assert state.phase == "will_usage_uncertain" and state.status == "needs_help"
    failure = store._timeline.events(first.thread_id)[-1].payload["data"]
    assert failure["failure_type"] == "TimeoutError"
    assert failure["provider_usage_error"] is False
    assert failure["usage"] is None
    assert failure["failure_fingerprint"] == hashlib.sha256(
        b"TimeoutError: synthetic interrupted model"
    ).hexdigest()
    assert "synthetic interrupted model" not in str(failure)
    assert store._timeline.rebuild_projection(first.thread_id) == state


def test_expired_legacy_claim_without_dispatch_marker_is_uncertain(postgres_engine):
    store = PostgresSensoryEventStore(postgres_engine, tenant_id="legacy-dispatch", installation_id="home")
    event = _event("legacy-dispatch")
    store.retain(event)
    state = store._timeline.load(f"sensory:{event.source_event_id}")
    # Actual pre-marker wire shape: neither started nor not-started is proved.
    store._timeline.transition(
        thread_id=state.thread_id, expected_revision=state.revision,
        checkpoint_id="legacy-model-claim", operation_id=event.source_event_id,
        phase="will_claimed", status="active", event_type="WILL_EVALUATION_CLAIMED",
        occurred_at=NOW, deadline=NOW + timedelta(seconds=60),
        data={"source_event_id": event.source_event_id, "shadow_mode": True},
    )
    assert store.claim_next(now=NOW + timedelta(seconds=61)) is None
    assert store._timeline.load(state.thread_id).phase == "will_usage_uncertain"


def test_competing_model_starts_have_one_durable_winner(postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from eva.adapters.postgres.operational_timeline import CheckpointConflict

    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id="competing-dispatch", installation_id="home")
    event = _event("competing-dispatch")
    store().retain(event)
    claim = store().claim_next(now=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store().mark_model_started(claim, now=NOW), range(2)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert [entry.event_type for entry in store()._timeline.events(claim.thread_id)].count("WILL_MODEL_STARTED") == 1
    # Neither the stale pre-model owner nor a timed-out owner can overwrite
    # a newer recovery outcome with a late answer.
    decision = FreeWillDecision("silence", "Synthetic", 0, "test-model")
    with pytest.raises(CheckpointConflict):
        store().complete(claim, decision, now=NOW)
    assert store().claim_next(now=NOW + timedelta(seconds=61)) is None
    with pytest.raises(CheckpointConflict):
        store().complete(winners[0], decision, now=NOW + timedelta(seconds=62))
    assert store()._timeline.load(claim.thread_id).phase == "will_usage_uncertain"


def test_transient_failure_keeps_existing_bounded_recovery(postgres_engine):
    store = PostgresSensoryEventStore(
        postgres_engine, tenant_id="admission-transient", installation_id="home"
    )
    event = _event("transient")
    store.retain(event)
    for attempt in range(3):
        at = NOW + timedelta(seconds=attempt)
        claim = store.claim_next(now=at, include_waiting=True)
        assert claim is not None
        store.fail(claim, TimeoutError("test provider timeout"), now=at)
    state = store._timeline.load(f"sensory:{event.source_event_id}")
    assert state.status == "needs_help" and state.retry_count == 3
    assert store.claim_next(now=NOW + timedelta(seconds=4)) is None


def test_uncertain_model_usage_survives_restart_without_recall_or_retry(postgres_engine):
    from eva.application.free_will import AutonomousUsageUncertain

    calls = []
    def store():
        return PostgresSensoryEventStore(postgres_engine, tenant_id="usage-uncertain", installation_id="home")
    class ModelAndMemory:
        def recall_for_presence(self, query):
            calls.append("recall")
            return None
        def evaluate_sensory_event(self, *args):
            calls.append("model")
            raise AutonomousUsageUncertain("autonomous provider usage is unverified")
    def worker():
        port = ModelAndMemory()
        return SensoryDecisionWorker(
            store(), port, port, IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
            tenant_id="usage-uncertain", installation_id="home", principal_id="owner",
        )
    event = _event("usage-uncertain")
    store().retain(event)
    assert worker().run_once(now=NOW) == "failed"
    checkpoint = store()._timeline.load(f"sensory:{event.source_event_id}")
    assert checkpoint.status == "needs_help"
    assert checkpoint.retry_count == 1
    assert checkpoint.phase == "will_usage_uncertain"
    assert worker().run_once(now=NOW + timedelta(seconds=1)) == "idle"
    assert worker().run_once(now=NOW + timedelta(minutes=10)) == "idle"
    assert store().claim_next_expression(now=NOW + timedelta(minutes=10)) is None
    assert calls == ["recall", "model"]
    history = store()._timeline.events(checkpoint.thread_id)
    assert history[-1].event_type == "WILL_USAGE_UNCERTAIN"
    assert history[-1].payload["data"]["usage"] is None


@pytest.mark.parametrize("attempts", [1, 3])
@pytest.mark.parametrize("admission_open", [False, True])
def test_legacy_cost_failures_wait_without_reexecution(postgres_engine, attempts, admission_open):
    from concurrent.futures import ThreadPoolExecutor
    from eva.adapters.model.pydantic_conversation import ConversationModelError

    def store():
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id=f"legacy-cost-{attempts}-{admission_open}", installation_id="home"
        )

    old = store()
    event = _event("old-cost")
    old.retain(event)
    for attempt in range(attempts):
        at = NOW + timedelta(seconds=attempt)
        claim = _legacy_claim(old, now=at)
        assert claim is not None
        old.fail(claim, ConversationModelError(
            "autonomous model spend has no verified price"
        ), now=at)
    thread = f"sensory:{event.source_event_id}"
    previous = old._timeline.load(thread)
    before = old._timeline.events(thread)
    assert previous.status == ("failed" if attempts == 1 else "needs_help")

    # Competing schedulers normalize the same retained job, never evaluate it.
    at = NOW + timedelta(seconds=10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda _: store().claim_next(now=at, include_waiting=admission_open), range(4)
        ))
    assert results == [None] * 4
    restored = store()
    after = restored._timeline.events(thread)
    state = restored._timeline.load(thread)
    assert after[:-1] == before
    assert len(after) == len(before) + 1
    assert after[-1].event_type == (
        "WILL_REVALIDATION_REQUIRED" if admission_open else "WILL_ADMISSION_BLOCKED"
    )
    assert state.status == "waiting_for_proof"
    assert state.phase == (
        "will_waiting_revalidation" if admission_open else "will_waiting_admission"
    )
    assert state.operation_id == previous.operation_id
    assert state.retry_count == attempts and state.deadline is None
    assert restored._event_from_thread(thread) == event
    assert restored.claim_next(now=at, include_waiting=admission_open) is None
    assert restored.claim_next_expression(now=at) is None
    assert restored._timeline.events(thread) == after


@pytest.mark.parametrize("admission_open", [False, True])
def test_mixed_failure_history_is_not_auto_repaired(postgres_engine, admission_open):
    from eva.adapters.model.pydantic_conversation import ConversationModelError

    store = PostgresSensoryEventStore(
        postgres_engine, tenant_id=f"legacy-mixed-{admission_open}", installation_id="home"
    )
    event = _event("mixed")
    store.retain(event)
    errors = [TimeoutError("unknown provider outcome")] + [
        ConversationModelError("autonomous model spend has no verified price")
    ] * 3
    for attempt, error in enumerate(errors):
        at = NOW + timedelta(seconds=attempt)
        claim = _legacy_claim(store, now=at)
        assert claim is not None
        store.fail(claim, error, now=at)
    thread = f"sensory:{event.source_event_id}"
    before = store._timeline.events(thread)
    assert store.claim_next(now=NOW + timedelta(seconds=10), include_waiting=admission_open) is None
    assert store._timeline.events(thread) == before
    assert store._timeline.load(thread).status == "needs_help"


@pytest.mark.parametrize("admission_open", [False, True])
def test_cost_recovery_filters_before_limit_and_preserves_other_scopes(postgres_engine, admission_open):
    import hashlib
    from eva.adapters.model.pydantic_conversation import ConversationModelError

    def store(installation):
        return PostgresSensoryEventStore(
            postgres_engine, tenant_id=f"legacy-filter-{admission_open}", installation_id=installation
        )

    local, other = store("home"), store("other-home")
    event = _event("target")
    for current in (local, other):
        current.retain(event)
        claim = current.claim_next(now=NOW)
        current.fail(claim, ConversationModelError(
            "autonomous model spend has no verified price"
        ), now=NOW)
    thread = f"sensory:{event.source_event_id}"
    other_before = other._timeline.events(thread)
    for index in range(70):
        unrelated = local._timeline.create_thread(
            thread_id=f"sensory:home-assistant:unrelated-{index}",
            checkpoint_id=f"unrelated-{index}", occurred_at=NOW + timedelta(seconds=1),
            data={}, event_type="RECOVERY_NEEDS_HELP", phase="will_claimed",
            status="needs_help",
        )
        local._timeline.transition(
            thread_id=unrelated.thread_id, expected_revision=unrelated.revision,
            checkpoint_id=f"unrelated-failure-{index}", occurred_at=NOW + timedelta(seconds=1),
            data={}, event_type="RECOVERY_NEEDS_HELP", phase="will_claimed",
            status="needs_help", retry_count=3, last_error_fingerprint="f" * 64,
        )
    # A matching failure fingerprint does not make a delivery phase safe to replay.
    fingerprint = hashlib.sha256(
        b"ConversationModelError: autonomous model spend has no verified price"
    ).hexdigest()
    ambiguous = local._timeline.create_thread(
        thread_id="sensory:home-assistant:ambiguous-delivery",
        checkpoint_id="ambiguous-delivery", occurred_at=NOW,
        data={}, event_type="PROACTIVE_EXPRESSION_AMBIGUOUS",
        phase="expression_ambiguous", status="needs_help",
    )
    local._timeline.transition(
        thread_id=ambiguous.thread_id, expected_revision=ambiguous.revision,
        checkpoint_id="ambiguous-failure", occurred_at=NOW, data={},
        event_type="PROACTIVE_EXPRESSION_AMBIGUOUS", phase="expression_ambiguous", status="needs_help",
        last_error_fingerprint=fingerprint,
    )
    assert local.claim_next(now=NOW + timedelta(seconds=2), include_waiting=admission_open) is None
    assert local._timeline.load(thread).status == "waiting_for_proof"
    assert local._timeline.load(thread).phase == (
        "will_waiting_revalidation" if admission_open else "will_waiting_admission"
    )
    assert other._timeline.events(thread) == other_before
    assert local._timeline.load("sensory:home-assistant:ambiguous-delivery").status == "needs_help"
    assert len(local._timeline.list_checkpoints(
        thread_prefix="sensory:", statuses=("needs_help",), limit=256
    )) == 71
    with pytest.raises(ValueError, match="fingerprint is invalid"):
        local._timeline.list_checkpoints(thread_prefix="sensory:", last_error_fingerprint="invalid")


def test_spontaneous_idea_enters_existing_background_worker_after_restart(postgres_engine):
    from eva.application.free_will import PersonalInitiativeProposal
    from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
    from eva.application.personal_interests import PersonalInterestWorker, PersonalInterestExploration
    tenant = "initiative-existing-worker"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id="home", initiative_principal_id="owner")
    event = _event(tenant)
    store.retain(event)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    decision = FreeWillDecision("silence", "Eigen maakidee", 0, "fixture",
        initiative=PersonalInitiativeProposal("Gezinsplan", "Zelf gekozen", "Maak een bruikbaar gezinsplan"))
    store.complete(claim, decision, now=NOW)
    data = store._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]
    assert data["status"] == "adopted"
    events = store._timeline.events(data["thread_id"])
    assert events[0].payload["data"]["origin"] == "eva_initiative"
    assert events[0].payload["data"]["source_event_id"] == event.source_event_id
    calls = []
    class Ports:
        def explore(self, interest):
            calls.append(interest)
            return PersonalInterestExploration("Een concreet bruikbaar gezinsplan.", "fixture")
        def retain_bounded(self, document, *, now):
            pass
    ports = Ports()
    worker = PersonalInterestWorker(PostgresPersonalInterestStore(postgres_engine), ports, ports)
    assert worker.run_once(tenant_id=tenant, installation_id="home", now=NOW) == "completed"
    assert len(calls) == 1 and calls[0].intent_id == data["intent_id"]
    assert worker.run_once(tenant_id=tenant, installation_id="home", now=NOW) == "idle"
    from eva.adapters.postgres.conversation_actions import PostgresPersonalPrototypeStore, PostgresPersonalProductReleaseStore
    from eva.application.personal_interests import (PersonalPrototypeWorker, PersonalProductReleaseWorker,
        PersonalPrototype, PersonalProductListing)
    ports.build_prototype = lambda claim: PersonalPrototype("Gezinsplan", "gezinsplan.md", "text/markdown", "# Gezinsplan\nEen invulbaar plan.", "fixture")
    ports.prepare_product_listing = lambda claim: PersonalProductListing("Gezinsplan", "Een invulbaar gezinsplan.", 499, ("gezin", "week", "plan"), "fixture")
    for next_worker in (PersonalPrototypeWorker(PostgresPersonalPrototypeStore(postgres_engine), ports, ports),
                        PersonalProductReleaseWorker(PostgresPersonalProductReleaseStore(postgres_engine), ports, ports)):
        assert next_worker.run_once(tenant_id=tenant, installation_id="home", now=NOW) == "completed"
    assert store._timeline.load(data["thread_id"]).phase == "release_ready"
    from dataclasses import replace
    next_event = replace(event, source_event_id=event.source_event_id + "-next")
    store.retain(next_event)
    next_claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.complete(next_claim, decision, now=NOW)
    next_data = store._timeline.events(next_claim.thread_id)[-1].payload["data"]["initiative"]
    assert next_data["status"] == "adopted" and next_data["intent_id"] != data["intent_id"]


def test_spontaneous_idea_rolls_back_with_decision_and_does_not_fan_out(postgres_engine):
    from eva.application.free_will import PersonalInitiativeProposal
    tenant = "initiative-atomic"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id="home", initiative_principal_id="owner")
    event = _event(tenant)
    store.retain(event)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    decision = FreeWillDecision("silence", "Eigen maakidee", 0, "fixture",
        initiative=PersonalInitiativeProposal("Gezinsplan", "Zelf gekozen", "Maak een plan"))
    with patch("eva.adapters.postgres.sensory_events.transition_timeline_thread", side_effect=RuntimeError("abort")):
        with pytest.raises(RuntimeError, match="abort"):
            store.complete(claim, decision, now=NOW)
    assert not store._timeline.list_checkpoints(thread_prefix="intent:personal-interest:")
    store.complete(claim, decision, now=NOW)
    first = store._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]
    from dataclasses import replace
    other = replace(event, source_event_id=event.source_event_id + "-other")
    store.retain(other)
    second = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.complete(second, decision, now=NOW)
    result = store._timeline.events(second.thread_id)[-1].payload["data"]["initiative"]
    assert result == {"status": "not_admitted", "reason": "active_initiative_exists", "thread_id": first["thread_id"]}
    assert len(store._timeline.list_checkpoints(thread_prefix="intent:personal-interest:")) == 1


def test_spontaneous_idea_cannot_supply_its_own_owner_scope(postgres_engine):
    from eva.application.free_will import PersonalInitiativeProposal
    tenant = "initiative-no-owner"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant, installation_id="home")
    store.retain(_event(tenant))
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.complete(claim, FreeWillDecision("silence", "Eigen maakidee", 0, "fixture",
        initiative=PersonalInitiativeProposal("Plan", "Zelf gekozen", "Maak een plan")), now=NOW)
    assert not store._timeline.list_checkpoints(thread_prefix="intent:personal-interest:")
    result = store._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]
    assert result["status"] == "not_admitted"


def test_notification_cooldown_preserves_private_initiative(postgres_engine):
    from eva.application.free_will import PersonalInitiativeProposal
    tenant = "initiative-cooldown"
    store = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id="home", initiative_principal_id="owner")
    store.retain(_event(tenant))
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    with patch.object(store, "_cooldown_applies", return_value=True):
        store.complete(claim, FreeWillDecision("respond", "Eigen idee", 1, "fixture", "Een bericht.",
            initiative=PersonalInitiativeProposal("Plan", "Eigen idee", "Maak een plan")), now=NOW)
    data = store._timeline.events(claim.thread_id)[-1].payload["data"]
    assert data["disposition"] == "silence" and data["expression"] is None
    assert data["initiative"]["status"] == "adopted"
