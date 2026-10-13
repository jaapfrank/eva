#!/usr/bin/env python3
"""Verify the sealed Step 3 sources used by the Eva 2 tranche 4A gates.

The release manifest is deliberately not its own authority.  This verifier pins
the complete expected path/digest set in code and then checks both the manifest
and the referenced repository bytes.  It performs no network, database, or
service operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


CONTRACT = "Eva2DesignSourceBindings/v1"
VERIFICATION_CONTRACT = "Eva2SourceBindingVerification/v1"
DEFAULT_MANIFEST = "release/design-source-bindings.json"
EXPECTED_BINDINGS: dict[str, str] = {
    "backup/history/root/design/evidence/eva2-first-vertical-contract-2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json": (
        "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1"
    ),
    "backup/history/root/design/evidence/eva2-step3-design-freeze-cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33.json": (
        "cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33"
    ),
    "backup/history/root/design/fixtures/noor-client-ack-key-v1.json": (
        "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93"
    ),
    "backup/history/root/docs/adr-0001-eva2-living-core.md": (
        "73b53e392b8e3b65d1241bff46bb92144960a2620dbd45f3c9d3526d9f07910d"
    ),
    "backup/history/root/docs/eva1-to-eva2-transition-manifest-2026-08-20.md": (
        "7ea53e81e6fb3af0304cf88119abc9bf864e1a9dbf1445bfaed6dbd18d3f8d85"
    ),
    "backup/history/root/docs/eva2-build-map-2026-08-20.md": (
        "188731302df263427ad499102e0d2be9ca11bf5321d81959d492d4604243fd3b"
    ),
    "backup/history/root/docs/eva2-build-map-core-contracts.md": (
        "877a0d25b637ef0f3bbfe3286ab40b5c40d7a0e50dbc3bf4af14504426bf0d63"
    ),
    "backup/history/root/docs/eva2-build-map-platform.md": (
        "244b2f2e397d4ce11e245d573b266dee443f77b85d0bd456d7e4b9cd66fa98f1"
    ),
    "backup/history/root/docs/eva2-build-map-presence-evidence.md": (
        "69d391ba4e3b3363c4d1d3cb6471c1510b68e974f5d8e61088356b6020328ed8"
    ),
    "backup/history/root/transition/evidence/eva1-to-eva2-transition-manifest-c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c.json": (
        "c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c"
    ),
}
EXPECTED_BINDING_IDS: dict[str, str] = {
    "backup/history/root/design/evidence/eva2-first-vertical-contract-2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json": "first_vertical_contract",
    "backup/history/root/design/evidence/eva2-step3-design-freeze-cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33.json": "step3_4a_authorization_freeze",
    "backup/history/root/design/fixtures/noor-client-ack-key-v1.json": "public_client_ack_fixture",
    "backup/history/root/docs/adr-0001-eva2-living-core.md": "architecture_decision",
    "backup/history/root/docs/eva1-to-eva2-transition-manifest-2026-08-20.md": "transition_disposition",
    "backup/history/root/docs/eva2-build-map-2026-08-20.md": "step3_master_build_map",
    "backup/history/root/docs/eva2-build-map-core-contracts.md": "step3_core_contracts_map",
    "backup/history/root/docs/eva2-build-map-platform.md": "step3_platform_map",
    "backup/history/root/docs/eva2-build-map-presence-evidence.md": "step3_presence_evidence_map",
    "backup/history/root/transition/evidence/eva1-to-eva2-transition-manifest-c6fff926d7f6b2f199081a91dac6cfcaf4e5c2c750159d6bf2dc068bae670c1c.json": "transition_evidence",
}


class BindingError(ValueError):
    """A deterministic, user-reportable source-binding error."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical JSON representation used by 4A evidence."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_exact(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except UnicodeDecodeError as exc:
        raise BindingError("manifest is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise BindingError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise BindingError("manifest root must be an object")
    return value


def _valid_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise BindingError("binding path must be a non-empty string")
    if "\\" in value or "\x00" in value:
        raise BindingError(f"binding path is not canonical: {value!r}")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or str(pure) != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise BindingError(f"binding path is not repository-relative: {value!r}")
    return value


def _valid_sha256(value: Any, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BindingError(f"binding sha256 is invalid for {path}")
    return value


def _binding_rows(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """Extract the binding list while rejecting alternate authorities."""

    raw_rows = manifest.get("bindings")
    if not isinstance(raw_rows, list):
        raise BindingError("bindings must be an array")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_ids: set[str] = set()
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, dict):
            raise BindingError(f"binding {index} must be an object")
        if set(raw_row) != {"id", "path", "sha256"}:
            raise BindingError(
                f"binding {index} must contain only id, path, and sha256"
            )
        identifier = raw_row["id"]
        if not isinstance(identifier, str) or not identifier:
            raise BindingError(f"binding {index} id must be a non-empty string")
        path = _valid_relative_path(raw_row["path"])
        digest = _valid_sha256(raw_row["sha256"], path)
        if path in seen:
            raise BindingError(f"duplicate binding path: {path}")
        if identifier in seen_ids:
            raise BindingError(f"duplicate binding id: {identifier}")
        seen.add(path)
        seen_ids.add(identifier)
        rows.append({"id": identifier, "path": path, "sha256": digest})
    return rows


def _safe_regular_file(root: Path, relative_path: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise BindingError(f"source path contains a symlink: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BindingError(f"source file is missing: {relative_path}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise BindingError(
            f"source path is not a regular repository file: {relative_path}"
        )
    return resolved


def _manifest_location(root: Path, manifest_path: Path | None) -> tuple[Path, str]:
    candidate = manifest_path or root / DEFAULT_MANIFEST
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = _valid_relative_path(candidate.relative_to(root).as_posix())
    except ValueError:
        raise BindingError(
            "source-binding manifest must be a regular file inside the repository"
        )
    try:
        resolved = _safe_regular_file(root, relative)
    except BindingError as exc:
        if "missing" in str(exc):
            raise BindingError(
                f"source-binding manifest is missing: {DEFAULT_MANIFEST}"
            ) from exc
        raise
    return resolved, relative


def binding_fingerprint(rows: Sequence[Mapping[str, str]]) -> str:
    normalized = [
        {
            **({"id": row["id"]} if "id" in row else {}),
            "path": row["path"],
            "sha256": row["sha256"],
        }
        for row in sorted(rows, key=lambda row: row["path"])
    ]
    return sha256_bytes(canonical_json_bytes(normalized))


def verify(
    root: Path,
    manifest_path: Path | None = None,
    *,
    expected_bindings: Mapping[str, str] | None = None,
    expected_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify a manifest and all source bytes, returning a stable receipt.

    ``expected_bindings`` exists for isolated unit fixtures only.  The CLI and
    production gate never expose it and always use ``EXPECTED_BINDINGS``.
    """

    root = root.resolve(strict=True)
    expected = dict(
        EXPECTED_BINDINGS if expected_bindings is None else expected_bindings
    )
    if expected_ids is None:
        identifiers = (
            dict(EXPECTED_BINDING_IDS)
            if expected_bindings is None
            else {path: path for path in expected}
        )
    else:
        identifiers = dict(expected_ids)
    if set(identifiers) != set(expected):
        raise BindingError("expected id and digest path sets differ")
    errors: list[str] = []
    rows: list[dict[str, str]] = []
    manifest_relative = DEFAULT_MANIFEST
    manifest_digest: str | None = None

    try:
        manifest, manifest_relative = _manifest_location(root, manifest_path)
        manifest_digest = sha256_file(manifest)
        value = load_json_exact(manifest)
        expected_root_keys = {
            "bindingCount",
            "bindings",
            "contract",
            "legacyInputsAllowed",
            "pathPolicy",
            "secretsAllowed",
        }
        if set(value) != expected_root_keys:
            errors.append("manifest root fields are not exact")
        if value.get("contract") != CONTRACT:
            errors.append(f"contract must be {CONTRACT}")
        if value.get("pathPolicy") != "repository_relative_only":
            errors.append("pathPolicy must be repository_relative_only")
        if value.get("secretsAllowed") is not False:
            errors.append("secretsAllowed must be false")
        if value.get("legacyInputsAllowed") is not False:
            errors.append("legacyInputsAllowed must be false")
        rows = _binding_rows(value)
        if value.get("bindingCount") != len(rows):
            errors.append("bindingCount does not match bindings")
    except (BindingError, OSError) as exc:
        errors.append(str(exc))

    actual = {row["path"]: row["sha256"] for row in rows}
    actual_ids = {row["path"]: row["id"] for row in rows}
    for path in sorted(set(expected) - set(actual)):
        errors.append(f"missing expected binding: {path}")
    for path in sorted(set(actual) - set(expected)):
        errors.append(f"unexpected binding: {path}")
    for path in sorted(set(actual) & set(expected)):
        if actual[path] != expected[path]:
            errors.append(f"manifest digest mismatch for {path}")
        if actual_ids[path] != identifiers[path]:
            errors.append(f"manifest id mismatch for {path}")

    if not errors:
        for path, expected_digest in sorted(expected.items()):
            try:
                source = _safe_regular_file(root, path)
                actual_digest = sha256_file(source)
                if actual_digest != expected_digest:
                    errors.append(f"source digest mismatch for {path}")
            except (BindingError, OSError) as exc:
                errors.append(str(exc))

    normalized_rows = [
        {"id": actual_ids[path], "path": path, "sha256": digest}
        for path, digest in sorted(actual.items())
    ]
    result: dict[str, Any] = {
        "contract": VERIFICATION_CONTRACT,
        "status": "passed" if not errors else "failed",
        "expectedCount": len(expected),
        "actualCount": len(rows),
        "manifestPath": manifest_relative,
        "manifestSha256": manifest_digest,
        "bindingsFingerprint": binding_fingerprint(normalized_rows),
        "errors": sorted(set(errors)),
    }
    if not errors:
        result["verifiedBindings"] = normalized_rows
    return result


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, args.manifest)
    except (BindingError, OSError) as exc:
        result = {
            "contract": VERIFICATION_CONTRACT,
            "status": "failed",
            "expectedCount": len(EXPECTED_BINDINGS),
            "actualCount": 0,
            "manifestPath": DEFAULT_MANIFEST,
            "manifestSha256": None,
            "bindingsFingerprint": binding_fingerprint([]),
            "errors": [str(exc)],
        }
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
