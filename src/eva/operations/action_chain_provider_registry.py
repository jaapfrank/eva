"""Release-sealed operations authority for action-chain connector workers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from eva.adapters.connectors.process import (
    ConnectorCommandTargetPin,
    ConnectorFixtureSelection,
    ConnectorProgramBinding,
    ConnectorProcessSpec,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.operations.release_verifier import ReleaseVerification


_RELEASE_SEAL = object()


@dataclass(frozen=True, slots=True)
class ReadProviderProfile:
    """One release-owned manifest and exact worker process specification."""

    capability_manifest_id: UUID
    revision: int
    connector_manifest: ConnectorManifest
    process_spec: ConnectorProcessSpec

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("read provider profile revision must be positive")
        if (
            hashlib.sha256(self.process_spec.program_bytes).hexdigest()
            != self.connector_manifest.executable_sha256
        ):
            raise ValueError("provider program bytes differ from connector manifest")
        self.process_spec.verify_command_targets()
        pinned_indexes = {
            pin.argument_index for pin in self.process_spec.command_target_pins
        }
        if 0 not in pinned_indexes:
            raise ValueError("read provider executable target must be pinned")
        for index, argument in enumerate(self.process_spec.command):
            if Path(argument).is_absolute() and index not in pinned_indexes:
                raise ValueError("absolute read provider command target must be pinned")
        if self.process_spec.program_binding is None:
            raise ValueError("read provider program binding is required")

    @property
    def process_spec_payload(self) -> dict[str, object]:
        fixture = self.process_spec.fixture_selection
        return {
            "contract": "ConnectorProcessSpec/v1",
            "command": list(self.process_spec.command),
            "command_targets": [
                {
                    "argument_index": pin.argument_index,
                    "target_path": str(pin.target_path),
                    "target_sha256": pin.target_sha256,
                }
                for pin in self.process_spec.command_target_pins
            ],
            "fixture_selection": (
                None
                if fixture is None
                else {
                    "artifact_path": str(fixture.artifact_path),
                    "artifact_sha256": fixture.artifact_sha256,
                    "worker_key": fixture.worker_key,
                    "case_id": fixture.case_id,
                    "current_state_sha256": fixture.current_state_sha256,
                }
            ),
            "invoke_failure_phase": self.process_spec.invoke_failure_phase,
            "program_binding": {
                "kind": self.process_spec.program_binding.kind,
                "argument_index": self.process_spec.program_binding.argument_index,
            },
            "program_sha256": hashlib.sha256(
                self.process_spec.program_bytes
            ).hexdigest(),
        }

    @property
    def process_spec_sha256(self) -> str:
        return canonical_sha256(self.process_spec_payload)

    @property
    def profile_payload(self) -> dict[str, object]:
        return {
            "contract": "ReadProviderProfile/v1",
            "capability_manifest_id": str(self.capability_manifest_id),
            "revision": self.revision,
            "connector_manifest_sha256": canonical_sha256(
                self.connector_manifest
            ),
            "process_spec_sha256": self.process_spec_sha256,
        }

    @property
    def profile_sha256(self) -> str:
        return canonical_sha256(self.profile_payload)

    @property
    def worker_id(self) -> str:
        return f"connector-worker:{self.process_spec_sha256}"


class SealedReadProviderRegistry:
    """Closed release input resolved by capability-manifest identity."""

    def __init__(
        self,
        profiles: tuple[ReadProviderProfile, ...],
        *,
        release_artifact_sha256: str,
        release_manifest_sha256: str,
        provenance_sha256: str,
        release_verification_receipt_sha256: str,
        _seal: object,
    ) -> None:
        if _seal is not _RELEASE_SEAL:
            raise ValueError("read provider registry requires a sealed release artifact")
        ordered = tuple(sorted(profiles, key=lambda item: str(item.capability_manifest_id)))
        if not ordered:
            raise ValueError("read provider registry must not be empty")
        by_manifest: dict[UUID, ReadProviderProfile] = {}
        connector_operations: set[tuple[str, str]] = set()
        for profile in ordered:
            if profile.capability_manifest_id in by_manifest:
                raise ValueError("read provider manifest identity must be unique")
            for operation in profile.connector_manifest.operations:
                key = (profile.connector_manifest.connector_id, operation.operation)
                if key in connector_operations:
                    raise ValueError("read provider connector operation must be unique")
                connector_operations.add(key)
            by_manifest[profile.capability_manifest_id] = profile
        self._profiles = ordered
        self._by_manifest: Mapping[UUID, ReadProviderProfile] = MappingProxyType(
            by_manifest
        )
        self._registry_sha256 = release_artifact_sha256
        self._release_manifest_sha256 = release_manifest_sha256
        self._provenance_sha256 = provenance_sha256
        self._release_verification_receipt_sha256 = (
            release_verification_receipt_sha256
        )

    @classmethod
    def from_release_artifact(
        cls,
        artifact_bytes: bytes,
        *,
        provenance_bytes: bytes,
        release_verification: ReleaseVerification,
    ) -> SealedReadProviderRegistry:
        """Load registry bytes selected by signed release provenance."""

        release_verification.require_verified()
        try:
            provenance = json.loads(provenance_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("release provenance is invalid JSON") from error
        if canonical_json_bytes(provenance) != provenance_bytes:
            raise ValueError("release provenance is not canonical JSON")
        _require_keys(
            provenance,
            {"schema_version", "contract", "release_id", "runtime_artifacts"},
            "release provenance",
        )
        artifacts = provenance["runtime_artifacts"]
        _require_keys(
            artifacts,
            {"read_provider_registry_sha256"},
            "release provenance artifacts",
        )
        if (
            provenance["schema_version"] != 1
            or provenance["contract"] != "EvaRuntimeArtifactProvenance/v1"
            or provenance["release_id"] != release_verification.release_id
            or hashlib.sha256(provenance_bytes).hexdigest()
            != release_verification.provenance_sha256
        ):
            raise ValueError("release provenance differs from verified release")
        expected_sha256 = artifacts["read_provider_registry_sha256"]
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_sha256
            )
        ):
            raise ValueError("release provenance registry hash is invalid")
        if hashlib.sha256(artifact_bytes).hexdigest() != expected_sha256:
            raise ValueError("read provider release artifact hash differs")
        try:
            artifact = json.loads(artifact_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("read provider release artifact is invalid JSON") from error
        if canonical_json_bytes(artifact) != artifact_bytes:
            raise ValueError("read provider release artifact is not canonical JSON")
        _require_keys(
            artifact,
            {"schema_version", "contract", "release_id", "profiles"},
            "release artifact",
        )
        if (
            artifact["schema_version"] != 1
            or artifact["contract"] != "ReadProviderRegistryRelease/v1"
            or artifact["release_id"] != release_verification.release_id
            or not isinstance(artifact["profiles"], list)
        ):
            raise ValueError("read provider release artifact contract differs")
        profiles = tuple(_load_profile(item) for item in artifact["profiles"])
        return cls(
            profiles,
            release_artifact_sha256=expected_sha256,
            release_manifest_sha256=release_verification.manifest_sha256,
            provenance_sha256=release_verification.provenance_sha256,
            release_verification_receipt_sha256=(
                release_verification.verification_receipt_sha256
            ),
            _seal=_RELEASE_SEAL,
        )

    @property
    def registry_sha256(self) -> str:
        return self._registry_sha256

    @property
    def release_manifest_sha256(self) -> str:
        return self._release_manifest_sha256

    @property
    def provenance_sha256(self) -> str:
        return self._provenance_sha256

    @property
    def release_verification_receipt_sha256(self) -> str:
        return self._release_verification_receipt_sha256

    def resolve(self, capability_manifest_id: UUID) -> ReadProviderProfile:
        try:
            profile = self._by_manifest[capability_manifest_id]
        except KeyError as error:
            raise LookupError("read provider is absent from sealed registry") from error
        profile.process_spec.verify_command_targets()
        return profile


# The signed v1 artifact kept its historical read-specific contract name, but
# the sealed profile itself is operation-neutral.  Effect authorization uses
# these aliases without changing the already released artifact bytes.
ActionProviderProfile = ReadProviderProfile
SealedActionProviderRegistry = SealedReadProviderRegistry


def _load_profile(value: object) -> ReadProviderProfile:
    _require_keys(
        value,
        {
            "capability_manifest_id",
            "revision",
            "connector_manifest",
            "process_spec",
        },
        "provider profile",
    )
    process = value["process_spec"]
    _require_keys(
        process,
        {
            "command",
            "command_targets",
            "program_binding",
            "program_base64",
            "invoke_failure_phase",
            "fixture_selection",
        },
        "provider process spec",
    )
    command = process["command"]
    targets = process["command_targets"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
        or not isinstance(targets, list)
    ):
        raise ValueError("read provider command is invalid")
    try:
        program_bytes = base64.b64decode(process["program_base64"], validate=True)
    except (TypeError, ValueError) as error:
        raise ValueError("read provider program encoding is invalid") from error
    if base64.b64encode(program_bytes).decode("ascii") != process["program_base64"]:
        raise ValueError("read provider program encoding is not canonical")
    pins = tuple(_load_target_pin(item) for item in targets)
    fixture = _load_fixture(process["fixture_selection"])
    program_binding = _load_program_binding(process["program_binding"])
    return ReadProviderProfile(
        capability_manifest_id=UUID(value["capability_manifest_id"]),
        revision=value["revision"],
        connector_manifest=ConnectorManifest.model_validate(
            value["connector_manifest"]
        ),
        process_spec=ConnectorProcessSpec(
            command=tuple(command),
            program_bytes=program_bytes,
            invoke_failure_phase=process["invoke_failure_phase"],
            fixture_selection=fixture,
            command_target_pins=pins,
            program_binding=program_binding,
        ),
    )


def _load_target_pin(value: object) -> ConnectorCommandTargetPin:
    _require_keys(
        value,
        {"argument_index", "target_path", "target_sha256"},
        "command target pin",
    )
    return ConnectorCommandTargetPin(
        argument_index=value["argument_index"],
        target_path=Path(value["target_path"]),
        target_sha256=value["target_sha256"],
    )


def _load_program_binding(value: object) -> ConnectorProgramBinding:
    _require_keys(value, {"kind", "argument_index"}, "program binding")
    return ConnectorProgramBinding(
        kind=value["kind"],
        argument_index=value["argument_index"],
    )


def _load_fixture(value: object) -> ConnectorFixtureSelection | None:
    if value is None:
        return None
    _require_keys(
        value,
        {
            "artifact_path",
            "artifact_sha256",
            "worker_key",
            "case_id",
            "current_state_sha256",
        },
        "fixture selection",
    )
    return ConnectorFixtureSelection(
        artifact_path=Path(value["artifact_path"]),
        artifact_sha256=value["artifact_sha256"],
        worker_key=value["worker_key"],
        case_id=value["case_id"],
        current_state_sha256=value["current_state_sha256"],
    )


def _require_keys(value: object, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"read provider {label} fields differ")


__all__ = [
    "ActionProviderProfile",
    "ReadProviderProfile",
    "SealedActionProviderRegistry",
    "SealedReadProviderRegistry",
]
