"""Fail-closed host CLI for Eva installation and signed A/B release state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys

from eva.operations.ab_slots import (
    ABInstallationState,
    ABSlotError,
    ABSlotStore,
    state_payload,
    state_sha256,
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
    return parser


def _authority_bytes(path: Path, label: str) -> bytes:
    absolute = Path(path).resolve(strict=True)
    info = absolute.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if info.st_size < 2 or info.st_size > _MAX_AUTHORITY_BYTES:
        raise ValueError(f"{label} has an invalid size")
    return absolute.read_bytes()


def _state_receipt(action: str, state: ABInstallationState) -> dict[str, object]:
    return {
        "action": action,
        "contract": "EvaABOperatorReceipt/v1",
        "state": state_payload(state),
        "state_sha256": state_sha256(state),
        "status": "passed",
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
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
        if arguments.command == "init":
            state = store.initialize(
                installation_id=arguments.installation_id,
                database_lineage_id=arguments.database_lineage_id,
                database_schema_revision=arguments.database_schema_revision,
                initialized_at=arguments.at,
            )
            receipt = _state_receipt("initialized", state)
        elif arguments.command == "verify-stage":
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
            receipt = {
                **_state_receipt("signature_verified_and_staged", state),
                "release": {
                    "image_set_sha256": verified.image_set_sha256,
                    "manifest_sha256": verified.manifest_sha256,
                    "release_id": verified.release_id,
                    "release_version": verified.release_version,
                    "signer_key_ids": list(verified.signer_key_ids),
                    "verification_receipt_sha256": (
                        verified.verification_receipt_sha256
                    ),
                },
                "slot": arguments.slot,
            }
        elif arguments.command == "activate":
            state = store.activate(
                arguments.slot,
                activation_receipt_sha256=arguments.activation_receipt_sha256,
                expected_revision=arguments.expected_revision,
                updated_at=arguments.at,
            )
            receipt = _state_receipt("slot_activated_with_traffic_closed", state)
        elif arguments.command == "traffic":
            state = store.set_traffic(
                arguments.state,
                expected_revision=arguments.expected_revision,
                updated_at=arguments.at,
            )
            receipt = _state_receipt(f"traffic_{arguments.state}", state)
        else:
            state = store.record_expand_migration(
                arguments.database_schema_revision,
                migration_receipt_sha256=arguments.migration_receipt_sha256,
                expected_revision=arguments.expected_revision,
                updated_at=arguments.at,
            )
            receipt = _state_receipt("expand_migration_recorded", state)
    except (
        ABSlotError,
        ReleaseVerificationError,
        OSError,
        ValueError,
    ) as error:
        print(_encode({"error": str(error), "status": "failed"}))
        return 2
    print(_encode(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
