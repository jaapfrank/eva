"""Host validation for bounded multi-tool plans using Eva's existing contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any, Literal

from eva.application.effect_verification import (
    EffectVerificationError,
    EffectVerificationProjection,
)
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
    effect_verification: EffectVerificationProjection | None = None

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
        if self.effect_class == "read" and self.effect_verification is not None:
            raise ValueError("a read tool cannot define effect verification")

    def authority_payload(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
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
        if self.effect_verification is not None:
            payload["effect_verification"] = (
                self.effect_verification.authority_payload()
            )
        return payload

    @property
    def capability_manifest_id(self) -> str:
        """Content address that makes validator or authority drift change the plan."""

        return f"capability:sha256:{canonical_sha256(self._manifest_payload())}"

    def _manifest_payload(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
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
        if self.effect_verification is not None:
            payload["effect_verification"] = (
                self.effect_verification.authority_payload()
            )
        return payload


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

    def authority_snapshot(self) -> dict[str, JsonValue]:
        """Return the complete sanitized authority used to validate one plan."""

        return {
            "catalog_sha256": self.sha256,
            "descriptors": [
                descriptor.authority_payload() for descriptor in self._descriptors
            ],
        }

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
        if expected_kind == "effect":
            if descriptor.effect_verification is None:
                raise ActionChainValidationError(
                    "effect tool lacks independent verification projection"
                )
            try:
                descriptor.effect_verification.project_expected(
                    action.resource, action.arguments
                )
            except EffectVerificationError as error:
                raise ActionChainValidationError(
                    "effect request cannot produce its expected proof"
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


def validate_admitted_action_chain(
    decision: ModelDecision,
    plan: ValidatedPlan,
    authority_snapshot: dict[str, JsonValue],
) -> str:
    """Revalidate the exact model decision, plan and full catalog binding."""

    if decision.episode_id != plan.episode_id or decision.generation != plan.generation:
        raise ActionChainValidationError(
            "admitted decision and plan use another episode generation"
        )
    if canonical_sha256(decision) != plan.model_decision_sha256:
        raise ActionChainValidationError("admitted model decision hash differs")
    if len(decision.actions) != len(plan.steps):
        raise ActionChainValidationError("admitted action and step counts differ")
    for action, step in zip(decision.actions, plan.steps, strict=True):
        expected_kind = "read" if action.kind == "request_read" else "effect"
        if (
            action.action_id != step.step_id
            or action.operation != step.operation
            or action.resource != step.resource
            or action.depends_on != step.depends_on
            or expected_kind != step.kind
        ):
            raise ActionChainValidationError(
                "admitted model action differs from its validated step"
            )
    if (
        plan.maximum_connector_calls != len(plan.steps)
        or plan.maximum_visible_deliveries != 0
    ):
        raise ActionChainValidationError("admitted plan budget shape differs")

    descriptors, catalog_sha256 = _validate_authority_snapshot(authority_snapshot)
    for step in plan.steps:
        descriptor = descriptors.get(step.operation)
        if descriptor is None or descriptor["enabled"] is not True:
            raise ActionChainValidationError(
                "admitted step is absent or disabled in the authority snapshot"
            )
        expected_manifest = f"capability:plan:{canonical_sha256({
            'catalog_sha256': catalog_sha256,
            'descriptor_manifest_id': descriptor['capability_manifest_id'],
        })}"
        if step.capability_manifest_id != expected_manifest:
            raise ActionChainValidationError(
                "admitted step differs from the complete catalog authority"
            )
        if (
            step.kind != descriptor["effect_class"]
            or step.input_schema_sha256 != descriptor["input_schema_sha256"]
            or step.output_schema_sha256 != descriptor["output_schema_sha256"]
            or step.proof_floor != descriptor["proof_floor"]
            or plan.policy_sha256 != descriptor["policy_scope_sha256"]
        ):
            raise ActionChainValidationError(
                "admitted step metadata differs from its tool authority"
            )
    return catalog_sha256


def validate_action_chain_requests(
    decision: ModelDecision, catalog: ActionToolCatalog
) -> None:
    """Recheck exact request arguments at the durable admission boundary."""

    for action in decision.actions:
        descriptor = catalog.require(action.operation)
        expected_kind = "read" if action.kind == "request_read" else "effect"
        if descriptor.effect_class != expected_kind:
            raise ActionChainValidationError(
                "admitted request kind differs from the host tool descriptor"
            )
        _reject_unsafe_plan_input(action.resource)
        _reject_unsafe_plan_input(action.arguments)
        try:
            descriptor.validate_request(action.resource, action.arguments)
        except Exception as error:
            raise ActionChainValidationError(
                "admitted request differs from the tool schema or resource scope"
            ) from error
        if expected_kind == "effect":
            if descriptor.effect_verification is None:
                raise ActionChainValidationError(
                    "effect tool lacks independent verification projection"
                )
            try:
                descriptor.effect_verification.project_expected(
                    action.resource, action.arguments
                )
            except EffectVerificationError as error:
                raise ActionChainValidationError(
                    "effect request cannot produce its expected proof"
                ) from error


def _operation_policies(policy: PolicyPack) -> dict[str, EffectPolicy]:
    operations = [item.operation for item in policy.effect_policies]
    if len(operations) != len(set(operations)):
        raise ActionChainValidationError("active policy operations must be unique")
    return {item.operation: item for item in policy.effect_policies}


def _validate_authority_snapshot(
    snapshot: dict[str, JsonValue],
) -> tuple[dict[str, dict[str, Any]], str]:
    validate_json_value(snapshot)
    if set(snapshot) != {"catalog_sha256", "descriptors"}:
        raise ActionChainValidationError("authority snapshot fields differ")
    catalog_sha256 = snapshot["catalog_sha256"]
    raw_descriptors = snapshot["descriptors"]
    if not isinstance(catalog_sha256, str) or not _SHA256.fullmatch(catalog_sha256):
        raise ActionChainValidationError("authority snapshot catalog hash is invalid")
    if (
        not isinstance(raw_descriptors, list)
        or not 1 <= len(raw_descriptors) <= 64
    ):
        raise ActionChainValidationError("authority snapshot descriptor count is invalid")

    required = {
        "capability_manifest_id",
        "effect_class",
        "enabled",
        "input_schema_sha256",
        "operation",
        "output_schema_sha256",
        "permission_class",
        "policy_scope_sha256",
        "proof_floor",
        "validator_sha256",
    }
    descriptors: dict[str, dict[str, Any]] = {}
    previous_operation: str | None = None
    for raw in raw_descriptors:
        if not isinstance(raw, dict) or frozenset(raw) not in {
            frozenset(required),
            frozenset(required | {"effect_verification"}),
        }:
            raise ActionChainValidationError("authority descriptor fields differ")
        operation = raw["operation"]
        effect_class = raw["effect_class"]
        enabled = raw["enabled"]
        permission_class = raw["permission_class"]
        proof_floor = raw["proof_floor"]
        if not isinstance(operation, str) or not _IDENTIFIER.fullmatch(operation):
            raise ActionChainValidationError("authority operation is invalid")
        if previous_operation is not None and operation <= previous_operation:
            raise ActionChainValidationError(
                "authority descriptors are not uniquely sorted"
            )
        if effect_class not in {"read", "effect"} or not isinstance(enabled, bool):
            raise ActionChainValidationError("authority effect class is invalid")
        raw_verification = raw.get("effect_verification")
        if raw_verification is not None:
            if effect_class != "effect":
                raise ActionChainValidationError(
                    "read authority cannot define effect verification"
                )
            try:
                EffectVerificationProjection.from_authority_payload(
                    raw_verification
                )
            except EffectVerificationError as error:
                raise ActionChainValidationError(
                    "authority effect verification is invalid"
                ) from error
        if (
            not isinstance(permission_class, str)
            or not _IDENTIFIER.fullmatch(permission_class)
            or not isinstance(proof_floor, str)
            or not proof_floor.strip()
            or len(proof_floor) > 512
        ):
            raise ActionChainValidationError("authority policy metadata is invalid")
        for field in (
            "input_schema_sha256",
            "output_schema_sha256",
            "policy_scope_sha256",
            "validator_sha256",
        ):
            value = raw[field]
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ActionChainValidationError("authority digest is invalid")
        descriptor_manifest_id = raw["capability_manifest_id"]
        manifest_payload = {
            key: value
            for key, value in raw.items()
            if key != "capability_manifest_id"
        }
        expected_descriptor_id = (
            f"capability:sha256:{canonical_sha256(manifest_payload)}"
        )
        if descriptor_manifest_id != expected_descriptor_id:
            raise ActionChainValidationError(
                "authority descriptor content address differs"
            )
        descriptors[operation] = raw
        previous_operation = operation
    if canonical_sha256(raw_descriptors) != catalog_sha256:
        raise ActionChainValidationError("authority catalog content address differs")
    return descriptors, catalog_sha256


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
    "validate_admitted_action_chain",
    "validate_action_chain_requests",
    "validate_model_decision",
]
