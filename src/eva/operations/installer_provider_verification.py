"""Connector-neutral verification of installer credentials.

The loopback wizard or operator CLI may invoke this component, but neither
receives the retained secret. A provider adapter receives it through one
bounded probe port and returns a closed observation. The verifier persists a
sanitized, content-addressed receipt before it asks the existing capability
workflow to record ``ready`` or ``blocked``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
import re
import time
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from eva.application.installer import (
    InstallerProviderProbe,
    InstallerProviderProbeError,
    InstallerProviderProbeObservation,
    InstallerProviderVerificationError,
)
from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.identity_pack_lifecycle import IdentityPackLifecycleError
from eva.operations.installer_capability_workflow import (
    InstallerCapabilityWorkflow,
    InstallerCapabilityWorkflowError,
)
from eva.operations.installer_identity_storage import write_private_once
from eva.operations.installer_secret_store import (
    SECRET_SLOTS,
    InstallerSecretNotAvailableError,
    InstallerSecretStore,
    InstallerSecretStoreError,
)
from eva.operations.installer_safety import (
    require_owner_only_directory,
    require_utc_timestamp,
)


VerificationOutcome = Literal["ready", "blocked"]
ProviderOperation = Literal["read_only_identity", "bounded_generation"]
UsageMeasurement = Literal["not_applicable", "measured", "unmeasured"]
VerificationReason = Literal[
    "verified",
    "unauthorized",
    "unreachable",
    "timeout",
    "invalid_response",
    "provider_error",
]
_BLOCKED_REASONS = frozenset(
    {"unauthorized", "unreachable", "timeout", "invalid_response", "provider_error"}
)
_ALL_REASONS = frozenset({"verified", *_BLOCKED_REASONS})
_CHECK_IDS = MappingProxyType(
    {
        "model_provider": "model.response.generate",
        "telegram": "telegram.get_me",
        "home_assistant": "home_assistant.api.read",
        "email": "email.identity.read",
    }
)
_PROVIDER_SCOPES = MappingProxyType(
    {
        "model_provider": (
            (
                "model.response.generate:installer_probe",
                "Eén kleine modelcontrole zonder tools of opslag",
            ),
        ),
        "telegram": (
            ("telegram.bot_identity.read", "Alleen de identiteit van de testbot lezen"),
        ),
        "home_assistant": (
            (
                "home_assistant.api_root.read",
                "Alleen controleren of de Home Assistant-API bereikbaar is",
            ),
        ),
        "email": (
            (
                "agentmail.inbox_identity.read",
                "Alleen de gekozen inboxidentiteit controleren; geen berichten lezen",
            ),
        ),
    }
)
_MIN_TIMEOUT_SECONDS = 1.0
_MAX_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class InstallerProviderVerificationResult:
    slot_id: str
    outcome: VerificationOutcome
    reason_code: VerificationReason
    evidence_sha256: str
    capability_revision: int
    capability_head_sha256: str | None
    already_in_state: bool
    provider_scope_manifest_sha256: str


def installer_provider_scope_manifest(slot_id: object) -> dict[str, object]:
    """Return the closed verification-only access boundary for one provider."""

    if not isinstance(slot_id, str) or slot_id not in _PROVIDER_SCOPES:
        raise InstallerProviderVerificationError(
            "provider verification slot is invalid"
        )
    scopes = _PROVIDER_SCOPES[slot_id]
    return {
        "contract": "EvaInstallerProviderScopeManifest/v1",
        "credential_scope_introspection": False,
        "mutation_allowed": False,
        "provider_scopes": [
            {"description": description, "scope_id": scope_id}
            for scope_id, description in scopes
        ],
        "slot_id": slot_id,
        "use_boundary": "installer_verification_only",
    }


def installer_provider_scope_manifest_sha256(slot_id: object) -> str:
    return hashlib.sha256(
        canonical_json_bytes(installer_provider_scope_manifest(slot_id))
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> str:
    return require_utc_timestamp(
        value,
        error=InstallerProviderVerificationError,
        invalid_message="provider verification timestamp is invalid",
        non_utc_message="provider verification timestamp must be UTC",
    )


def _private_directory(path: Path) -> None:
    require_owner_only_directory(
        path,
        error=InstallerProviderVerificationError,
        unavailable_message="provider verification workspace is unavailable",
        invalid_message=(
            "provider verification workspace must be an owner-only directory"
        ),
    )


def _checked_observation(
    value: object, *, expected_check_id: str
) -> InstallerProviderProbeObservation:
    if not isinstance(value, InstallerProviderProbeObservation):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if value.check_id != expected_check_id:
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if (
        value.outcome not in {"ready", "blocked"}
        or value.reason_code not in _ALL_REASONS
    ):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if (value.outcome == "ready") != (value.reason_code == "verified"):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if value.provider_operation not in {"read_only_identity", "bounded_generation"}:
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if value.usage_measurement not in {"not_applicable", "measured", "unmeasured"}:
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    counts = (value.input_tokens, value.output_tokens)
    if not isinstance(value.billable_request_attempted, bool) or (
        value.billable_request_attempted
        and value.provider_operation != "bounded_generation"
    ):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if value.usage_measurement == "measured":
        if any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0
            for count in counts
        ):
            raise InstallerProviderVerificationError(
                "provider verification observation is invalid"
            )
    elif counts != (None, None):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    if (
        (
            expected_check_id == "model.response.generate"
            and value.provider_operation != "bounded_generation"
        )
        or (
            expected_check_id != "model.response.generate"
            and value.provider_operation != "read_only_identity"
        )
        or (
            value.provider_operation == "read_only_identity"
            and value.usage_measurement != "not_applicable"
        )
        or (
            value.provider_operation == "bounded_generation"
            and value.usage_measurement == "not_applicable"
        )
        or (
            value.outcome == "ready"
            and value.provider_operation == "bounded_generation"
            and not value.billable_request_attempted
        )
    ):
        raise InstallerProviderVerificationError(
            "provider verification observation is invalid"
        )
    return value


def _blocked_observation(
    check_id: str, reason_code: VerificationReason
) -> InstallerProviderProbeObservation:
    if check_id == "model.response.generate":
        return InstallerProviderProbeObservation(
            check_id,
            "blocked",
            reason_code,
            provider_operation="bounded_generation",
            usage_measurement="unmeasured",
        )
    return InstallerProviderProbeObservation(check_id, "blocked", reason_code)


@dataclass(frozen=True, slots=True)
class InstallerProviderVerifier:
    capability_workflow: InstallerCapabilityWorkflow
    secret_store: InstallerSecretStore
    evidence_workspace: Path
    probes: Mapping[str, InstallerProviderProbe]
    configuration_sha256_by_slot: Mapping[str, str] = field(default_factory=dict)
    clock: Callable[[], str] = _utc_now
    monotonic: Callable[[], float] = time.monotonic

    @staticmethod
    def scope_manifest(slot_id: object) -> dict[str, object]:
        return installer_provider_scope_manifest(slot_id)

    @staticmethod
    def scope_manifest_sha256(slot_id: object) -> str:
        return installer_provider_scope_manifest_sha256(slot_id)

    def for_configured_probe(
        self,
        *,
        slot_id: str,
        probe: InstallerProviderProbe,
        configuration_sha256: str,
    ) -> "InstallerProviderVerifier":
        return InstallerProviderVerifier(
            capability_workflow=self.capability_workflow,
            secret_store=self.secret_store,
            evidence_workspace=self.evidence_workspace,
            probes={slot_id: probe},
            configuration_sha256_by_slot={slot_id: configuration_sha256},
            clock=self.clock,
            monotonic=self.monotonic,
        )

    def __post_init__(self) -> None:
        _private_directory(self.evidence_workspace)
        copied = dict(self.probes)
        if not copied or any(
            slot_id not in _CHECK_IDS
            or not callable(getattr(probe, "read_only_probe", None))
            for slot_id, probe in copied.items()
        ):
            raise InstallerProviderVerificationError(
                "provider verification probe registry is invalid"
            )
        configuration = dict(self.configuration_sha256_by_slot)
        if any(
            slot_id not in copied
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for slot_id, digest in configuration.items()
        ):
            raise InstallerProviderVerificationError(
                "provider verification configuration binding is invalid"
            )
        object.__setattr__(self, "probes", MappingProxyType(copied))
        object.__setattr__(
            self, "configuration_sha256_by_slot", MappingProxyType(configuration)
        )

    def _preflight(
        self, slot_id: str
    ) -> tuple[str, int, InstallerProviderProbe, bytes, int, str | None]:
        if slot_id not in SECRET_SLOTS or slot_id not in _CHECK_IDS:
            raise InstallerProviderVerificationError(
                "provider verification slot is invalid"
            )
        probe = self.probes.get(slot_id)
        if probe is None:
            raise InstallerProviderVerificationError(
                "provider verification probe is unavailable"
            )
        try:
            capability_status = self.capability_workflow.status()
            capability = capability_status.step(slot_id)
        except InstallerCapabilityWorkflowError as exc:
            raise InstallerProviderVerificationError(
                "provider verification preflight is unavailable"
            ) from exc
        if capability.state not in {"selected", "blocked"}:
            raise InstallerProviderVerificationError(
                "capability must be selected before provider verification"
            )
        try:
            secret_status, raw_secret = self.secret_store.read_secret_with_status(
                slot_id=slot_id
            )
        except InstallerSecretNotAvailableError as exc:
            raise InstallerProviderVerificationError(
                "provider verification secret is not available"
            ) from exc
        except InstallerSecretStoreError as exc:
            raise InstallerProviderVerificationError(
                "provider verification secret is unavailable"
            ) from exc
        expected_reference = f"installer-secret://{slot_id}/v1"
        if (
            secret_status.state != "present"
            or secret_status.reference != expected_reference
            or secret_status.generation is None
        ):
            raise InstallerProviderVerificationError(
                "provider verification secret is not available"
            )
        return (
            expected_reference,
            secret_status.generation,
            probe,
            raw_secret,
            capability_status.revision,
            capability_status.head_sha256,
        )

    def verify(
        self, *, slot_id: object, timeout_seconds: object = 10.0
    ) -> InstallerProviderVerificationResult:
        if not isinstance(slot_id, str):
            raise InstallerProviderVerificationError(
                "provider verification slot is invalid"
            )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not _MIN_TIMEOUT_SECONDS
            <= float(timeout_seconds)
            <= _MAX_TIMEOUT_SECONDS
        ):
            raise InstallerProviderVerificationError(
                "provider verification timeout is invalid"
            )
        checked_timeout = float(timeout_seconds)
        (
            reference,
            secret_generation,
            probe,
            raw_secret,
            capability_revision,
            capability_head_sha256,
        ) = self._preflight(slot_id)

        started_at = _timestamp(self.clock())
        started_monotonic = self.monotonic()
        secret_buffer = bytearray(raw_secret)
        del raw_secret
        observation: InstallerProviderProbeObservation
        try:
            try:
                observed = probe.read_only_probe(
                    secret=memoryview(secret_buffer).toreadonly(),
                    timeout_seconds=checked_timeout,
                )
                observation = _checked_observation(
                    observed, expected_check_id=_CHECK_IDS[slot_id]
                )
            except TimeoutError:
                observation = _blocked_observation(_CHECK_IDS[slot_id], "timeout")
            except InstallerProviderProbeError as exc:
                observation = InstallerProviderProbeObservation(
                    _CHECK_IDS[slot_id],
                    "blocked",
                    exc.reason_code,
                    provider_operation=exc.provider_operation,
                    usage_measurement=exc.usage_measurement,
                    billable_request_attempted=exc.billable_request_attempted,
                )
            except InstallerProviderVerificationError:
                observation = _blocked_observation(
                    _CHECK_IDS[slot_id], "invalid_response"
                )
            except Exception:
                observation = _blocked_observation(
                    _CHECK_IDS[slot_id], "provider_error"
                )
        finally:
            secret_buffer[:] = b"\x00" * len(secret_buffer)

        completed_monotonic = self.monotonic()
        if (
            not isinstance(started_monotonic, (int, float))
            or isinstance(started_monotonic, bool)
            or not math.isfinite(float(started_monotonic))
            or not isinstance(completed_monotonic, (int, float))
            or isinstance(completed_monotonic, bool)
            or not math.isfinite(float(completed_monotonic))
            or completed_monotonic < started_monotonic
        ):
            raise InstallerProviderVerificationError(
                "provider verification monotonic clock is invalid"
            )
        duration_ms = int(round((completed_monotonic - started_monotonic) * 1000))
        if duration_ms > round(checked_timeout * 1000):
            observation = InstallerProviderProbeObservation(
                _CHECK_IDS[slot_id],
                "blocked",
                "timeout",
                provider_operation=observation.provider_operation,
                usage_measurement=(
                    "unmeasured"
                    if observation.provider_operation == "bounded_generation"
                    else "not_applicable"
                ),
                billable_request_attempted=observation.billable_request_attempted,
            )
        completed_at = _timestamp(self.clock())
        if completed_at < started_at:
            raise InstallerProviderVerificationError(
                "provider verification completion precedes start"
            )

        scope_manifest = installer_provider_scope_manifest(slot_id)
        scope_manifest_sha256 = hashlib.sha256(
            canonical_json_bytes(scope_manifest)
        ).hexdigest()
        receipt = {
            "billable_request_attempted": observation.billable_request_attempted,
            "check_id": observation.check_id,
            "completed_at": completed_at,
            "configuration_sha256": self.configuration_sha256_by_slot.get(slot_id),
            "contract": "EvaInstallerProviderVerificationReceipt/v4",
            "cost_measurement": (
                "not_reported_by_chatgpt_access"
                if observation.provider_operation == "bounded_generation"
                else "not_applicable"
            ),
            "duration_ms": duration_ms,
            "input_tokens": observation.input_tokens,
            "mutation_attempted": False,
            "outcome": observation.outcome,
            "output_tokens": observation.output_tokens,
            "provider_operation": observation.provider_operation,
            "provider_payload_retained": False,
            "provider_scope_manifest_sha256": scope_manifest_sha256,
            "provider_scopes": [
                item["scope_id"] for item in scope_manifest["provider_scopes"]
            ],
            "reason_code": observation.reason_code,
            "secret_reference": reference,
            "secret_generation": secret_generation,
            "secret_returned": False,
            "slot_id": slot_id,
            "started_at": started_at,
            "usage_measurement": observation.usage_measurement,
        }
        encoded = canonical_json_bytes(receipt)
        evidence_sha256 = hashlib.sha256(encoded).hexdigest()
        evidence_path = self.evidence_workspace / f"{evidence_sha256}.json"
        try:
            _private_directory(self.evidence_workspace)
            write_private_once(evidence_path, encoded)
        except IdentityPackLifecycleError as exc:
            raise InstallerProviderVerificationError(
                "provider verification receipt write failed"
            ) from exc
        try:
            transition = self.capability_workflow.record_verification(
                step_id=slot_id,
                outcome=observation.outcome,
                evidence_sha256=evidence_sha256,
                expected_revision=capability_revision,
                expected_head_sha256=capability_head_sha256,
            )
        except InstallerCapabilityWorkflowError as exc:
            raise InstallerProviderVerificationError(
                "provider verification state changed before commit"
            ) from exc
        return InstallerProviderVerificationResult(
            slot_id=slot_id,
            outcome=observation.outcome,
            reason_code=observation.reason_code,
            evidence_sha256=evidence_sha256,
            capability_revision=transition.revision,
            capability_head_sha256=transition.head_sha256,
            already_in_state=transition.already_in_state,
            provider_scope_manifest_sha256=scope_manifest_sha256,
        )


__all__ = [
    "InstallerProviderProbe",
    "InstallerProviderProbeError",
    "InstallerProviderProbeObservation",
    "InstallerProviderVerificationError",
    "InstallerProviderVerificationResult",
    "InstallerProviderVerifier",
    "installer_provider_scope_manifest",
    "installer_provider_scope_manifest_sha256",
]
