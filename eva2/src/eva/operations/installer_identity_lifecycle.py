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
from pathlib import Path
import re
from typing import Callable, Literal

from eva.application.identity_drafts import (
    identity_presentation_diff,
    validate_identity_draft_base,
)
from eva.adapters.objects.identity_verifier import verify_identity_pack_bytes
from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    load_identity_pack_trust_binding,
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
from eva.operations.installer_safety import (
    owner_only_lock,
    require_owner_only_directory,
)
from eva.operations.installer_identity_storage import (
    atomic_write_private,
    read_private_artifact,
    write_private_once,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


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


IdentityWorkflowPhase = Literal["empty", "staged", "issued", "active", "rolled_back"]


@dataclass(frozen=True, slots=True)
class InstallerIdentityWorkflowStatus:
    phase: IdentityWorkflowPhase
    base_pack_sha256: str
    current_pack_sha256: str
    draft_sha256: str | None
    candidate_sha256: str | None
    updated_at: str | None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IdentityPackLifecycleError(f"{label} must be a canonical SHA-256")
    return value


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
        require_owner_only_directory(
            self.workspace,
            error=IdentityPackLifecycleError,
            unavailable_message="installer identity workspace is unavailable",
            invalid_message="installer identity workspace must be an owner-only directory",
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
        raw = read_private_artifact(self.current_path, label="current identity pack")
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

    @property
    def workflow_path(self) -> Path:
        return self.workspace / "workflow.json"

    def _workflow_lock(self):
        return owner_only_lock(
            self.workspace / ".workflow.lock",
            error=IdentityPackLifecycleError,
            unavailable_message="installer identity workflow lock is unavailable",
        )

    def _load_workflow(self) -> InstallerIdentityWorkflowStatus | None:
        if not self.workflow_path.exists() and not self.workflow_path.is_symlink():
            return None
        raw = read_private_artifact(
            self.workflow_path, label="installer identity workflow"
        )
        try:
            payload = require_canonical_bytes(raw)
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "installer identity workflow is invalid"
            ) from exc
        if (
            set(payload)
            != {
                "base_pack_sha256",
                "candidate_sha256",
                "contract",
                "current_pack_sha256",
                "draft_sha256",
                "phase",
                "updated_at",
            }
            or payload.get("contract") != "EvaInstallerIdentityWorkflow/v1"
        ):
            raise IdentityPackLifecycleError("installer identity workflow is invalid")
        phase = payload.get("phase")
        if phase not in {"staged", "issued", "active", "rolled_back"}:
            raise IdentityPackLifecycleError(
                "installer identity workflow phase is invalid"
            )
        base_sha = _require_sha256(
            payload.get("base_pack_sha256"), label="workflow base identity hash"
        )
        current_sha = _require_sha256(
            payload.get("current_pack_sha256"),
            label="workflow current identity hash",
        )
        draft_sha = _require_sha256(
            payload.get("draft_sha256"), label="workflow identity draft hash"
        )
        candidate_value = payload.get("candidate_sha256")
        candidate_sha = (
            None
            if candidate_value is None
            else _require_sha256(
                candidate_value, label="workflow identity candidate hash"
            )
        )
        updated_at = payload.get("updated_at")
        if not isinstance(updated_at, str) or not 1 <= len(updated_at) <= 64:
            raise IdentityPackLifecycleError(
                "installer identity workflow timestamp is invalid"
            )
        if (phase == "staged") != (candidate_sha is None):
            raise IdentityPackLifecycleError(
                "installer identity workflow candidate state is invalid"
            )
        return InstallerIdentityWorkflowStatus(
            phase=phase,
            base_pack_sha256=base_sha,
            current_pack_sha256=current_sha,
            draft_sha256=draft_sha,
            candidate_sha256=candidate_sha,
            updated_at=updated_at,
        )

    def _write_workflow(
        self,
        *,
        phase: Literal["staged", "issued", "active", "rolled_back"],
        base_pack_sha256: str,
        current_pack_sha256: str,
        draft_sha256: str,
        candidate_sha256: str | None,
    ) -> InstallerIdentityWorkflowStatus:
        raw = canonical_json_bytes(
            {
                "base_pack_sha256": base_pack_sha256,
                "candidate_sha256": candidate_sha256,
                "contract": "EvaInstallerIdentityWorkflow/v1",
                "current_pack_sha256": current_pack_sha256,
                "draft_sha256": draft_sha256,
                "phase": phase,
                "updated_at": self.clock(),
            }
        )
        atomic_write_private(self.workflow_path, raw)
        workflow = self._load_workflow()
        if workflow is None:
            raise IdentityPackLifecycleError(
                "installer identity workflow was not retained"
            )
        return workflow

    def _load_rollback(self) -> tuple[IdentityPack, bytes]:
        raw = read_private_artifact(self.rollback_path, label="identity rollback pack")
        try:
            return verify_identity_pack_bytes(raw, trust=self._trust()), raw
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "identity rollback pack is invalid"
            ) from exc

    def _load_draft(self, draft_sha256: object) -> IdentityPackDraft:
        digest = _require_sha256(draft_sha256, label="identity draft hash")
        raw = read_private_artifact(
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
        raw = read_private_artifact(path, label="signed identity candidate")
        if not hmac.compare_digest(_sha256(raw), digest):
            raise IdentityPackLifecycleError("signed identity candidate hash differs")
        try:
            candidate = verify_identity_pack_bytes(raw, trust=self._trust())
        except ValueError as exc:
            raise IdentityPackLifecycleError(
                "signed identity candidate is invalid"
            ) from exc
        return candidate, path, raw

    def status(self) -> InstallerIdentityWorkflowStatus:
        with self._workflow_lock():
            current = self.current_identity()
            current_sha = canonical_sha256(current)
            workflow = self._load_workflow()
            if workflow is None:
                return InstallerIdentityWorkflowStatus(
                    phase="empty",
                    base_pack_sha256=current_sha,
                    current_pack_sha256=current_sha,
                    draft_sha256=None,
                    candidate_sha256=None,
                    updated_at=None,
                )
            assert workflow.draft_sha256 is not None
            draft = self._load_draft(workflow.draft_sha256)
            candidate: IdentityPack | None = None
            base: IdentityPack
            if workflow.candidate_sha256 is None:
                if current_sha != workflow.base_pack_sha256:
                    raise IdentityPackLifecycleError(
                        "current identity differs from staged workflow"
                    )
                base = current
            else:
                candidate, _path, _raw = self._load_candidate(workflow.candidate_sha256)
                if current_sha == workflow.base_pack_sha256:
                    base = current
                elif current_sha == workflow.candidate_sha256:
                    base, base_raw = self._load_rollback()
                    if _sha256(base_raw) != workflow.base_pack_sha256:
                        raise IdentityPackLifecycleError(
                            "identity rollback differs from workflow base"
                        )
                else:
                    raise IdentityPackLifecycleError(
                        "current identity differs from resumable workflow"
                    )
            if candidate is not None:
                validate_identity_successor(candidate, base, trust=self._trust())
            try:
                validate_identity_draft_base(draft, base)
            except ValueError as exc:
                raise IdentityPackLifecycleError(
                    "staged identity draft differs from workflow base"
                ) from exc
            expected_retained_current = (
                workflow.candidate_sha256
                if workflow.phase == "active"
                else workflow.base_pack_sha256
            )
            if workflow.current_pack_sha256 != expected_retained_current:
                raise IdentityPackLifecycleError(
                    "installer identity workflow retained current hash differs"
                )
            allowed_actual = {
                "staged": {workflow.base_pack_sha256},
                "issued": {
                    workflow.base_pack_sha256,
                    workflow.candidate_sha256,
                },
                "active": {
                    workflow.base_pack_sha256,
                    workflow.candidate_sha256,
                },
                "rolled_back": {workflow.base_pack_sha256},
            }[workflow.phase]
            if current_sha not in allowed_actual:
                raise IdentityPackLifecycleError(
                    "current identity cannot resume retained workflow phase"
                )
            return InstallerIdentityWorkflowStatus(
                phase=workflow.phase,
                base_pack_sha256=workflow.base_pack_sha256,
                current_pack_sha256=current_sha,
                draft_sha256=workflow.draft_sha256,
                candidate_sha256=workflow.candidate_sha256,
                updated_at=workflow.updated_at,
            )

    def stage(
        self,
        *,
        markdown: str,
        draft_id: str,
        expected_base_sha256: object,
    ) -> InstallerIdentityStage:
        with self._workflow_lock():
            return self._stage(
                markdown=markdown,
                draft_id=draft_id,
                expected_base_sha256=expected_base_sha256,
            )

    def _stage(
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
        already_staged = write_private_once(self._draft_path(digest), raw)
        changes = identity_presentation_diff(draft, base)
        self._write_workflow(
            phase="staged",
            base_pack_sha256=actual_base,
            current_pack_sha256=actual_base,
            draft_sha256=digest,
            candidate_sha256=None,
        )
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
        with self._workflow_lock():
            return self._issue(
                draft_sha256=draft_sha256,
                expected_base_sha256=expected_base_sha256,
            )

    def _issue(
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
        workflow = self._load_workflow()
        if (
            workflow is None
            or workflow.phase not in {"staged", "issued"}
            or workflow.base_pack_sha256 != actual_base
            or workflow.draft_sha256 != draft_digest
        ):
            raise IdentityPackLifecycleError(
                "identity draft is not the current staged workflow"
            )
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
                    read_private_artifact(
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
            write_private_once(issue_intent_path, issue_intent)
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
        self._write_workflow(
            phase="issued",
            base_pack_sha256=actual_base,
            current_pack_sha256=actual_base,
            draft_sha256=draft_digest,
            candidate_sha256=candidate_sha,
        )
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
        with self._workflow_lock():
            return self._activate(
                candidate_sha256=candidate_sha256,
                expected_current_sha256=expected_current_sha256,
            )

    def _activate(
        self, *, candidate_sha256: object, expected_current_sha256: object
    ) -> InstallerIdentityTransition:
        candidate_digest = _require_sha256(
            candidate_sha256, label="identity candidate hash"
        )
        expected = _require_sha256(
            expected_current_sha256, label="expected current identity hash"
        )
        workflow = self._load_workflow()
        if (
            workflow is None
            or workflow.phase not in {"issued", "active"}
            or workflow.base_pack_sha256 != expected
            or workflow.candidate_sha256 != candidate_digest
            or workflow.draft_sha256 is None
        ):
            raise IdentityPackLifecycleError(
                "identity candidate is not the resumable issued workflow"
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
            transition = InstallerIdentityTransition(
                status="already_active",
                previous_sha256=rollback_sha,
                current_sha256=current_sha,
                rollback_sha256=rollback_sha,
            )
        else:
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
            transition = InstallerIdentityTransition(
                status="activated",
                previous_sha256=cutover.previous_sha256,
                current_sha256=cutover.current_sha256,
                rollback_sha256=cutover.rollback_sha256,
            )
        self._write_workflow(
            phase="active",
            base_pack_sha256=workflow.base_pack_sha256,
            current_pack_sha256=candidate_digest,
            draft_sha256=workflow.draft_sha256,
            candidate_sha256=candidate_digest,
        )
        return transition

    def rollback(
        self, *, expected_current_sha256: object
    ) -> InstallerIdentityTransition:
        with self._workflow_lock():
            return self._rollback(expected_current_sha256=expected_current_sha256)

    def _rollback(
        self, *, expected_current_sha256: object
    ) -> InstallerIdentityTransition:
        expected = _require_sha256(
            expected_current_sha256, label="expected current identity hash"
        )
        workflow = self._load_workflow()
        if (
            workflow is None
            or workflow.phase not in {"active", "rolled_back"}
            or workflow.candidate_sha256 != expected
            or workflow.draft_sha256 is None
        ):
            raise IdentityPackLifecycleError(
                "identity rollback is not the resumable active workflow"
            )
        current = self.current_identity()
        current_sha = canonical_sha256(current)
        _rollback, rollback_raw = self._load_rollback()
        rollback_sha = _sha256(rollback_raw)
        if hmac.compare_digest(current_sha, rollback_sha):
            transition = InstallerIdentityTransition(
                status="already_rolled_back",
                previous_sha256=expected,
                current_sha256=current_sha,
                rollback_sha256=rollback_sha,
            )
        else:
            if not hmac.compare_digest(current_sha, expected):
                raise IdentityPackLifecycleError(
                    "current identity changed before rollback"
                )
            cutover = rollback_identity_successor(
                current_path=self.current_path,
                rollback_path=self.rollback_path,
                trust=self._trust(),
            )
            transition = InstallerIdentityTransition(
                status="rolled_back",
                previous_sha256=cutover.previous_sha256,
                current_sha256=cutover.current_sha256,
                rollback_sha256=cutover.rollback_sha256,
            )
        self._write_workflow(
            phase="rolled_back",
            base_pack_sha256=workflow.base_pack_sha256,
            current_pack_sha256=workflow.base_pack_sha256,
            draft_sha256=workflow.draft_sha256,
            candidate_sha256=workflow.candidate_sha256,
        )
        return transition


__all__ = [
    "InstallerIdentityCandidate",
    "InstallerIdentityLifecycle",
    "InstallerIdentityStage",
    "InstallerIdentityTransition",
    "InstallerIdentityWorkflowStatus",
]
