"""Host validation for bounded multi-tool plans using Eva's existing contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Literal

from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import EffectPolicy, PolicyPack
from eva.contracts.planning import (
    ModelDecision,
    PlanModelInput,
    ValidatedPlan,
    ValidatedStep,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/@+\-=]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]{0,31}:")
_EXECUTABLE_TEXT = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:bash|cat|chmod|chown|cmd|cp|curl|docker|eval|exec|"
    r"kubectl|mv|node(?:\d+(?:\.\d+)*)?|powershell|pwsh|"
    r"python(?:\d+(?:\.\d+)*)?|rm|scp|sftp|sh|ssh|sudo|wget|zsh)"
    r"(?:\.exe)?(?![A-Za-z0-9_.-])|\$\(|`",
    re.IGNORECASE,
)
_FORBIDDEN_ARGUMENT_PARTS = {
    "api_key",
    "authorization",
    "command",
    "cookie",
    "credential",
    "executable",
    "password",
    "script",
    "secret",
    "shell",
    "token",
}
_MAXIMUM_STEPS = 6

ToolEffectClass = Literal["read", "effect"]
RequestValidator = Callable[[str, dict[str, JsonValue]], None]


class ActionChainValidationError(ValueError):
    """A model plan differs from the host-owned capability and policy catalog."""


@dataclass(frozen=True, slots=True)
class ActionToolDescriptor:
    """Sanitized host authority for one tool that may appear in a model plan."""

    operation: str
    effect_class: ToolEffectClass
    permission_class: str
    input_schema_sha256: str
    output_schema_sha256: str
    validator_sha256: str
    policy_scope_sha256: str
    proof_floor: str
    enabled: bool
    validate_request: RequestValidator

    def __post_init__(self) -> None:
        for name, value in (
            ("operation", self.operation),
            ("permission_class", self.permission_class),
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"action tool {name} is invalid")
        if self.effect_class not in {"read", "effect"}:
            raise ValueError("action tool effect class is invalid")
        for name, value in (
            ("input_schema_sha256", self.input_schema_sha256),
            ("output_schema_sha256", self.output_schema_sha256),
            ("validator_sha256", self.validator_sha256),
            ("policy_scope_sha256", self.policy_scope_sha256),
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError(f"action tool {name} is invalid")
        if not self.proof_floor.strip() or len(self.proof_floor) > 512:
            raise ValueError("action tool proof floor is invalid")
        if not callable(self.validate_request):
            raise ValueError("action tool request validator is missing")

    def authority_payload(self) -> dict[str, JsonValue]:
        return {
            "capability_manifest_id": self.capability_manifest_id,
            "effect_class": self.effect_class,
            "enabled": self.enabled,
            "input_schema_sha256": self.input_schema_sha256,
            "operation": self.operation,
            "output_schema_sha256": self.output_schema_sha256,
            "permission_class": self.permission_class,
            "policy_scope_sha256": self.policy_scope_sha256,
            "proof_floor": self.proof_floor,
            "validator_sha256": self.validator_sha256,
        }

    @property
    def capability_manifest_id(self) -> str:
        """Content address that makes validator or authority drift change the plan."""

        return f"capability:sha256:{canonical_sha256(self._manifest_payload())}"

    def _manifest_payload(self) -> dict[str, JsonValue]:
        return {
            "effect_class": self.effect_class,
            "enabled": self.enabled,
            "input_schema_sha256": self.input_schema_sha256,
            "operation": self.operation,
            "output_schema_sha256": self.output_schema_sha256,
            "permission_class": self.permission_class,
            "policy_scope_sha256": self.policy_scope_sha256,
            "proof_floor": self.proof_floor,
            "validator_sha256": self.validator_sha256,
        }


class ActionToolCatalog:
    """Immutable per-turn allowlist; model output cannot add a capability."""

    def __init__(self, descriptors: tuple[ActionToolDescriptor, ...]) -> None:
        if not descriptors or len(descriptors) > 64:
            raise ValueError("action tool catalog must contain 1..64 tools")
        names = [descriptor.operation for descriptor in descriptors]
        if len(names) != len(set(names)):
            raise ValueError("action tool catalog operations must be unique")
        self._descriptors = tuple(sorted(descriptors, key=lambda item: item.operation))
        self._by_operation = {
            descriptor.operation: descriptor for descriptor in self._descriptors
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            [descriptor.authority_payload() for descriptor in self._descriptors]
        )

    @property
    def enabled_operations(self) -> tuple[str, ...]:
        return tuple(
            descriptor.operation
            for descriptor in self._descriptors
            if descriptor.enabled
        )

    def require(self, operation: str) -> ActionToolDescriptor:
        try:
            descriptor = self._by_operation[operation]
        except KeyError as error:
            raise ActionChainValidationError(
                "model plan requested a tool outside the active catalog"
            ) from error
        if not descriptor.enabled:
            raise ActionChainValidationError("model plan requested a disabled tool")
        return descriptor

    def plan_capability_manifest_id(self, operation: str) -> str:
        """Bind a chosen descriptor and the complete active catalog into one ID."""

        descriptor = self.require(operation)
        digest = canonical_sha256(
            {
                "catalog_sha256": self.sha256,
                "descriptor_manifest_id": descriptor.capability_manifest_id,
            }
        )
        return f"capability:plan:{digest}"


def validate_model_decision(
    decision: ModelDecision,
    plan_input: PlanModelInput,
    catalog: ActionToolCatalog,
    policy: PolicyPack,
    *,
    deadline: str,
) -> ValidatedPlan:
    """Turn one model proposal into a bounded host-owned ``ValidatedPlan/v1``."""

    if decision.decision not in {"request_read", "propose_effect"}:
        raise ActionChainValidationError("model decision contains no executable plan")
    policy_sha256 = canonical_sha256(policy)
    if policy_sha256 != plan_input.policy_pack_sha256:
        raise ActionChainValidationError("plan input is not bound to the active policy")
    if (
        decision.episode_id != plan_input.presence_context.episode_id
        or decision.generation != plan_input.presence_context.generation
    ):
        raise ActionChainValidationError(
            "model decision is not bound to the requested episode generation"
        )
    if len(plan_input.allowed_operations) != len(set(plan_input.allowed_operations)):
        raise ActionChainValidationError("allowed plan operations must be unique")
    if set(plan_input.allowed_operations) != set(catalog.enabled_operations):
        raise ActionChainValidationError(
            "plan input operations differ from the active host catalog"
        )
    if plan_input.maximum_steps > _MAXIMUM_STEPS:
        raise ActionChainValidationError("plan input exceeds the host step limit")
    if not 1 <= len(decision.actions) <= plan_input.maximum_steps:
        raise ActionChainValidationError("action chain exceeds the admitted step budget")

    policies = _operation_policies(policy)
    kinds: dict[str, ToolEffectClass] = {}
    ancestors: dict[str, set[str]] = {}
    validated_steps: list[ValidatedStep] = []
    effect_attempts = 0
    for action in decision.actions:
        descriptor = catalog.require(action.operation)
        if action.operation not in plan_input.allowed_operations:
            raise ActionChainValidationError("model plan requested an unadmitted tool")
        expected_kind: ToolEffectClass = (
            "read" if action.kind == "request_read" else "effect"
        )
        if descriptor.effect_class != expected_kind:
            raise ActionChainValidationError(
                "model action kind differs from the host tool descriptor"
            )
        if descriptor.policy_scope_sha256 != policy_sha256:
            raise ActionChainValidationError(
                "tool descriptor is not bound to the active policy"
            )
        effect_policy = policies.get(action.operation)
        if effect_policy is None:
            raise ActionChainValidationError("tool is missing from the active policy")
        if effect_policy.effect_class != expected_kind:
            raise ActionChainValidationError(
                "tool effect class differs from the active policy"
            )
        if effect_policy.proof_floor != descriptor.proof_floor:
            raise ActionChainValidationError(
                "tool proof floor differs from the active policy"
            )

        _reject_unsafe_plan_input(action.resource)
        _reject_unsafe_plan_input(action.arguments)
        try:
            descriptor.validate_request(action.resource, action.arguments)
        except Exception as error:
            raise ActionChainValidationError(
                "model request differs from the admitted schema or resource scope"
            ) from error

        dependency_ancestors = set(action.depends_on)
        for dependency in action.depends_on:
            dependency_ancestors.update(ancestors[dependency])
        if expected_kind == "effect" and not any(
            kinds[dependency] == "read" for dependency in dependency_ancestors
        ):
            raise ActionChainValidationError(
                "an effect step requires a prior read dependency"
            )
        ancestors[action.action_id] = dependency_ancestors
        kinds[action.action_id] = expected_kind
        if expected_kind == "effect":
            effect_attempts += effect_policy.maximum_attempts
        validated_steps.append(
            ValidatedStep(
                step_id=action.action_id,
                kind=expected_kind,
                operation=action.operation,
                resource=action.resource,
                input_schema_sha256=descriptor.input_schema_sha256,
                output_schema_sha256=descriptor.output_schema_sha256,
                depends_on=action.depends_on,
                capability_manifest_id=catalog.plan_capability_manifest_id(
                    action.operation
                ),
                proof_floor=descriptor.proof_floor,
            )
        )

    if len(validated_steps) > policy.budgets.maximum_connector_calls:
        raise ActionChainValidationError("plan exceeds the policy connector budget")
    if effect_attempts > 32:
        raise ActionChainValidationError("plan exceeds the contract effect budget")
    return ValidatedPlan(
        schema_version=1,
        contract="ValidatedPlan/v1",
        episode_id=decision.episode_id,
        generation=decision.generation,
        model_decision_sha256=canonical_sha256(decision),
        policy_sha256=policy_sha256,
        steps=[step.model_dump(mode="json") for step in validated_steps],
        maximum_connector_calls=len(validated_steps),
        maximum_effect_attempts=effect_attempts,
        maximum_visible_deliveries=0,
        deadline=deadline,
    )


def action_chain_thread_id(plan: ValidatedPlan) -> str:
    digest = canonical_sha256(
        [plan.episode_id, plan.generation, canonical_sha256(plan)]
    )
    return f"action-chain:{digest[:48]}"


def action_chain_step_operation_id(plan: ValidatedPlan, step_id: str) -> str:
    if step_id not in {step.step_id for step in plan.steps}:
        raise ValueError("action chain step does not belong to the plan")
    digest = canonical_sha256(
        [plan.episode_id, plan.generation, canonical_sha256(plan), step_id]
    )
    return f"action-chain-step:{digest[:48]}"


def _operation_policies(policy: PolicyPack) -> dict[str, EffectPolicy]:
    operations = [item.operation for item in policy.effect_policies]
    if len(operations) != len(set(operations)):
        raise ActionChainValidationError("active policy operations must be unique")
    return {item.operation: item for item in policy.effect_policies}


def _reject_unsafe_plan_input(value: JsonValue) -> None:
    validate_json_value(value)
    if isinstance(value, str):
        normalized = value.strip()
        if (
            "://" in normalized
            or _URI_SCHEME.match(normalized)
            or _EXECUTABLE_TEXT.search(normalized)
        ):
            raise ActionChainValidationError(
                "model plan may not carry an external URI or executable instruction"
            )
        return
    if isinstance(value, list):
        for item in value:
            _reject_unsafe_plan_input(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_ARGUMENT_PARTS):
                raise ActionChainValidationError(
                    "model plan may not carry secrets or executable instructions"
                )
            _reject_unsafe_plan_input(item)


__all__ = [
    "ActionChainValidationError",
    "ActionToolCatalog",
    "ActionToolDescriptor",
    "action_chain_step_operation_id",
    "action_chain_thread_id",
    "validate_model_decision",
]
