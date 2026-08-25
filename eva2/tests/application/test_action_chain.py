from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from eva.application.action_chain import (
    ActionChainValidationError,
    ActionToolCatalog,
    ActionToolDescriptor,
    action_chain_step_operation_id,
    action_chain_thread_id,
    validate_model_decision,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import PolicyPack
from eva.contracts.planning import (
    ModelDecision,
    PlanModelInput,
    PresenceContext,
    ProposedAction,
)


H = "a" * 64
DEADLINE = "2026-08-25T14:00:00Z"
OPERATIONS = ("ha.history.read", "ha.state.read", "ha.test_target.set")


def _exact(
    resource: str, *keys: str
) -> Callable[[str, dict[str, JsonValue]], None]:
    def validate(candidate_resource: str, arguments: dict[str, JsonValue]) -> None:
        if candidate_resource != resource:
            raise ValueError("resource differs")
        if set(arguments) != set(keys) or not all(
            isinstance(arguments[key], str) for key in keys
        ):
            raise ValueError("arguments differ")

    return validate


def _policy(*, maximum_connector_calls: int = 8) -> PolicyPack:
    return PolicyPack(
        schema_version=1,
        contract="PolicyPack/v1",
        policy_id="policy:eva2:mc01",
        policy_version=1,
        effect_policies=[
            {
                "operation": "ha.history.read",
                "effect_class": "read",
                "consent_exempt": True,
                "proof_floor": "fresh provider observation",
                "maximum_attempts": 1,
            },
            {
                "operation": "ha.state.read",
                "effect_class": "read",
                "consent_exempt": True,
                "proof_floor": "fresh provider observation",
                "maximum_attempts": 1,
            },
            {
                "operation": "ha.test_target.set",
                "effect_class": "effect",
                "consent_exempt": False,
                "proof_floor": "provider receipt plus independent readback",
                "maximum_attempts": 2,
            },
        ],
        budgets={
            "maximum_model_attempts": 2,
            "maximum_connector_calls": maximum_connector_calls,
            "maximum_visible_deliveries": 1,
            "maximum_wall_clock_ms": 60_000,
            "maximum_input_tokens": 4_000,
            "maximum_output_tokens": 1_000,
            "maximum_provider_cost_euro_cents": 10,
        },
        retry_policy_id="retry:eva2:mc01",
        retention_policy_id="retention:eva2:mc01",
        privacy_policy_id="privacy:eva2:mc01",
        source_sha256="b" * 64,
        release_signature_sha256="c" * 64,
        effective_at="2026-08-25T12:00:00Z",
    )


def _descriptor(
    policy: PolicyPack,
    operation: str,
    effect_class: str,
    resource: str,
    *keys: str,
    enabled: bool = True,
    proof_floor: str | None = None,
) -> ActionToolDescriptor:
    policy_entry = next(
        item for item in policy.effect_policies if item.operation == operation
    )
    return ActionToolDescriptor(
        operation=operation,
        effect_class=effect_class,  # type: ignore[arg-type]
        permission_class=f"home_{effect_class}",
        input_schema_sha256=H,
        output_schema_sha256="d" * 64,
        validator_sha256="e" * 64,
        policy_scope_sha256=canonical_sha256(policy),
        proof_floor=proof_floor or policy_entry.proof_floor,
        enabled=enabled,
        validate_request=_exact(resource, *keys),
    )


def _catalog(
    policy: PolicyPack, *, disabled_effect: bool = False
) -> ActionToolCatalog:
    return ActionToolCatalog(
        (
            _descriptor(
                policy,
                "ha.state.read",
                "read",
                "overkapping",
                "target",
            ),
            _descriptor(
                policy,
                "ha.history.read",
                "read",
                "overkapping",
                "period",
                "target",
            ),
            _descriptor(
                policy,
                "ha.test_target.set",
                "effect",
                "input_boolean.eva_test",
                "state",
                "target",
                enabled=not disabled_effect,
            ),
        )
    )


def _plan_input(
    policy: PolicyPack,
    *,
    allowed_operations: tuple[str, ...] = OPERATIONS,
    maximum_steps: int = 6,
    episode_id: str = "episode:telegram:5001",
    generation: int = 1,
) -> PlanModelInput:
    presence = PresenceContext.model_construct(
        episode_id=episode_id,
        generation=generation,
    )
    return PlanModelInput.model_construct(
        schema_version=1,
        contract="PlanModelInput/v1",
        purpose="plan",
        output_contract="ModelDecision/v1",
        presence_context=presence,
        policy_pack_sha256=canonical_sha256(policy),
        allowed_operations=list(allowed_operations),
        maximum_steps=maximum_steps,
    )


def _decision(*actions: ProposedAction, decision: str = "propose_effect") -> ModelDecision:
    return ModelDecision(
        schema_version=1,
        contract="ModelDecision/v1",
        episode_id="episode:telegram:5001",
        generation=1,
        purpose="plan",
        decision=decision,  # type: ignore[arg-type]
        actions=[action.model_dump(mode="json") for action in actions],
        clarification_question=None,
        wait_reason=None,
        uncertainty_basis_points=500,
    )


def _read(
    action_id: str = "step:state", *, depends_on: list[str] | None = None
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        kind="request_read",
        operation="ha.state.read",
        resource="overkapping",
        arguments={"target": "overkapping"},
        depends_on=depends_on or [],
        uncertainty_basis_points=100,
    )


def _effect(*, depends_on: list[str]) -> ProposedAction:
    return ProposedAction(
        action_id="step:test-effect",
        kind="propose_effect",
        operation="ha.test_target.set",
        resource="input_boolean.eva_test",
        arguments={"state": "on", "target": "input_boolean.eva_test"},
        depends_on=depends_on,
        uncertainty_basis_points=300,
    )


def _valid_decision() -> ModelDecision:
    return _decision(
        _read(),
        ProposedAction(
            action_id="step:history",
            kind="request_read",
            operation="ha.history.read",
            resource="overkapping",
            arguments={"period": "today", "target": "overkapping"},
            depends_on=["step:state"],
            uncertainty_basis_points=200,
        ),
        _effect(depends_on=["step:history"]),
    )


def _validate(
    decision: ModelDecision,
    *,
    policy: PolicyPack | None = None,
    plan_input: PlanModelInput | None = None,
    catalog: ActionToolCatalog | None = None,
):
    active_policy = policy or _policy()
    return validate_model_decision(
        decision,
        plan_input or _plan_input(active_policy),
        catalog or _catalog(active_policy),
        active_policy,
        deadline=DEADLINE,
    )


def test_existing_plan_contract_accepts_host_validated_read_effect_chain() -> None:
    decision = _valid_decision()
    plan = _validate(decision)

    assert [step.kind for step in plan.steps] == ["read", "read", "effect"]
    assert plan.model_decision_sha256 == canonical_sha256(decision)
    assert plan.maximum_connector_calls == 3
    assert plan.maximum_effect_attempts == 2
    assert plan.maximum_visible_deliveries == 0


def test_model_contract_accepts_mixed_and_legacy_all_effect_proposals() -> None:
    mixed = _decision(_read(), _effect(depends_on=["step:state"]))
    legacy = _decision(_effect(depends_on=[]))

    assert [action.kind for action in mixed.actions] == [
        "request_read",
        "propose_effect",
    ]
    assert [action.kind for action in legacy.actions] == ["propose_effect"]
    with pytest.raises(ValidationError, match="at least one proposed effect"):
        _decision(_read())


@pytest.mark.parametrize(
    "actions",
    (
        [_read(), _read()],
        [_read(depends_on=["step:later"]), _read("step:later")],
        [_read(), _read("step:later", depends_on=["step:state", "step:state"])],
    ),
)
def test_model_contract_rejects_duplicate_or_non_topological_actions(
    actions: list[ProposedAction],
) -> None:
    with pytest.raises(ValidationError):
        _decision(*actions, decision="request_read")


def test_host_rejects_unknown_wrong_kind_or_effect_without_a_read() -> None:
    unknown = _effect(depends_on=["step:state"]).model_copy(
        update={"operation": "ha.unknown.write"}
    )
    with pytest.raises(ActionChainValidationError, match="outside the active catalog"):
        _validate(_decision(_read(), unknown))

    wrong_kind = _effect(depends_on=["step:state"]).model_copy(
        update={"operation": "ha.state.read"}
    )
    with pytest.raises(ActionChainValidationError, match="differs from the host"):
        _validate(_decision(_read(), wrong_kind))

    with pytest.raises(ActionChainValidationError, match="requires a prior read"):
        _validate(_decision(_effect(depends_on=[])))


@pytest.mark.parametrize(
    ("resource", "arguments"),
    (
        ("verkeerde-kamer", {"target": "overkapping"}),
        ("overkapping", {"extra": "nee", "target": "overkapping"}),
        ("overkapping", {"target": "https://example.invalid/action"}),
        ("overkapping", {"target": "ssh://host.invalid/task"}),
        ("overkapping", {"target": "javascript:alert(1)"}),
        ("overkapping", {"target": "vbscript:msgbox(1)"}),
        ("overkapping", {"target": "intent:open"}),
        ("overkapping", {"target": "curl https://example.invalid/action"}),
        ("overkapping", {"target": "please run rm -rf /tmp/test"}),
        ("overkapping", {"target": "cat /etc/passwd"}),
        ("overkapping", {"target": "cmd.exe /c whoami"}),
        ("overkapping", {"target": "powershell.exe -Command Get-ChildItem"}),
        ("overkapping", {"target": "python3 -c pass"}),
        ("overkapping", {"target": "sh.exe -c id"}),
        ("overkapping", {"api_token": "niet-opslaan", "target": "overkapping"}),
        ("overkapping", {"shell_command": "doe iets", "target": "overkapping"}),
    ),
)
def test_host_rejects_unadmitted_resources_arguments_and_authority(
    resource: str, arguments: dict[str, str]
) -> None:
    unsafe_read = _read().model_copy(
        update={"resource": resource, "arguments": arguments}
    )
    with pytest.raises(ActionChainValidationError):
        _validate(_decision(unsafe_read, _effect(depends_on=["step:state"])))


def test_host_binds_plan_to_episode_catalog_policy_and_budgets() -> None:
    policy = _policy()
    with pytest.raises(ActionChainValidationError, match="episode generation"):
        _validate(
            _valid_decision(),
            policy=policy,
            plan_input=_plan_input(policy, episode_id="episode:other"),
        )
    with pytest.raises(ActionChainValidationError, match="active host catalog"):
        _validate(
            _valid_decision(),
            policy=policy,
            plan_input=_plan_input(policy, allowed_operations=OPERATIONS[:-1]),
        )
    with pytest.raises(ActionChainValidationError, match="host step limit"):
        _validate(
            _valid_decision(),
            policy=policy,
            plan_input=_plan_input(policy, maximum_steps=7),
        )

    constrained_policy = _policy(maximum_connector_calls=2)
    with pytest.raises(ActionChainValidationError, match="connector budget"):
        _validate(_valid_decision(), policy=constrained_policy)

    changed_policy = policy.model_copy(update={"policy_version": 2})
    with pytest.raises(ActionChainValidationError, match="active policy"):
        _validate(
            _valid_decision(),
            policy=changed_policy,
            plan_input=_plan_input(changed_policy),
            catalog=_catalog(policy),
        )


def test_disabled_tool_and_proof_floor_mismatch_fail_closed() -> None:
    policy = _policy()
    disabled_catalog = _catalog(policy, disabled_effect=True)
    with pytest.raises(ActionChainValidationError, match="disabled tool"):
        _validate(
            _valid_decision(),
            policy=policy,
            plan_input=_plan_input(
                policy, allowed_operations=disabled_catalog.enabled_operations
            ),
            catalog=disabled_catalog,
        )

    descriptors = list(_catalog(policy)._descriptors)  # noqa: SLF001
    effect_index = next(
        index
        for index, descriptor in enumerate(descriptors)
        if descriptor.effect_class == "effect"
    )
    descriptors[effect_index] = _descriptor(
        policy,
        "ha.test_target.set",
        "effect",
        "input_boolean.eva_test",
        "state",
        "target",
        proof_floor="alleen een providerantwoord",
    )
    with pytest.raises(ActionChainValidationError, match="proof floor"):
        _validate(
            _valid_decision(),
            policy=policy,
            catalog=ActionToolCatalog(tuple(descriptors)),
        )


def test_catalog_digest_and_operation_ids_are_deterministic() -> None:
    policy = _policy()
    catalog = _catalog(policy)
    reversed_catalog = ActionToolCatalog(tuple(reversed(catalog._descriptors)))  # noqa: SLF001
    assert catalog.sha256 == reversed_catalog.sha256

    plan = _validate(_valid_decision(), policy=policy, catalog=catalog)
    assert action_chain_thread_id(plan) == action_chain_thread_id(plan)
    assert action_chain_step_operation_id(plan, "step:test-effect") == (
        action_chain_step_operation_id(plan, "step:test-effect")
    )
    with pytest.raises(ValueError, match="does not belong"):
        action_chain_step_operation_id(plan, "step:other")


def test_validator_drift_changes_the_sealed_plan_and_operation_ids() -> None:
    policy = _policy()
    original = _catalog(policy)
    changed_descriptors = list(original._descriptors)  # noqa: SLF001
    changed_index = next(
        index
        for index, descriptor in enumerate(changed_descriptors)
        if descriptor.operation == "ha.state.read"
    )
    source = changed_descriptors[changed_index]
    changed_descriptors[changed_index] = ActionToolDescriptor(
        operation=source.operation,
        effect_class=source.effect_class,
        permission_class=source.permission_class,
        input_schema_sha256=source.input_schema_sha256,
        output_schema_sha256=source.output_schema_sha256,
        validator_sha256="f" * 64,
        policy_scope_sha256=source.policy_scope_sha256,
        proof_floor=source.proof_floor,
        enabled=True,
        validate_request=source.validate_request,
    )
    changed = ActionToolCatalog(tuple(changed_descriptors))

    original_plan = _validate(_valid_decision(), policy=policy, catalog=original)
    changed_plan = _validate(_valid_decision(), policy=policy, catalog=changed)
    assert original_plan.steps[0].capability_manifest_id != (
        changed_plan.steps[0].capability_manifest_id
    )
    assert canonical_sha256(original_plan) != canonical_sha256(changed_plan)
    assert action_chain_thread_id(original_plan) != action_chain_thread_id(changed_plan)


def test_unchosen_catalog_drift_changes_the_sealed_plan_identity() -> None:
    policy = _policy()
    original = _catalog(policy)
    changed_descriptors = list(original._descriptors)  # noqa: SLF001
    changed_index = next(
        index
        for index, descriptor in enumerate(changed_descriptors)
        if descriptor.operation == "ha.history.read"
    )
    source = changed_descriptors[changed_index]
    changed_descriptors[changed_index] = ActionToolDescriptor(
        operation=source.operation,
        effect_class=source.effect_class,
        permission_class=source.permission_class,
        input_schema_sha256=source.input_schema_sha256,
        output_schema_sha256=source.output_schema_sha256,
        validator_sha256="f" * 64,
        policy_scope_sha256=source.policy_scope_sha256,
        proof_floor=source.proof_floor,
        enabled=True,
        validate_request=source.validate_request,
    )
    changed = ActionToolCatalog(tuple(changed_descriptors))
    decision = _decision(_read(), _effect(depends_on=["step:state"]))

    original_plan = _validate(decision, policy=policy, catalog=original)
    changed_plan = _validate(decision, policy=policy, catalog=changed)
    assert original_plan.model_decision_sha256 == changed_plan.model_decision_sha256
    assert original_plan.steps[0].capability_manifest_id != (
        changed_plan.steps[0].capability_manifest_id
    )
    assert action_chain_thread_id(original_plan) != action_chain_thread_id(changed_plan)
