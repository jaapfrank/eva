from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterator
from uuid import UUID, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

import test_action_chain_effects as effect_fixtures
import test_action_chain_read_dispatcher as read_dispatcher_fixtures
from test_action_chain_effect_authorizer import _prepared_effect
from test_action_chain_ledger import (
    NOW,
    POLICY_SHA256,
    RETENTION_UNTIL,
    _decision_and_plan as _home_decision_and_plan,
    _fact_values,
)
from test_action_chain_read_authorizer import _verified_registry_release

from tests.support.postgres_action_chain_effect_advances import (
    PostgresActionChainEffectAdvancer,
)
from eva.adapters.postgres.action_chain_effect_cancellations import (
    PostgresActionChainEffectCanceller,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from tests.support.postgres_action_chain_effect_readback_results import (
    PostgresActionChainEffectReadbackResultStore,
)
from eva.adapters.postgres.action_chain_effect_readbacks import (
    EffectReadbackAuthority,
    PostgresActionChainEffectReadbackPreparer,
)
from eva.adapters.postgres.action_chain_effect_readback_validation import (
    effect_readback_thread_id,
)
from eva.adapters.postgres.action_chain_ledger import PostgresActionChainLedger
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import ActionToolCatalog, ActionToolDescriptor
from eva.application.effect_verification import (
    EffectVerificationField,
    EffectVerificationProjection,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.execution import (
    CapabilityManifest,
    ConnectorBinding,
    ResourceScope,
)
from eva.operations.action_chain_effect_executor import (
    PostgresActionChainEffectExecutor,
)
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_effect_readback_executor import (
    PostgresActionChainEffectReadbackExecutor,
)
from eva.operations.action_chain_provider_registry import (
    SealedActionProviderRegistry,
    SealedReadProviderRegistry,
)


ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = ROOT / "development/tests/fixtures/browser/worker-profile.json"
WORKER_PATH = ROOT / "development/tests/fixtures/browser/playwright_mcp_worker.py"
EFFECT_INPUT_SHA256 = "74cf02212aa6a14edc59279827695ccbc7ce87fe4622fde034d69676e8785db0"
EFFECT_OUTPUT_SHA256 = (
    "6a8f6d037c157dc18a7bc44608f0b79ccb574e4099c3804a7b232d25ae62c691"
)
READBACK_INPUT_SHA256 = (
    "7300325716eea0c03f68384fe15bcf91aad459bb93a5b3f68b3cb8ae2b955cb3"
)
READBACK_OUTPUT_SHA256 = (
    "c332825e9e133dcea610e33ea881587bad6f2248ab40e2dcd2abb47415997fa9"
)
SANDBOX_SHA256 = "f3e99ea7a5bc6691771f2d0b4f50d41fa838d47ed7c35c3f8d98959bd453dd9c"
RESOURCE_ID = "browser_fixture.eva_action_a02"
WORKER_SHA256 = "85252cf1a110c4bc9b9b516798d325e17ca1aff3dc2387a9305251a643e1044b"
_ORIGINAL_EFFECT_AUTHORITY = effect_fixtures._effect_authority


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from effect_fixtures.action_chain_engine.__wrapped__()


def _browser_decision_and_plan(*, scenario: str = "normal"):
    uncertainty_by_scenario = {"normal": 200, "before": 201, "after": 202}
    if scenario not in uncertainty_by_scenario:
        raise ValueError("browser test scenario is invalid")
    decision, plan, original = _home_decision_and_plan()
    decision_payload = decision.model_dump(mode="json")
    decision_payload["actions"][1].update(
        {
            "operation": "browser.fixture.click",
            "resource": RESOURCE_ID,
            "arguments": {"state": "committed", "target": RESOURCE_ID},
            "uncertainty_basis_points": uncertainty_by_scenario[scenario],
        }
    )
    decision = decision.__class__.model_validate(decision_payload)

    def validate_effect(resource: str, arguments: dict[str, object]) -> None:
        assert resource == RESOURCE_ID
        assert arguments == {"state": "committed", "target": RESOURCE_ID}

    catalog = ActionToolCatalog(
        (
            original.require("ha.state.read"),
            ActionToolDescriptor(
                operation="browser.fixture.click",
                effect_class="effect",
                permission_class="browser_fixture_effect",
                input_schema_sha256=EFFECT_INPUT_SHA256,
                output_schema_sha256=EFFECT_OUTPUT_SHA256,
                validator_sha256="1" * 64,
                policy_scope_sha256=POLICY_SHA256,
                proof_floor="provider receipt plus independent server readback",
                enabled=True,
                validate_request=validate_effect,
                effect_verification=EffectVerificationProjection(
                    (
                        EffectVerificationField(
                            name="state",
                            expected_source="arguments",
                            expected_pointer="/state",
                            observed_pointer="/state",
                        ),
                        EffectVerificationField(
                            name="target",
                            expected_source="arguments",
                            expected_pointer="/target",
                            observed_pointer="/target",
                        ),
                    )
                ),
            ),
        )
    )
    plan_payload = plan.model_dump(mode="json")
    plan_payload["model_decision_sha256"] = canonical_sha256(decision)
    plan_payload["steps"][0]["capability_manifest_id"] = (
        catalog.plan_capability_manifest_id("ha.state.read")
    )
    plan_payload["steps"][1].update(
        {
            "operation": "browser.fixture.click",
            "resource": RESOURCE_ID,
            "input_schema_sha256": EFFECT_INPUT_SHA256,
            "output_schema_sha256": EFFECT_OUTPUT_SHA256,
            "capability_manifest_id": catalog.plan_capability_manifest_id(
                "browser.fixture.click"
            ),
            "proof_floor": "provider receipt plus independent server readback",
        }
    )
    return decision, plan.__class__.model_validate(plan_payload), catalog


def _browser_authority(scope) -> effect_fixtures.EffectStepAuthority:
    resource = ResourceScope(
        resource_type="browser-fixture-action",
        resource_id=RESOURCE_ID,
        scope_sha256=canonical_sha256(
            {"resource_type": "browser-fixture-action", "resource_id": RESOURCE_ID}
        ),
    )
    original = _ORIGINAL_EFFECT_AUTHORITY(scope)
    return effect_fixtures.EffectStepAuthority(
        capability_activation_id=original.capability_activation_id,
        connector_binding_id=original.connector_binding_id,
        resource_scope=resource,
    )


def _seed_browser_effect_authority(engine: Engine, scope):
    original_seed = _seed_browser_effect_authority.original
    old = original_seed(engine, scope)
    authority = _browser_authority(scope)
    bindings = metadata.tables["eva_core.connector_bindings"]
    manifests = metadata.tables["eva_core.capability_manifests"]
    activations = metadata.tables["eva_core.capability_activations"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        binding_row = connection.execute(
            sa.select(bindings).where(bindings.c.binding_id == old.connector_binding_id)
        ).one()
        binding = ConnectorBinding.model_validate(binding_row.payload).model_copy(
            update={
                "provider_id": "playwright-mcp",
                "provider_account_id": "fixture-playwright-a02",
                "provider_resource_id": RESOURCE_ID,
                "confirmed_operations": [
                    "browser.fixture.click",
                    "effect.readback",
                ],
                "confirmed_scopes": [
                    "browser-fixture-action",
                    "browser_fixture_effect",
                    "browser_fixture_read",
                ],
                "credential_reference": "secret-descriptor:none:browser-a02",
            }
        )
        connection.execute(
            sa.update(bindings)
            .where(bindings.c.binding_id == old.connector_binding_id)
            .values(
                provider_id=binding.provider_id,
                provider_account_id=binding.provider_account_id,
                provider_resource_id=binding.provider_resource_id,
                confirmed_operations=binding.confirmed_operations,
                confirmed_scopes=binding.confirmed_scopes,
                credential_reference=binding.credential_reference,
                payload=binding.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(binding)),
            )
        )
        activation_row = connection.execute(
            sa.select(activations).where(
                activations.c.activation_id == old.capability_activation_id
            )
        ).one()
        manifest_row = connection.execute(
            sa.select(manifests).where(
                manifests.c.capability_manifest_id
                == activation_row.capability_manifest_id
            )
        ).one()
        manifest = CapabilityManifest.model_validate(manifest_row.payload).model_copy(
            update={
                "capability_id": "capability:browser-fixture-click:test",
                "connector_id": "playwright-mcp",
                "operation": "browser.fixture.click",
                "input_schema_sha256": EFFECT_INPUT_SHA256,
                "output_schema_sha256": EFFECT_OUTPUT_SHA256,
                "required_provider_scopes": ["browser_fixture_effect"],
                "proof_floor": "provider receipt plus independent server readback",
                "executable_sha256": WORKER_SHA256,
                "sandbox_profile_sha256": SANDBOX_SHA256,
            }
        )
        connection.execute(
            sa.update(manifests)
            .where(
                manifests.c.capability_manifest_id
                == activation_row.capability_manifest_id
            )
            .values(
                capability_id=manifest.capability_id,
                connector_id=manifest.connector_id,
                operation=manifest.operation,
                input_schema_sha256=bytes.fromhex(manifest.input_schema_sha256),
                output_schema_sha256=bytes.fromhex(manifest.output_schema_sha256),
                required_provider_scopes=manifest.required_provider_scopes,
                proof_floor=manifest.proof_floor,
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
                payload=manifest.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(manifest)),
            )
        )
        connection.execute(
            sa.update(activations)
            .where(activations.c.activation_id == old.capability_activation_id)
            .values(
                capability_id=manifest.capability_id,
                operation=manifest.operation,
                scope=authority.resource_scope.model_dump(mode="json"),
                scope_sha256=bytes.fromhex(authority.resource_scope.scope_sha256),
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
            )
        )
    return authority


_seed_browser_effect_authority.original = effect_fixtures._seed_effect_authority  # type: ignore[attr-defined]


def _seed_browser_readback_authority(
    engine: Engine, scope, effect_authority
) -> EffectReadbackAuthority:
    activation_id = uuid5(scope.installation_id, "browser-a02:readback:activation")
    manifest_id = uuid5(scope.installation_id, "browser-a02:readback:manifest")
    manifest = CapabilityManifest(
        schema_version=1,
        contract="CapabilityManifest/v1",
        capability_id="capability:browser-fixture-readback:test",
        connector_id="playwright-mcp",
        operation="effect.readback",
        input_schema_sha256=READBACK_INPUT_SHA256,
        output_schema_sha256=READBACK_OUTPUT_SHA256,
        effect_class="verification_read",
        required_provider_scopes=["browser_fixture_read"],
        proof_floor="authoritative fixture server state matches intended effect",
        maximum_freshness_ms=0,
        executable_sha256=WORKER_SHA256,
        sandbox_profile_sha256=SANDBOX_SHA256,
    )
    policy = _fact_values("static/policy-pack")[1]
    manifests = metadata.tables["eva_core.capability_manifests"]
    activations = metadata.tables["eva_core.capability_activations"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(
            sa.insert(manifests).values(
                tenant_id=scope.tenant_id,
                installation_id=scope.installation_id,
                capability_manifest_id=manifest_id,
                capability_id=manifest.capability_id,
                connector_id=manifest.connector_id,
                operation=manifest.operation,
                input_schema_sha256=bytes.fromhex(manifest.input_schema_sha256),
                output_schema_sha256=bytes.fromhex(manifest.output_schema_sha256),
                effect_class=manifest.effect_class,
                required_provider_scopes=manifest.required_provider_scopes,
                proof_floor=manifest.proof_floor,
                maximum_freshness_ms=0,
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
                release_signature_sha256=bytes.fromhex("5" * 64),
                state="active",
                payload=manifest.model_dump(mode="json"),
                payload_sha256=bytes.fromhex(canonical_sha256(manifest)),
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                created_at=NOW,
            )
        )
        connection.execute(
            sa.insert(activations).values(
                tenant_id=scope.tenant_id,
                installation_id=scope.installation_id,
                activation_id=activation_id,
                capability_manifest_id=manifest_id,
                connector_binding_id=effect_authority.connector_binding_id,
                capability_id=manifest.capability_id,
                operation=manifest.operation,
                scope=effect_authority.resource_scope.model_dump(mode="json"),
                scope_sha256=bytes.fromhex(
                    effect_authority.resource_scope.scope_sha256
                ),
                policy_id=policy["policy_id"],
                policy_pack_version=policy["policy_version"],
                policy_pack_sha256=bytes.fromhex(POLICY_SHA256),
                executable_sha256=bytes.fromhex(manifest.executable_sha256),
                sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
                state="active",
                revision=1,
                retention_policy_id="retention:noor-document:30d",
                retention_until=RETENTION_UNTIL,
                activated_at=NOW,
                updated_at=NOW,
            )
        )
    return EffectReadbackAuthority(
        capability_activation_id=activation_id,
        connector_binding_id=effect_authority.connector_binding_id,
        resource_scope=effect_authority.resource_scope,
    )


def _registry(
    engine: Engine,
    scope,
    *,
    activation_id: UUID,
    operation: str,
    profile_path: Path = PROFILE_PATH,
) -> SealedActionProviderRegistry | SealedReadProviderRegistry:
    profile_payload = json.loads(profile_path.read_bytes())
    worker_bytes = WORKER_PATH.read_bytes()
    assert hashlib.sha256(worker_bytes).hexdigest() == WORKER_SHA256
    assert (
        profile_payload["connectorManifests"][operation]["executable_sha256"]
        == WORKER_SHA256
    )
    activations = metadata.tables["eva_core.capability_activations"]
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        activation = connection.execute(
            sa.select(activations).where(activations.c.activation_id == activation_id)
        ).one()
    executable = Path(sys.executable).resolve()
    command = [
        str(executable),
        "-I",
        "-c",
        worker_bytes.decode("utf-8"),
        str(profile_path.resolve()),
    ]
    artifact = canonical_json_bytes(
        {
            "schema_version": 1,
            "contract": "ReadProviderRegistryRelease/v1",
            "release_id": f"fixture:browser-a02:{operation}",
            "profiles": [
                {
                    "capability_manifest_id": str(activation.capability_manifest_id),
                    "revision": 1,
                    "connector_manifest": profile_payload["connectorManifests"][
                        operation
                    ],
                    "process_spec": {
                        "command": command,
                        "command_targets": [
                            {
                                "argument_index": 0,
                                "target_path": str(executable),
                                "target_sha256": hashlib.sha256(
                                    executable.read_bytes()
                                ).hexdigest(),
                            },
                            {
                                "argument_index": 4,
                                "target_path": str(profile_path.resolve()),
                                "target_sha256": hashlib.sha256(
                                    profile_path.read_bytes()
                                ).hexdigest(),
                            },
                        ],
                        "program_base64": base64.b64encode(worker_bytes).decode(
                            "ascii"
                        ),
                        "program_binding": {
                            "kind": "python_inline_code",
                            "argument_index": 3,
                        },
                        "invoke_failure_phase": "invoke_external_io_possible",
                        "fixture_selection": None,
                    },
                }
            ],
        }
    )
    provenance, verification = _verified_registry_release(
        release_id=f"fixture:browser-a02:{operation}",
        registry_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    registry_type = (
        SealedActionProviderRegistry
        if operation == "browser.fixture.click"
        else SealedReadProviderRegistry
    )
    return registry_type.from_release_artifact(
        artifact,
        provenance_bytes=provenance,
        release_verification=verification,
    )


def _oracle(effect_key: str) -> dict[str, object]:
    from urllib import parse as urllib_parse
    from urllib import request as urllib_request

    url = "http://fixture.eva.test:8080/oracle?" + urllib_parse.urlencode(
        {"effect_key": effect_key}
    )
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        return json.loads(response.read())


def _owner() -> tuple[UUID, UUID]:
    principal = _fact_values("static/principal-owner")[1]
    binding = _fact_values("static/principal-binding")[1]
    return principal["principal_id"], binding["principal_binding_id"]


def test_owner_stop_after_browser_authorization_prevents_the_real_click(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sealed Playwright worker must receive no call bytes after a stop."""

    monkeypatch.setattr(
        read_dispatcher_fixtures,
        "_decision_and_plan",
        _browser_decision_and_plan,
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_effect_authority",
        _browser_authority,
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_seed_effect_authority",
        _seed_browser_effect_authority,
    )
    scope, chain, catalog, effect_authority, prepared = _prepared_effect(
        action_chain_engine
    )
    effect_id = UUID(prepared.intent.effect_intent_id)
    registry = _registry(
        action_chain_engine,
        scope,
        activation_id=effect_authority.capability_activation_id,
        operation="browser.fixture.click",
    )
    authorized = PostgresActionChainEffectAuthorizer(
        action_chain_engine, scope, provider_registry=registry
    ).authorize_effect(
        effect_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    owner_principal_id, principal_binding_id = _owner()
    cancelled = PostgresActionChainEffectCanceller(
        action_chain_engine, scope
    ).cancel_pre_dispatch(
        effect_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )
    assert authorized.disposition == "authorized"
    assert cancelled.disposition == "cancelled"
    assert cancelled.intent.state == "cancelled"

    with pytest.raises(
        ActionChainEffectConflict, match="prepared effect step is no longer running"
    ):
        PostgresActionChainEffectExecutor(
            action_chain_engine,
            scope,
            provider_registry=registry,
            clock=lambda: NOW + timedelta(seconds=11),
        ).execute(
            effect_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
            timeout_seconds=10,
        )
    assert (
        PostgresActionChainLedger(action_chain_engine, scope)
        .load(chain.plan_id)
        .steps[1]
        .state
        == "cancelled"
    )
    oracle = _oracle(prepared.intent.idempotency_key)
    assert oracle["post_requests"] == oracle["transitions"] == 0
    with action_chain_engine.connect() as connection:
        call_starts = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.event_type
                    == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
                )
            )
        )
    assert call_starts == 0


def test_real_browser_click_crosses_durable_ledger_and_server_readback(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_dispatcher_fixtures,
        "_decision_and_plan",
        _browser_decision_and_plan,
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_effect_authority",
        _browser_authority,
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_seed_effect_authority",
        _seed_browser_effect_authority,
    )
    scope, chain, catalog, effect_authority, prepared = _prepared_effect(
        action_chain_engine
    )
    effect_id = UUID(prepared.intent.effect_intent_id)
    effect_registry = _registry(
        action_chain_engine,
        scope,
        activation_id=effect_authority.capability_activation_id,
        operation="browser.fixture.click",
    )
    executed = PostgresActionChainEffectExecutor(
        action_chain_engine,
        scope,
        provider_registry=effect_registry,
        clock=lambda: NOW + timedelta(seconds=11),
    ).execute(
        effect_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
        timeout_seconds=10,
    )
    assert executed.stored_result is not None
    assert executed.stored_result.intent.state == "provider_accepted"
    oracle = _oracle(prepared.intent.idempotency_key)
    assert oracle["post_requests"] == oracle["transitions"] == 1
    assert oracle["event"]["request_sha256"] == prepared.intent.frozen_payload_sha256

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectExecutor(
            restarted,
            scope,
            provider_registry=effect_registry,
            clock=lambda: NOW + timedelta(seconds=12),
        ).execute(
            effect_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=12),
            timeout_seconds=10,
        )
    finally:
        restarted.dispose()
    assert replay.disposition == "already_started"
    assert _oracle(prepared.intent.idempotency_key)["post_requests"] == 1

    readback_authority = _seed_browser_readback_authority(
        action_chain_engine, scope, effect_authority
    )
    prepared_readback = PostgresActionChainEffectReadbackPreparer(
        action_chain_engine, scope
    ).prepare(
        effect_id,
        authority=readback_authority,
        now=NOW + timedelta(seconds=12),
    )
    readback_registry = _registry(
        action_chain_engine,
        scope,
        activation_id=readback_authority.capability_activation_id,
        operation="effect.readback",
    )
    readback_id = UUID(prepared_readback.intent.read_intent_id)
    observed = PostgresActionChainEffectReadbackExecutor(
        action_chain_engine,
        scope,
        provider_registry=readback_registry,
        clock=lambda: NOW + timedelta(seconds=16),
    ).execute(
        readback_id,
        now=NOW + timedelta(seconds=14),
        timeout_seconds=10,
    )
    assert observed.stored_result is not None
    assert observed.stored_result.attempt.state == "observed"
    stored_readback = PostgresActionChainEffectReadbackResultStore(
        action_chain_engine, scope
    ).record(readback_id)
    assert stored_readback.readback.match_status == "matches"
    advanced = PostgresActionChainEffectAdvancer(action_chain_engine, scope).advance(
        effect_id, now=NOW + timedelta(seconds=17)
    )
    assert advanced.disposition == "advanced"
    loaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert loaded.steps[1].state == "succeeded"

    with action_chain_engine.connect() as connection:
        event_types = connection.scalars(
            sa.select(operational_events.c.event_type).where(
                operational_events.c.event_type.in_(
                    [
                        "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED",
                        "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                        "STATE_VERIFIED",
                    ]
                )
            )
        ).all()
        readback_call_starts = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.thread_id
                    == effect_readback_thread_id(readback_id),
                    operational_events.c.event_type
                    == "ACTION_CHAIN_READ_PROVIDER_CALL_STARTED",
                )
            )
        )
    assert event_types.count("ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED") == 1
    assert readback_call_starts == 1
    assert event_types.count("STATE_VERIFIED") == 1
    final_oracle = _oracle(prepared.intent.idempotency_key)
    assert final_oracle["post_requests"] == final_oracle["transitions"] == 1


@pytest.mark.parametrize(
    ("scenario", "failure_phase", "expected_posts", "expected_state"),
    [
        ("before", "before_effect_io", 0, "not_committed"),
        ("after", "after_effect_io", 1, "committed"),
    ],
)
def test_browser_click_crash_never_starts_a_second_provider_call(
    action_chain_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    failure_phase: str,
    expected_posts: int,
    expected_state: str,
) -> None:
    monkeypatch.setattr(
        read_dispatcher_fixtures,
        "_decision_and_plan",
        lambda: _browser_decision_and_plan(scenario=scenario),
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_effect_authority",
        _browser_authority,
    )
    monkeypatch.setattr(
        effect_fixtures,
        "_seed_effect_authority",
        _seed_browser_effect_authority,
    )
    profile_payload = json.loads(PROFILE_PATH.read_bytes())
    profile_payload["testFailurePhase"] = failure_phase
    failure_profile = tmp_path / "worker-profile.json"
    failure_profile.write_bytes(canonical_json_bytes(profile_payload))

    scope, _chain, catalog, effect_authority, prepared = _prepared_effect(
        action_chain_engine
    )
    effect_id = UUID(prepared.intent.effect_intent_id)
    registry = _registry(
        action_chain_engine,
        scope,
        activation_id=effect_authority.capability_activation_id,
        operation="browser.fixture.click",
        profile_path=failure_profile,
    )
    executed = PostgresActionChainEffectExecutor(
        action_chain_engine,
        scope,
        provider_registry=registry,
        clock=lambda: NOW + timedelta(seconds=11),
    ).execute(
        effect_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
        timeout_seconds=10,
    )
    assert executed.stored_result is not None
    assert executed.stored_result.intent.state == "ambiguous"
    assert executed.stored_result.attempt.state == "ambiguous_transport"
    first_oracle = _oracle(prepared.intent.idempotency_key)
    assert first_oracle["post_requests"] == expected_posts
    assert first_oracle["transitions"] == expected_posts
    assert first_oracle["state"] == expected_state

    restarted = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        replay = PostgresActionChainEffectExecutor(
            restarted,
            scope,
            provider_registry=registry,
            clock=lambda: NOW + timedelta(seconds=12),
        ).execute(
            effect_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=12),
            timeout_seconds=10,
        )
    finally:
        restarted.dispose()
    assert replay.disposition == "already_started"
    assert _oracle(prepared.intent.idempotency_key)["post_requests"] == expected_posts

    with action_chain_engine.connect() as connection:
        call_starts = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.event_type
                    == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
                )
            )
        )
        state_verified = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(operational_events.c.event_type == "STATE_VERIFIED")
            )
        )
    assert call_starts == 1
    assert state_verified == 0
