"""Bind confirmed installer inputs without provisioning or transforming credentials."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.installer_secret_store import InstallerSecretStore
from eva.operations.release_verifier import ReleaseVerification
from eva.operations.installer_development_candidate import InstallerDevelopmentCandidate


@dataclass(frozen=True, slots=True)
class RuntimeInputReceipt:
    plan_sha256: str
    manifest_sha256: str
    image_set_sha256: str
    verification_receipt_sha256: str
    images: tuple[tuple[str, str], ...]
    secret_bindings: tuple[tuple[str, str, int], ...]
    authority_kind: str = "verified_release"


@dataclass(frozen=True, slots=True)
class RuntimeSecretInput:
    slot_id: str
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class InstallerRuntimeInputs:
    receipt: RuntimeInputReceipt
    secrets: tuple[RuntimeSecretInput, ...] = field(repr=False)


def bind_installer_runtime_inputs(
    *,
    plan: dict[str, Any],
    expected_plan_sha256: str,
    release_manifest: dict[str, Any],
    release_verification: ReleaseVerification,
    secret_store: InstallerSecretStore,
) -> InstallerRuntimeInputs:
    """Resolve exact current generations for an installer-owned consumer.

    The caller must rebuild the current plan and supply its trusted expected
    hash; a browser-provided plan and hash are not confirmation authority.
    This is a point-in-time binding, not a release verification replacement or
    a guarantee that provider credentials remain valid upstream. Consumers must
    bind again before provisioning if the confirmed inputs have changed.
    """
    plan = deepcopy(plan)
    release_manifest = deepcopy(release_manifest)
    if not isinstance(release_verification, (ReleaseVerification, InstallerDevelopmentCandidate)):
        raise ValueError("verified release is required")
    release_verification.require_verified()
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if (
        plan.get("contract") != "EvaInstallerInstallationPlan/v2"
        or plan.get("status") != "ready_for_release_preparation"
        or plan.get("blockers") != []
        or plan.get("plan_sha256") != expected_plan_sha256
        or canonical_sha256(body) != expected_plan_sha256
    ):
        raise ValueError("confirmed installation plan differs or is not ready")
    if canonical_sha256(release_manifest) != release_verification.manifest_sha256:
        raise ValueError("release manifest differs from verified release")
    images = release_manifest["imageDigests"]
    if canonical_sha256(images) != release_verification.image_set_sha256:
        raise ValueError("release image set differs from verified release")
    bindings = plan.get("inputs", {}).get("secret_bindings")
    if not isinstance(bindings, list):
        raise ValueError("installation plan secret bindings are invalid")
    resolved = []
    public = []
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"slot_id", "reference", "generation"}:
            raise ValueError("installation plan secret binding is invalid")
        slot = binding["slot_id"]
        if not isinstance(slot, str) or slot in seen:
            raise ValueError("installation plan secret slot is invalid or duplicated")
        seen.add(slot)
        status, content = secret_store.read_secret_with_status(slot_id=slot)
        if (
            status.state != "present"
            or status.slot_id != slot
            or status.reference != binding["reference"]
            or type(binding["generation"]) is not int
            or status.generation != binding["generation"]
        ):
            raise ValueError("installation secret changed since plan confirmation")
        public.append((slot, status.reference, status.generation))
        resolved.append(RuntimeSecretInput(slot, content))
    return InstallerRuntimeInputs(
        receipt=RuntimeInputReceipt(
            plan_sha256=expected_plan_sha256,
            manifest_sha256=release_verification.manifest_sha256,
            image_set_sha256=release_verification.image_set_sha256,
            verification_receipt_sha256=release_verification.verification_receipt_sha256,
            images=tuple(sorted(images.items())),
            secret_bindings=tuple(public),
            authority_kind=("owner_local_development" if isinstance(
                release_verification, InstallerDevelopmentCandidate) else "verified_release"),
        ),
        secrets=tuple(resolved),
    )
