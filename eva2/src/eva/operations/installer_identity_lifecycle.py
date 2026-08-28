"""Private, restart-safe IdentityPack workflow for the local installer.

The browser only submits a small ``SOUL.md`` and exact public hashes.  This
operator-side controller owns the private file boundary and reuses the
existing offline IdentityPack issuer and atomic cutover implementation.  It
never returns identity contents or private-key material.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Callable

from eva.application.identity_drafts import identity_presentation_diff
from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    load_identity_pack_trust_binding,
    verify_identity_pack_bytes,
)
from eva.application.soul_markdown import soul_markdown_sha256, soul_markdown_to_draft
from eva.contracts.canonical_json import (
    canonical_json_bytes,
    canonical_sha256,
    require_canonical_bytes,
)
from eva.contracts.identity import IdentityPack, IdentityPackDraft
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    activate_identity_successor,
    issue_identity_pack,
    load_private_ed25519_seed,
    rollback_identity_successor,
    validate_identity_successor,
    write_identity_candidate,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_ARTIFACT_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class InstallerIdentityStage:
    draft_sha256: str
    source_sha256: str
    base_pack_sha256: str
    changed_fields: tuple[str, ...]
    already_staged: bool


@dataclass(frozen=True, slots=True)
class InstallerIdentityCandidate:
    draft_sha256: str
    candidate_sha256: str
    base_pack_sha256: str
    pack_version: int
    already_issued: bool


@dataclass(frozen=True, slots=True)
class InstallerIdentityTransition:
    status: str
    previous_sha256: str
    current_sha256: str
    rollback_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IdentityPackLifecycleError(f"{label} must be a canonical SHA-256")
    return value


def _read_private_artifact(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IdentityPackLifecycleError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_ARTIFACT_BYTES
    ):
        raise IdentityPackLifecycleError(f"{label} is not a private regular file")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            raw = handle.read(_MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise IdentityPackLifecycleError(f"{label} is unavailable") from exc
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise IdentityPackLifecycleError(f"{label} is too large")
    return raw


def _write_private_once(path: Path, raw: bytes) -> bool:
    """Write exact bytes once; an identical retained artifact is idempotent."""

    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        retained = _read_private_artifact(path, label="installer identity artifact")
        if not hmac.compare_digest(retained, raw):
            raise IdentityPackLifecycleError("installer identity artifact conflicts")
        return True
    except OSError as exc:
        raise IdentityPackLifecycleError(
            "installer identity artifact write failed"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise IdentityPackLifecycleError(
            "installer identity artifact write failed"
        ) from exc
    return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class InstallerIdentityLifecycle:
    """Bind one local wizard to one private, signed identity authority."""

    current_path: Path
    trust_path: Path
    private_key_path: Path
    workspace: Path
    clock: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        try:
            metadata = self.workspace.lstat()
        except OSError as exc:
            raise IdentityPackLifecycleError(
                "installer identity workspace is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise IdentityPackLifecycleError(
                "installer identity workspace must be an owner-only directory"
            )
        paths = {
            self.current_path.absolute(),
            self.trust_path.absolute(),
            self.private_key_path.absolute(),
            self.workspace.absolute(),
        }
        if len(paths) != 4:
            raise IdentityPackLifecycleError(
                "installer identity paths must be distinct"
            )
        # Fail before serving the wizard if its public identity authority is bad.
        self.current_identity()

    def _trust(self) -> IdentityPackTrustBinding:
        try:
            return load_identity_pack_trust_binding(self.trust_path)
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "installer identity trust binding is invalid"
            ) from exc

    def current_identity(self) -> IdentityPack:
        raw = _read_private_artifact(self.current_path, label="current identity pack")
        try:
            return verify_identity_pack_bytes(raw, trust=self._trust())
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "current identity pack is invalid"
            ) from exc

    def _draft_path(self, draft_sha256: str) -> Path:
        return self.workspace / f"draft-{draft_sha256}.json"

    def _candidate_path(self, candidate_sha256: str) -> Path:
        return self.workspace / f"candidate-{candidate_sha256}.json"

    def _issue_intent_path(self, draft_sha256: str) -> Path:
        return self.workspace / f"issue-{draft_sha256}.json"

    @property
    def rollback_path(self) -> Path:
        return self.workspace / "rollback.json"

    def _load_rollback(self) -> tuple[IdentityPack, bytes]:
        raw = _read_private_artifact(self.rollback_path, label="identity rollback pack")
        try:
            return verify_identity_pack_bytes(raw, trust=self._trust()), raw
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "identity rollback pack is invalid"
            ) from exc

    def _load_draft(self, draft_sha256: object) -> IdentityPackDraft:
        digest = _require_sha256(draft_sha256, label="identity draft hash")
        raw = _read_private_artifact(
            self._draft_path(digest), label="staged identity draft"
        )
        if not hmac.compare_digest(_sha256(raw), digest):
            raise IdentityPackLifecycleError("staged identity draft hash differs")
        try:
            return IdentityPackDraft.model_validate(require_canonical_bytes(raw))
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "staged identity draft is invalid"
            ) from exc

    def _load_candidate(
        self, candidate_sha256: object
    ) -> tuple[IdentityPack, Path, bytes]:
        digest = _require_sha256(candidate_sha256, label="identity candidate hash")
        path = self._candidate_path(digest)
        raw = _read_private_artifact(path, label="signed identity candidate")
        if not hmac.compare_digest(_sha256(raw), digest):
            raise IdentityPackLifecycleError("signed identity candidate hash differs")
        try:
            candidate = verify_identity_pack_bytes(raw, trust=self._trust())
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "signed identity candidate is invalid"
            ) from exc
        return candidate, path, raw

    def stage(
        self,
        *,
        markdown: str,
        draft_id: str,
        expected_base_sha256: object,
    ) -> InstallerIdentityStage:
        expected = _require_sha256(
            expected_base_sha256, label="expected identity base hash"
        )
        base = self.current_identity()
        actual_base = canonical_sha256(base)
        if not hmac.compare_digest(actual_base, expected):
            raise IdentityPackLifecycleError("current identity changed after preview")
        draft = soul_markdown_to_draft(markdown, draft_id=draft_id, base=base)
        raw = canonical_json_bytes(draft)
        digest = _sha256(raw)
        already_staged = _write_private_once(self._draft_path(digest), raw)
        changes = identity_presentation_diff(draft, base)
        return InstallerIdentityStage(
            draft_sha256=digest,
            source_sha256=soul_markdown_sha256(markdown),
            base_pack_sha256=actual_base,
            changed_fields=tuple(item.field for item in changes),
            already_staged=already_staged,
        )

    def issue(
        self, *, draft_sha256: object, expected_base_sha256: object
    ) -> InstallerIdentityCandidate:
        expected = _require_sha256(
            expected_base_sha256, label="expected identity base hash"
        )
        base = self.current_identity()
        actual_base = canonical_sha256(base)
        if not hmac.compare_digest(actual_base, expected):
            raise IdentityPackLifecycleError("current identity changed after preview")
        draft_digest = _require_sha256(draft_sha256, label="identity draft hash")
        draft = self._load_draft(draft_digest)
        issue_intent_path = self._issue_intent_path(draft_digest)
        issued_at = self.clock()
        issue_intent = canonical_json_bytes(
            {
                "contract": "EvaInstallerIdentityIssueIntent/v1",
                "draft_sha256": draft_digest,
                "issued_at": issued_at,
            }
        )
        if issue_intent_path.exists() or issue_intent_path.is_symlink():
            already_issued = True
            try:
                retained_intent = require_canonical_bytes(
                    _read_private_artifact(
                        issue_intent_path, label="identity issue intent"
                    )
                )
            except ValueError as exc:
                raise IdentityPackLifecycleError(
                    "identity issue intent is invalid"
                ) from exc
            if (
                set(retained_intent) != {"contract", "draft_sha256", "issued_at"}
                or retained_intent.get("contract")
                != "EvaInstallerIdentityIssueIntent/v1"
                or retained_intent.get("draft_sha256") != draft_digest
                or not isinstance(retained_intent.get("issued_at"), str)
            ):
                raise IdentityPackLifecycleError("identity issue intent is invalid")
            issued_at = retained_intent["issued_at"]
        else:
            already_issued = False
            _write_private_once(issue_intent_path, issue_intent)
        seed = load_private_ed25519_seed(self.private_key_path)
        candidate = issue_identity_pack(
            draft,
            base,
            private_key_bytes=seed,
            issued_at=issued_at,
        )
        validate_identity_successor(candidate, base, trust=self._trust())
        raw = canonical_json_bytes(candidate)
        candidate_sha = _sha256(raw)
        candidate_path = self._candidate_path(candidate_sha)
        if candidate_path.exists() or candidate_path.is_symlink():
            retained, _path, retained_raw = self._load_candidate(candidate_sha)
            validate_identity_successor(retained, base, trust=self._trust())
            if not hmac.compare_digest(retained_raw, raw):
                raise IdentityPackLifecycleError("signed identity candidate conflicts")
            already_issued = True
        else:
            write_identity_candidate(candidate_path, candidate)
            # The issue intent may already exist after a crash before the
            # candidate write; the retained timestamp keeps the signature and
            # candidate hash deterministic across that restart.
            already_issued = False
        return InstallerIdentityCandidate(
            draft_sha256=draft_digest,
            candidate_sha256=candidate_sha,
            base_pack_sha256=actual_base,
            pack_version=candidate.pack_version,
            already_issued=already_issued,
        )

    def activate(
        self, *, candidate_sha256: object, expected_current_sha256: object
    ) -> InstallerIdentityTransition:
        candidate_digest = _require_sha256(
            candidate_sha256, label="identity candidate hash"
        )
        expected = _require_sha256(
            expected_current_sha256, label="expected current identity hash"
        )
        candidate, candidate_path, _raw = self._load_candidate(candidate_digest)
        current = self.current_identity()
        current_sha = canonical_sha256(current)
        if hmac.compare_digest(current_sha, candidate_digest):
            rollback, rollback_raw = self._load_rollback()
            if (
                rollback.identity_id != candidate.identity_id
                or rollback.issuer != candidate.issuer
                or rollback.pack_version + 1 != candidate.pack_version
            ):
                raise IdentityPackLifecycleError(
                    "identity rollback is not the active direct predecessor"
                )
            rollback_sha = _sha256(rollback_raw)
            return InstallerIdentityTransition(
                status="already_active",
                previous_sha256=rollback_sha,
                current_sha256=current_sha,
                rollback_sha256=rollback_sha,
            )
        if not hmac.compare_digest(current_sha, expected):
            raise IdentityPackLifecycleError(
                "current identity changed before activation"
            )
        validate_identity_successor(candidate, current, trust=self._trust())
        cutover = activate_identity_successor(
            current_path=self.current_path,
            candidate_path=candidate_path,
            rollback_path=self.rollback_path,
            trust=self._trust(),
        )
        return InstallerIdentityTransition(
            status="activated",
            previous_sha256=cutover.previous_sha256,
            current_sha256=cutover.current_sha256,
            rollback_sha256=cutover.rollback_sha256,
        )

    def rollback(
        self, *, expected_current_sha256: object
    ) -> InstallerIdentityTransition:
        expected = _require_sha256(
            expected_current_sha256, label="expected current identity hash"
        )
        current = self.current_identity()
        current_sha = canonical_sha256(current)
        _rollback, rollback_raw = self._load_rollback()
        rollback_sha = _sha256(rollback_raw)
        if hmac.compare_digest(current_sha, rollback_sha):
            return InstallerIdentityTransition(
                status="already_rolled_back",
                previous_sha256=expected,
                current_sha256=current_sha,
                rollback_sha256=rollback_sha,
            )
        if not hmac.compare_digest(current_sha, expected):
            raise IdentityPackLifecycleError("current identity changed before rollback")
        cutover = rollback_identity_successor(
            current_path=self.current_path,
            rollback_path=self.rollback_path,
            trust=self._trust(),
        )
        return InstallerIdentityTransition(
            status="rolled_back",
            previous_sha256=cutover.previous_sha256,
            current_sha256=cutover.current_sha256,
            rollback_sha256=cutover.rollback_sha256,
        )


__all__ = [
    "InstallerIdentityCandidate",
    "InstallerIdentityLifecycle",
    "InstallerIdentityStage",
    "InstallerIdentityTransition",
]
