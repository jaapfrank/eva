"""Fail-closed operator CLI for Eva A/B releases and offline identity changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.contracts.canonical_json import require_canonical_bytes
from eva.contracts.identity import IdentityPackDraft
from eva.operations.ab_slots import (
    ABInstallationState,
    ABSlotError,
    ABSlotStore,
    state_payload,
    state_sha256,
)
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    activate_identity_successor,
    bootstrap_identity_authority,
    issue_identity_pack,
    load_private_ed25519_seed,
    rollback_identity_successor,
    write_identity_candidate,
)
from eva.operations.release_verifier import (
    ReleaseVerificationError,
    verify_release_manifest,
)


_MAX_AUTHORITY_BYTES = 4 * 1024 * 1024


def _encode(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eva")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "diagnose"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--root", type=Path, required=True)
    initialize.add_argument("--installation-id", required=True)
    initialize.add_argument("--database-lineage-id", required=True)
    initialize.add_argument("--database-schema-revision", required=True)
    initialize.add_argument("--at", required=True)

    stage = commands.add_parser("verify-stage")
    stage.add_argument("--root", type=Path, required=True)
    stage.add_argument("--slot", choices=("a", "b"), required=True)
    stage.add_argument("--manifest", type=Path, required=True)
    stage.add_argument("--trust-root", type=Path, required=True)
    stage.add_argument("--verified-at", required=True)
    stage.add_argument("--expected-revision", type=int, required=True)
    stage.add_argument("--revoked-subject-id", action="append", default=[])

    activate = commands.add_parser("activate")
    activate.add_argument("--root", type=Path, required=True)
    activate.add_argument("--slot", choices=("a", "b"), required=True)
    activate.add_argument("--activation-receipt-sha256", required=True)
    activate.add_argument("--expected-revision", type=int, required=True)
    activate.add_argument("--at", required=True)

    traffic = commands.add_parser("traffic")
    traffic.add_argument("--root", type=Path, required=True)
    traffic.add_argument(
        "--state", choices=("closed", "draining", "open"), required=True
    )
    traffic.add_argument("--expected-revision", type=int, required=True)
    traffic.add_argument("--at", required=True)

    migration = commands.add_parser("record-expand-migration")
    migration.add_argument("--root", type=Path, required=True)
    migration.add_argument("--database-schema-revision", required=True)
    migration.add_argument("--migration-receipt-sha256", required=True)
    migration.add_argument("--expected-revision", type=int, required=True)
    migration.add_argument("--at", required=True)
    identity_issue = commands.add_parser(
        "identity-issue", help="offline sign one staged identity proposal"
    )
    identity_issue.add_argument("--base", type=Path, required=True)
    identity_issue.add_argument("--draft", type=Path, required=True)
    identity_issue.add_argument("--private-key-file", type=Path, required=True)
    identity_issue.add_argument("--issued-at", required=True)
    identity_issue.add_argument("--output", type=Path, required=True)
    identity_issue.add_argument("--trust-file", type=Path, required=True)
    identity_bootstrap = commands.add_parser(
        "identity-bootstrap",
        help="create one fresh offline identity signer and signed current pack",
    )
    identity_bootstrap.add_argument("--template", type=Path, required=True)
    identity_bootstrap.add_argument("--template-trust-file", type=Path, required=True)
    identity_bootstrap.add_argument("--draft", type=Path, required=True)
    identity_bootstrap.add_argument("--identity-id", required=True)
    identity_bootstrap.add_argument("--issuer", required=True)
    identity_bootstrap.add_argument("--issued-at", required=True)
    identity_bootstrap.add_argument("--output-directory", type=Path, required=True)
    identity_activate = commands.add_parser(
        "identity-activate", help="atomically activate one signed identity candidate"
    )
    identity_activate.add_argument("--candidate", type=Path, required=True)
    identity_activate.add_argument("--current", type=Path, required=True)
    identity_activate.add_argument("--rollback", type=Path, required=True)
    identity_activate.add_argument("--trust-file", type=Path, required=True)
    identity_rollback = commands.add_parser(
        "identity-rollback", help="restore the directly previous signed identity"
    )
    identity_rollback.add_argument("--current", type=Path, required=True)
    identity_rollback.add_argument("--rollback", type=Path, required=True)
    identity_rollback.add_argument("--trust-file", type=Path, required=True)
    return parser


def _authority_bytes(path: Path, label: str) -> bytes:
    input_info = Path(path).lstat()
    if not stat.S_ISREG(input_info.st_mode) or stat.S_ISLNK(input_info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    absolute = Path(path).resolve(strict=True)
    info = absolute.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if info.st_size < 2 or info.st_size > _MAX_AUTHORITY_BYTES:
        raise ValueError(f"{label} has an invalid size")
    return absolute.read_bytes()


def _identity_draft(path: Path) -> IdentityPackDraft:
    try:
        return IdentityPackDraft.model_validate(
            require_canonical_bytes(path.read_bytes())
        )
    except (OSError, ValueError) as exc:
        raise IdentityPackLifecycleError("identity draft is invalid") from exc


def _identity_issue(arguments: argparse.Namespace) -> dict[str, object]:
    trust = load_identity_pack_trust_binding(arguments.trust_file)
    base = load_current_identity_pack(arguments.base, trust=trust)
    candidate = issue_identity_pack(
        _identity_draft(arguments.draft),
        base,
        private_key_bytes=load_private_ed25519_seed(arguments.private_key_file),
        issued_at=arguments.issued_at,
    )
    return {
        "candidate_sha256": write_identity_candidate(arguments.output, candidate),
        "pack_version": candidate.pack_version,
        "status": "issued",
    }


def _identity_bootstrap(arguments: argparse.Namespace) -> dict[str, object]:
    trust = load_identity_pack_trust_binding(arguments.template_trust_file)
    template = load_current_identity_pack(arguments.template, trust=trust)
    result = bootstrap_identity_authority(
        output_directory=arguments.output_directory,
        template=template,
        draft=_identity_draft(arguments.draft),
        identity_id=arguments.identity_id,
        issuer=arguments.issuer,
        issued_at=arguments.issued_at,
    )
    return {
        "current_sha256": result.current_sha256,
        "pack_version": result.pack_version,
        "status": "bootstrapped",
        "trust_sha256": result.trust_sha256,
    }


def _identity_activate(arguments: argparse.Namespace) -> dict[str, object]:
    cutover = activate_identity_successor(
        current_path=arguments.current,
        candidate_path=arguments.candidate,
        rollback_path=arguments.rollback,
        trust=load_identity_pack_trust_binding(arguments.trust_file),
    )
    return {
        "current_sha256": cutover.current_sha256,
        "previous_sha256": cutover.previous_sha256,
        "rollback_sha256": cutover.rollback_sha256,
        "status": "activated",
    }


def _identity_rollback(arguments: argparse.Namespace) -> dict[str, object]:
    cutover = rollback_identity_successor(
        current_path=arguments.current,
        rollback_path=arguments.rollback,
        trust=load_identity_pack_trust_binding(arguments.trust_file),
    )
    return {
        "current_sha256": cutover.current_sha256,
        "previous_sha256": cutover.previous_sha256,
        "rollback_sha256": cutover.rollback_sha256,
        "status": "rolled_back",
    }


def _state_receipt(action: str, state: ABInstallationState) -> dict[str, object]:
    return {
        "action": action,
        "contract": "EvaABOperatorReceipt/v1",
        "state": state_payload(state),
        "state_sha256": state_sha256(state),
        "status": "passed",
    }


def _run_ab_operation(
    arguments: argparse.Namespace, store: ABSlotStore
) -> dict[str, object]:
    if arguments.command == "init":
        state = store.initialize(
            installation_id=arguments.installation_id,
            database_lineage_id=arguments.database_lineage_id,
            database_schema_revision=arguments.database_schema_revision,
            initialized_at=arguments.at,
        )
        return _state_receipt("initialized", state)
    if arguments.command == "verify-stage":
        verified = verify_release_manifest(
            _authority_bytes(arguments.manifest, "release manifest"),
            _authority_bytes(arguments.trust_root, "trust root"),
            verified_at=arguments.verified_at,
            revoked_subject_ids=tuple(arguments.revoked_subject_id),
        )
        state = store.stage_release(
            arguments.slot,
            verified.for_slot(),
            expected_revision=arguments.expected_revision,
            updated_at=arguments.verified_at,
        )
        return {
            **_state_receipt("signature_verified_and_staged", state),
            "release": {
                "image_set_sha256": verified.image_set_sha256,
                "manifest_sha256": verified.manifest_sha256,
                "release_id": verified.release_id,
                "release_version": verified.release_version,
                "signer_key_ids": list(verified.signer_key_ids),
                "verification_receipt_sha256": verified.verification_receipt_sha256,
            },
            "slot": arguments.slot,
        }
    if arguments.command == "activate":
        state = store.activate(
            arguments.slot,
            activation_receipt_sha256=arguments.activation_receipt_sha256,
            expected_revision=arguments.expected_revision,
            updated_at=arguments.at,
        )
        return _state_receipt("slot_activated_with_traffic_closed", state)
    if arguments.command == "traffic":
        state = store.set_traffic(
            arguments.state,
            expected_revision=arguments.expected_revision,
            updated_at=arguments.at,
        )
        return _state_receipt(f"traffic_{arguments.state}", state)
    state = store.record_expand_migration(
        arguments.database_schema_revision,
        migration_receipt_sha256=arguments.migration_receipt_sha256,
        expected_revision=arguments.expected_revision,
        updated_at=arguments.at,
    )
    return _state_receipt("expand_migration_recorded", state)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    identity_commands = {
        "identity-issue": _identity_issue,
        "identity-bootstrap": _identity_bootstrap,
        "identity-activate": _identity_activate,
        "identity-rollback": _identity_rollback,
    }
    operation = identity_commands.get(arguments.command)
    if operation is not None:
        try:
            print(_encode(operation(arguments)))
        except (IdentityPackLifecycleError, ValueError):
            print(
                _encode({"error": "identity operation rejected", "status": "rejected"})
            )
            return 2
        return 0
    store = ABSlotStore(arguments.root)
    if arguments.command == "status":
        try:
            state = store.load()
        except (OSError, RuntimeError, ValueError) as exc:
            print(_encode({"status": "unavailable", "error": str(exc)}))
            return 2
        print(
            _encode(
                {
                    "status": "available",
                    "state": state_payload(state),
                    "state_sha256": state_sha256(state),
                }
            )
        )
        return 0
    if arguments.command == "diagnose":
        diagnosis = store.diagnose()
        print(
            _encode(
                {
                    "status": "passed" if diagnosis.healthy else "failed",
                    "errors": list(diagnosis.errors),
                    "state_sha256": (
                        None
                        if diagnosis.state is None
                        else state_sha256(diagnosis.state)
                    ),
                }
            )
        )
        return 0 if diagnosis.healthy else 2
    try:
        receipt = _run_ab_operation(arguments, store)
    except (ABSlotError, ReleaseVerificationError, OSError, ValueError) as error:
        print(_encode({"error": str(error), "status": "failed"}))
        return 2
    print(_encode(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
