"""Household lifecycle with real PostgreSQL and a deterministic public provider."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from eva.bootstrap.household_voice import HouseholdVoicePolicy
from eva.bootstrap.household_research_policy import (
    HouseholdResearchPolicy,
    HouseholdResearchAuthority,
)
from eva.bootstrap.household_work import HouseholdWorkServer, HouseholdWorkActions
from eva.bootstrap.delegated_research import (
    HouseholdResearchProfile,
    DelegatedResearchRunner,
)
from eva.bootstrap.delegated_work_provider import MODEL_PROFILE_ID
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.runtime_budget import RuntimeResourceBudget
from eva.contracts.delegated_work import load_delegated_work_spec
from eva.adapters.postgres.delegated_work import (
    DelegatedWorkNotFound,
    DelegatedWorkStoreError,
    PostgresDelegatedWorkStore,
)
from eva.operations.delegated_work_worker import DelegatedWorkWorker
from tests.postgres.test_delegated_work_store import (
    _seed_authority,
    _id,
)
from tests.postgres.test_delegated_research import Research, PUBLIC, NOW
from tests.operations.test_installer_runtime_budget import _values

from tests.postgres import test_delegated_work_store as store_fixtures

delegated_engine = store_fixtures.delegated_engine

SAT = "assist_satellite.kitchen"
DEVICE = "a" * 32


def setup(engine, label, *, conversation_label=None):
    scope, episode, binding = _seed_authority(engine, label)
    clock = [NOW]
    voice = HouseholdVoicePolicy.model_validate_json(
        json.dumps(
            dict(
                contract="HouseholdVoicePolicy/v1",
                schema_version=1,
                policy_id="voice:" + label,
                policy_version=1,
                state="enabled",
                tenant_id="tenant:" + (conversation_label or label),
                installation_id="installation:" + (conversation_label or label),
                owner_principal_id=str(_id(label, "principal")),
                principal_binding_id=str(binding),
                identity_sha256="b" * 64,
                token_sha256="c" * 64,
                household_id="test-home",
                consent_operation_id="household-consent:" + label,
                devices={SAT: DEVICE, "assist_satellite.office": "b" * 32},
                effective_at="2026-08-26T00:00:00Z",
                expires_at="2026-09-10T00:00:00Z",
            )
        )
    )
    budget = RuntimeResourceBudget(
        tenant_id=voice.tenant_id,
        installation_id=voice.installation_id,
        source_budget_sha256=canonical_sha256(
            _values()
            | {
                "max_input_tokens": 65536,
                "contract": "EvaInstallerBudget/v2",
                "unknown_cost_policy": "block_autonomous_spend",
            }
        ),
        **(_values() | {"max_input_tokens": 65536}),
    )
    research = Research()
    profile = HouseholdResearchProfile(MODEL_PROFILE_ID, "a" * 64)
    policy = HouseholdResearchPolicy.model_validate_json(
        json.dumps(
            dict(
                contract="HouseholdResearchPolicy/v1",
                schema_version=1,
                policy_id="research:" + label,
                state="enabled",
                tenant_id=voice.tenant_id,
                installation_id=voice.installation_id,
                household_id=voice.household_id,
                owner_principal_id=str(voice.owner_principal_id),
                principal_binding_id=str(binding),
                household_policy_id=voice.policy_id,
                household_policy_sha256=canonical_sha256(voice.model_dump(mode="json")),
                consent_operation_id=voice.consent_operation_id,
                devices=voice.devices,
                capability="research.public_sources",
                result_scope="originating_household_session",
                provider_configuration_sha256="a" * 64,
                resource_budget_sha256=canonical_sha256(budget.to_payload()),
                effective_at=voice.effective_at,
                expires_at=voice.expires_at,
            )
        )
    )
    policies = [policy]
    authority = HouseholdResearchAuthority(
        voice_authority=SimpleNamespace(
            policy=voice, token="test-token", validate_current=lambda: None
        ),
        policy_loader=lambda: policies[0],
        budget_loader=lambda: budget,
        provider_validator=lambda: None,
        profile=profile,
        clock=lambda: clock[0],
    )
    server = HouseholdWorkServer(engine, scope, authority)
    args = dict(token="test-token", satellite_id=SAT, device_id=DEVICE)
    binding = server.open_session(**args)
    service = server.session(**args, session_id=binding["session_id"])
    turn = service.turn(str(uuid4()), PUBLIC["question"])
    worker = DelegatedWorkWorker(
        server.store,
        DelegatedResearchRunner(
            server.store, research, profile, clock=lambda: clock[0]
        ),
        worker_id="worker:" + label,
        release=profile.release,
        clock=lambda: clock[0],
    )
    return SimpleNamespace(
        server=server,
        service=service,
        turn=turn,
        worker=worker,
        research=research,
        clock=clock,
        policies=policies,
        args=args,
    )


def start(x):
    return x.service.execute("research", x.turn, request_key="start", research=PUBLIC)[
        "work"
    ]["work_id"]


def test_end_to_end_retry_sources_and_single_delivery(delegated_engine):
    x = setup(delegated_engine, "hh-finish")
    work_id = start(x)
    x.clock[0] += timedelta(seconds=1)
    retry = x.service.turn(x.turn.message_id.rsplit(":", 1)[1], PUBLIC["question"])
    assert retry.occurred_at == x.turn.occurred_at
    assert (
        x.service.execute("research", retry, request_key="start", research=PUBLIC)[
            "created"
        ]
        is False
    )
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    view = x.service.execute("get", retry, request_key="get", work_id=work_id)["work"]
    assert "https://www.rfc-editor.org/rfc/rfc9110" in view["result"]
    assert view["usage"]["tool_calls"] == 1
    claim = x.service.poll(retry)
    assert claim["result"] == view["result"]
    assert x.service.result_delivery(retry, "claim", work_id=work_id) is None
    x.service.result_delivery(
        retry,
        "ack",
        work_id=work_id,
        result_sha256=claim["result_sha256"],
        disposition="interrupted",
    )
    assert x.service.result_delivery(retry, "claim", work_id=work_id) is None
    assert asyncio.run(x.worker.run_once()) == "idle"
    assert x.research.calls == 1


def test_private_entrypoints_and_other_sessions_cannot_read(delegated_engine):
    x = setup(delegated_engine, "hh-isolation")
    work_id = start(x)
    store = x.server.store
    with pytest.raises(DelegatedWorkNotFound):
        store.load(work_id, **x.service._owner)
    with pytest.raises(DelegatedWorkNotFound):
        store.request_stop(work_id, **x.service._owner, now=NOW)
    assert store.list_owned(**x.service._owner) == ()
    other = x.server.session(
        **x.args, session_id=x.server.open_session(**x.args)["session_id"]
    )
    turn = other.turn(str(uuid4()), "Status")
    with pytest.raises(DelegatedWorkNotFound):
        other.execute("get", turn, request_key="get", work_id=work_id)
    args = x.args | dict(satellite_id="assist_satellite.office", device_id="b" * 32)
    with pytest.raises(ValueError):
        x.server.session(**args, session_id=x.service.binding["session_id"])
    with pytest.raises(ValueError):
        x.server.open_session(**(x.args | {"token": "wrong"}))
    assert x.research.calls == 0


def test_stop_resume_is_same_origin_new_generation(delegated_engine):
    x = setup(delegated_engine, "hh-resume")
    work_id = start(x)
    assert (
        x.service.execute("stop", x.turn, request_key="stop", work_id=work_id)["work"][
            "state"
        ]
        == "stopped"
    )
    new = x.service.execute("resume", x.turn, request_key="resume", work_id=work_id)[
        "work"
    ]
    assert new["generation"] == 2 and new["work_id"] != work_id
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    assert x.research.calls == 1


def test_policy_revocation_blocks_claim_and_result(delegated_engine):
    x = setup(delegated_engine, "hh-revoke")
    work_id = start(x)
    x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
    assert asyncio.run(x.worker.run_once()) == "idle"
    with pytest.raises(ValueError):
        x.service.execute("get", x.turn, request_key="get", work_id=work_id)
    assert x.research.calls == 0


def test_session_close_preserves_background_but_disables_exposure(delegated_engine):
    x = setup(delegated_engine, "hh-close")
    work_id = start(x)
    x.service.current("close")
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    with pytest.raises(ValueError):
        x.service.execute("get", x.turn, request_key="get", work_id=work_id)
    with pytest.raises(ValueError):
        x.service.result_delivery(x.turn, "claim", work_id=work_id)
    assert x.research.calls == 1


def test_disabled_store_and_private_release_do_not_claim_household(delegated_engine):
    x = setup(delegated_engine, "hh-off")
    start(x)
    from eva.bootstrap.delegated_research import DelegatedResearchProfile

    legacy = DelegatedResearchProfile(MODEL_PROFILE_ID, "a" * 64)
    assert (
        x.server.store.claim_next(worker_id="old", release=legacy.release, now=NOW)
        is None
    )
    disabled = PostgresDelegatedWorkStore(x.server.store._engine, x.server.store.scope)
    assert (
        disabled.claim_next(
            worker_id="off", release=x.server.authority.profile.release, now=NOW
        )
        is None
    )
    assert x.research.calls == 0


def test_only_public_arguments_and_scoped_actions_are_accepted(delegated_engine):
    from eva.application.conversation import (
        ConversationActionCall,
        ConversationConflict,
    )
    from pydantic import ValidationError

    x = setup(delegated_engine, "hh-args")
    actions = HouseholdWorkActions(x.server.store._engine, x.service)
    assert set(actions.action_names) == {t.name for t in actions.conversation_tools()}
    for extra in (
        "owner_principal_id",
        "audience",
        "device_id",
        "consent_operation_id",
        "budget",
        "work_results",
    ):
        with pytest.raises(ValidationError):
            x.service.execute(
                "research",
                x.turn,
                request_key=extra,
                research=PUBLIC | {extra: "forged"},
            )
    for question in ("Mijn password: secret", "Lees /home/jaap/private.txt"):
        with pytest.raises((ValueError, ConversationConflict)):
            x.service.execute(
                "research",
                x.turn,
                request_key="private",
                research=PUBLIC | {"question": question},
            )
    for name in ("start_delegated_work", "list_delegated_work"):
        with pytest.raises(ConversationConflict):
            actions.execute(x.turn, ConversationActionCall(name, name, {}))
    work_id = start(x)
    saved = x.server.store.load(
        work_id, **x.service._owner, household_origin=x.service.origin(x.turn)
    )
    for field, value in (
        ("maximum_total_tokens", 100_000),
        ("maximum_claims_before_call_start", 3),
    ):
        raw = saved.spec.to_payload()
        raw["grant"][field] = value
        with pytest.raises(ValueError):
            x.server.authority.validate_spec(load_delegated_work_spec(raw))
    assert x.research.calls == 0


def test_atomic_claims_and_reauthorization_after_policy_rotation(delegated_engine):
    from concurrent.futures import ThreadPoolExecutor

    x = setup(delegated_engine, "hh-claim")
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda _: x.service.result_delivery(x.turn, "claim", work_id=work_id),
                range(2),
            )
        )
    claim = next(c for c in claims if c is not None)
    assert sum(c is not None for c in claims) == 1
    x.policies[0] = x.policies[0].model_copy(update={"policy_id": "rotated"})
    with pytest.raises(ValueError):
        x.service.result_delivery(
            x.turn, "authorize", work_id=work_id, result_sha256=claim["result_sha256"]
        )
    assert x.research.calls == 1


def test_foreground_and_explicit_stop_during_provider_call(delegated_engine):
    x = setup(delegated_engine, "hh-running")
    work_id = start(x)
    x.research.release.clear()

    async def run():
        pending = asyncio.create_task(x.worker.run_once())
        assert await asyncio.to_thread(x.research.started.wait, 5)
        second = x.service.turn(str(uuid4()), "Hoe staat het ermee?")
        assert (
            x.service.execute("get", second, request_key="get", work_id=work_id)[
                "work"
            ]["state"]
            == "call_started"
        )
        x.service.execute("stop", second, request_key="stop", work_id=work_id)
        x.research.release.set()
        assert await pending == "stopped"

    asyncio.run(run())
    assert x.service.poll(x.turn) is None
    assert x.research.calls == 1


def test_missing_origin_and_tampered_result_fail_closed(delegated_engine):
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs

    x = setup(delegated_engine, "hh-integrity")
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    with x.server.store._engine.begin() as c:
        c.execute(
            runs.update()
            .where(runs.c.work_id == work_id)
            .values(result_sha256=b"x" * 32)
        )
    with pytest.raises(DelegatedWorkStoreError):
        x.service.execute("get", x.turn, request_key="get", work_id=work_id)
    # A valid-shaped but unjournaled original message must never authorize work.
    y = setup(delegated_engine, "hh-origin")
    from dataclasses import replace

    forged = replace(
        y.turn, message_id=y.turn.conversation_id + ":request:" + str(uuid4())
    )
    origin = y.service.origin(forged)
    with y.server.store._engine.begin() as c:
        y.server.store._scope(c)
        from eva.adapters.postgres.household_work_sessions import verify_origin

        with pytest.raises(ValueError):
            verify_origin(c, y.server.store.scope, origin, require_open=False)


def test_retention_expiry_and_first_ingress_conflict(delegated_engine):
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    from eva.application.conversation import ConversationConflict

    x = setup(delegated_engine, "hh-retention")
    work_id = start(x)
    with pytest.raises(ConversationConflict):
        x.service.turn(x.turn.message_id.rsplit(":", 1)[1], "Ander verzoek")
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    with x.server.store._engine.begin() as c:
        c.execute(
            runs.update().where(runs.c.work_id == work_id).values(retention_until=NOW)
        )
    with pytest.raises(DelegatedWorkNotFound):
        x.service.execute("get", x.turn, request_key="get", work_id=work_id)
    assert x.service.poll(x.turn) is None


def test_private_task_id_cannot_be_read_or_resumed(delegated_engine):

    x = setup(delegated_engine, "hh-private")
    # The owner itself is not sufficient: a private work row with the SAME sponsor.
    from eva.bootstrap.delegated_research import DelegatedResearchProfile
    from tests.postgres.test_delegated_work_store import _admit

    spec = DelegatedResearchProfile(MODEL_PROFILE_ID, "a" * 64).build_spec(
        research=PUBLIC,
        work_id="work:private-same-sponsor",
        episode_id=str(uuid4()),
        generation=1,
        parent_task_id="task:private",
        owner_principal_id=str(x.service._owner["owner_principal_id"]),
        principal_binding_id=str(x.service._owner["principal_binding_id"]),
        task=PUBLIC["question"],
        created_at=NOW,
        deadline=NOW + timedelta(minutes=5),
    )
    _admit(x.server.store, spec)
    for operation in ("get", "stop", "resume"):
        with pytest.raises(DelegatedWorkNotFound):
            x.service.execute(
                operation, x.turn, request_key=operation, work_id=spec.work_id
            )
    assert x.research.calls == 0


def test_expired_session_and_receipt_replay_never_expose_results(delegated_engine):
    from eva.application.conversation import ConversationActionCall

    x = setup(delegated_engine, "hh-cache")
    actions = HouseholdWorkActions(x.server.store._engine, x.service)
    call = ConversationActionCall(
        "call:start", "start_background_research", {"research": PUBLIC}
    )
    receipt = actions.execute(x.turn, call)
    assert actions.execute(x.turn, call) == receipt
    x.clock[0] += timedelta(seconds=16)
    for operation in ("read", "renew"):
        with pytest.raises(ValueError):
            x.service.current(operation)
    with pytest.raises(ValueError):
        actions.pending(x.turn)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    assert x.research.calls == 1


def test_revocation_during_call_preserves_measured_usage_without_output(
    delegated_engine,
):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs

    x = setup(delegated_engine, "hh-active-revoke")
    work_id = start(x)
    x.research.release.clear()

    async def run():
        pending = asyncio.create_task(x.worker.run_once())
        assert await asyncio.to_thread(x.research.started.wait, 5)
        x.policies[0] = x.policies[0].model_copy(update={"state": "revoked"})
        x.research.release.set()
        assert await pending in {"failed", "ambiguous"}

    asyncio.run(run())
    with x.server.store._engine.connect() as c:
        row = (
            c.execute(sa.select(runs).where(runs.c.work_id == work_id)).mappings().one()
        )
        assert row["usage_payload"]["measurement"] == "measured"
        assert row["usage_payload"]["completed_model_responses"] == 1
        assert row["result_payload"] is None
    with pytest.raises(ValueError):
        x.service.poll(x.turn)
    assert x.research.calls == 1


def test_admission_origin_and_receipt_share_one_transaction(
    delegated_engine, monkeypatch
):
    import sqlalchemy as sa
    from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
    import eva.adapters.postgres.delegated_work as module

    x = setup(delegated_engine, "hh-atomic")

    def crash(*args, **kwargs):
        raise RuntimeError("simulated admission failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, "record_admission_destination", crash)
        with pytest.raises(RuntimeError, match="simulated"):
            start(x)
    with x.server.store._engine.connect() as c:
        assert (
            c.scalar(
                sa.select(sa.func.count())
                .select_from(runs)
                .where(runs.c.tenant_id == x.server.store.scope.tenant_id)
            )
            == 0
        )
    work_id = start(x)
    saved = x.server.store.load(
        work_id, **x.service._owner, household_origin=x.service.origin(x.turn)
    )
    assert load_delegated_work_spec(saved.spec.to_payload()) == saved.spec
    assert saved.spec.origin.message_id == x.turn.message_id
    assert x.research.calls == 0


def test_exact_followed_work_remains_accessible_after_twenty_new_tasks(
    delegated_engine,
):
    x = setup(delegated_engine, "hh-follow")
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    for index in range(21):
        turn = x.service.turn(str(uuid4()), PUBLIC["question"])
        x.service.execute("research", turn, request_key=str(index), research=PUBLIC)
    assert x.service.execute("get", x.turn, request_key="get", work_id=work_id)["work"][
        "result"
    ]
    assert x.service.poll(x.turn)["work_id"] == work_id
    assert x.research.calls == 1


def test_http_control_polling_never_creates_user_ingress(delegated_engine):
    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.household_work import install_household_work
    from eva.adapters.postgres.operational_timeline import operational_events
    x = setup(delegated_engine, "hh-http-control")
    app = FastAPI()
    install_household_work(app, admit=lambda request, payload: None, server=x.server)
    client = TestClient(app)
    prefix = "/internal/v1/ha-voice/session/work/"
    headers = {"Authorization": "Bearer test-token"}
    device = {"satellite_id": SAT, "device_id": DEVICE}
    def count():
        with delegated_engine.begin() as c:
            x.server.store._scope(c)
            return c.scalar(sa.select(sa.func.count()).select_from(operational_events).where(
                operational_events.c.tenant_id == x.server.store.scope.tenant_id,
                operational_events.c.event_type == "INGRESS_COMMITTED"))
    def post(op, data):
        return client.post(prefix + op, headers=headers, json=data)
    before = count()
    opened = post("open", device)
    assert opened.status_code == 200
    empty = {**device, "session_id": opened.json()["session_id"]}
    for _ in range(3):
        assert post("poll", empty).json()["state"] == "idle"
    assert count() == before
    data = {**device, "session_id": x.service.binding["session_id"]}
    assert post("poll", data).json()["state"] == "idle"
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    before = count()
    assert post("poll", empty).json()["state"] == "idle"
    claim = post("poll", data).json()["result"]
    assert claim["work_id"] == work_id and "https://" in claim["result"]
    identity = {**data, "work_id": work_id, "result_sha256": claim["result_sha256"]}
    assert post("authorize", identity).json()["state"] == "authorize"
    assert post("ack", {**identity, "disposition": "completed"}).status_code == 200
    assert post("poll", data).json()["state"] == "idle"
    assert count() == before
    assert post("close", data).json()["state"] == "closed"
    assert post("close", data).json()["state"] == "closed"
    assert post("renew", data).status_code == 403
    assert post("poll", data).status_code == 403
    assert count() == before


def test_http_rejects_scope_spoofing_and_revoked_result(delegated_engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.household_work import install_household_work
    x = setup(delegated_engine, "hh-http-authority")
    app = FastAPI()
    install_household_work(app, admit=lambda request, payload: None, server=x.server)
    client = TestClient(app)
    prefix = "/internal/v1/ha-voice/session/work/"
    headers = {"Authorization": "Bearer test-token"}
    device = {"satellite_id": SAT, "device_id": DEVICE}
    data = {**device, "session_id": x.service.binding["session_id"]}
    def post(op, body): return client.post(prefix + op, headers=headers, json=body)
    for key in ("principal_id", "audience", "conversation_id", "origin"):
        assert post("poll", {**data, key: "private-owner"}).status_code == 422
    assert post("open", data).status_code == 422
    assert post("poll", device).status_code == 422
    assert post("poll", {**data, "work_id": "chosen"}).status_code == 422
    assert post("authorize", data).status_code == 422
    assert post("unknown", data).status_code == 422
    assert client.post(prefix+"poll?principal_id=owner", headers=headers, json=data).status_code == 422
    assert post("poll", {**data, "satellite_id": "assist_satellite.office", "device_id": "b"*32}).status_code == 403
    assert client.post(prefix+"poll", headers={"Authorization": "Bearer wrong"}, json=data).status_code == 403
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    claim = post("poll", data).json()["result"]
    x.policies[0] = x.policies[0].model_copy(update={"state": "disabled"})
    result = post("authorize", {**data, "work_id": work_id, "result_sha256": claim["result_sha256"]})
    assert result.status_code == 403
    assert claim["result"] not in result.text


def test_distinct_household_task_titles_survive_actions_replay_and_resume(delegated_engine):
    from eva.application.conversation import ConversationActionCall
    x = setup(delegated_engine, "hh-distinct-titles")
    actions = HouseholdWorkActions(delegated_engine, x.service)
    first_request = PUBLIC | {"title": "First public topic", "question": "Explain HTTP status codes"}
    second_request = PUBLIC | {"title": "Second public topic", "question": "Explain HTTP cache headers"}
    first_call = ConversationActionCall("first", "start_background_research", {"research": first_request})
    first = actions.execute(x.turn, first_call).output["operation_result"]["work"]
    second_turn = x.service.turn(str(uuid4()), second_request["question"])
    second_call = ConversationActionCall("second", "start_background_research", {"research": second_request})
    second = actions.execute(second_turn, second_call).output["operation_result"]["work"]
    assert (first["title"], first["question"]) == (first_request["title"], first_request["question"])
    assert (second["title"], second["question"]) == (second_request["title"], second_request["question"])
    assert first["work_id"] != second["work_id"]
    assert actions.execute(x.turn, first_call).output["operation_result"]["work"] == first
    for turn, work in ((x.turn, first), (second_turn, second)):
        saved = x.server.store.load(work["work_id"], **x.service._owner,
                                    household_origin=x.service.origin(turn))
        assert saved.spec.origin.message_id == turn.message_id
    x.service.execute("stop", second_turn, request_key="stop-first", work_id=first["work_id"])
    resumed = x.service.execute("resume", second_turn, request_key="resume-first", work_id=first["work_id"])["work"]
    assert resumed["title"] == first["title"] and resumed["question"] == first["question"]
    saved = x.server.store.load(resumed["work_id"], **x.service._owner,
                                household_origin=x.service.origin(x.turn))
    assert saved.spec.origin.message_id == x.turn.message_id
    assert x.service.execute("get", second_turn, request_key="second-state", work_id=second["work_id"])["work"]["state"] == "admitted"
    assert x.research.calls == 0


def test_household_replay_preserves_legacy_title(delegated_engine, monkeypatch):
    x = setup(delegated_engine, "hh-legacy-title")
    admit = x.server.store.admit

    def legacy_admit(**kwargs):
        return admit(**(kwargs | {"title": "Huishoudelijk bronnenonderzoek"}))

    monkeypatch.setattr(x.server.store, "admit", legacy_admit)
    work_id = start(x)
    monkeypatch.setattr(x.server.store, "admit", admit)
    replay = x.service.execute("research", x.turn, request_key="start", research=PUBLIC)
    assert replay["created"] is False
    assert replay["work"]["work_id"] == work_id
    assert replay["work"]["title"] == "Huishoudelijk bronnenonderzoek"
