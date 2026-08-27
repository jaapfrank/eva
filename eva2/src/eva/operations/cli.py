"""Small host CLI surface for Eva installation and offline identity operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from eva.application.conversation import load_current_identity_pack
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.contracts.canonical_json import require_canonical_bytes
from eva.contracts.identity import IdentityPackDraft
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    activate_identity_successor,
    issue_identity_pack,
    load_private_ed25519_seed,
    rollback_identity_successor,
    write_identity_candidate,
)
from eva.operations.ab_slots import ABSlotStore, state_payload, state_sha256


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
    identity_issue = commands.add_parser(
        "identity-issue", help="offline sign one staged identity proposal"
    )
    identity_issue.add_argument("--base", type=Path, required=True)
    identity_issue.add_argument("--draft", type=Path, required=True)
    identity_issue.add_argument("--private-key-file", type=Path, required=True)
    identity_issue.add_argument("--issued-at", required=True)
    identity_issue.add_argument("--output", type=Path, required=True)
    identity_issue.add_argument("--trust-file", type=Path, required=True)
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


def _identity_draft(path: Path) -> IdentityPackDraft:
    try:
        return IdentityPackDraft.model_validate(require_canonical_bytes(path.read_bytes()))
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


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    identity_commands = {
        "identity-issue": _identity_issue,
        "identity-activate": _identity_activate,
        "identity-rollback": _identity_rollback,
    }
    operation = identity_commands.get(arguments.command)
    if operation is not None:
        try:
            print(_encode(operation(arguments)))
        except (IdentityPackLifecycleError, ValueError):
            print(_encode({"error": "identity operation rejected", "status": "rejected"}))
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
    diagnosis = store.diagnose()
    print(
        _encode(
            {
                "status": "passed" if diagnosis.healthy else "failed",
                "errors": list(diagnosis.errors),
                "state_sha256": (
                    None if diagnosis.state is None else state_sha256(diagnosis.state)
                ),
            }
        )
    )
    return 0 if diagnosis.healthy else 2


if __name__ == "__main__":
    sys.exit(main())
