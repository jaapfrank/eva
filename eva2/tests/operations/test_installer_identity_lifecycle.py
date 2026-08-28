from __future__ import annotations

import json
from pathlib import Path

import pytest

from eva.application.conversation import load_current_identity_pack
from eva.application.soul_markdown import soul_markdown_to_draft
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.identity_pack_lifecycle import (
    IdentityPackLifecycleError,
    bootstrap_identity_authority,
)
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle


def _lifecycle(
    root: Path, *, clock=lambda: "2026-08-29T01:00:00Z"
) -> InstallerIdentityLifecycle:
    template = load_current_identity_pack()
    authority = root / "authority"
    bootstrap_identity_authority(
        output_directory=authority,
        template=template,
        draft=soul_markdown_to_draft(
            "# Eva\n\n## Naam\nEvi", draft_id="installer-bootstrap", base=template
        ),
        identity_id="identity:installer:test",
        issuer="issuer:installer:test",
        issued_at="2026-08-29T00:55:00Z",
    )
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    return InstallerIdentityLifecycle(
        current_path=authority / "current.json",
        trust_path=authority / "trust.json",
        private_key_path=authority / "private-key.bin",
        workspace=workspace,
        clock=clock,
    )


def test_installer_identity_stages_issues_activates_and_rolls_back_after_restart(
    private_tmp_path: Path,
) -> None:
    lifecycle = _lifecycle(private_tmp_path)
    markdown = (
        "# Noor\n\n## Naam\nNoor\n\n## Humor\nlicht en speels\n\n"
        "## Temperament\nwarm, nieuwsgierig, eigenwijs"
    )

    base_sha256 = canonical_sha256(lifecycle.current_identity())
    staged = lifecycle.stage(
        markdown=markdown,
        draft_id="installer-noor",
        expected_base_sha256=base_sha256,
    )
    assert staged.changed_fields == ("display_name", "temperament", "humour")
    assert staged.already_staged is False
    assert (
        lifecycle.stage(
            markdown=markdown,
            draft_id="installer-noor",
            expected_base_sha256=base_sha256,
        ).already_staged
        is True
    )
    draft_path = lifecycle.workspace / f"draft-{staged.draft_sha256}.json"
    assert draft_path.stat().st_mode & 0o777 == 0o600

    issued = lifecycle.issue(
        draft_sha256=staged.draft_sha256,
        expected_base_sha256=staged.base_pack_sha256,
    )
    assert issued.pack_version == 2
    assert issued.already_issued is False

    restarted = InstallerIdentityLifecycle(
        current_path=lifecycle.current_path,
        trust_path=lifecycle.trust_path,
        private_key_path=lifecycle.private_key_path,
        workspace=lifecycle.workspace,
        clock=lambda: "2026-08-29T02:00:00Z",
    )
    replay = restarted.issue(
        draft_sha256=staged.draft_sha256,
        expected_base_sha256=staged.base_pack_sha256,
    )
    assert replay.already_issued is True
    assert replay.candidate_sha256 == issued.candidate_sha256

    activated = restarted.activate(
        candidate_sha256=issued.candidate_sha256,
        expected_current_sha256=staged.base_pack_sha256,
    )
    assert activated.status == "activated"
    assert activated.current_sha256 == issued.candidate_sha256
    assert restarted.current_identity().display_name == "Noor"
    assert (
        restarted.activate(
            candidate_sha256=issued.candidate_sha256,
            expected_current_sha256=staged.base_pack_sha256,
        ).status
        == "already_active"
    )

    rolled_back = restarted.rollback(expected_current_sha256=issued.candidate_sha256)
    assert rolled_back.status == "rolled_back"
    assert restarted.current_identity().display_name == "Evi"
    assert (
        restarted.rollback(expected_current_sha256=issued.candidate_sha256).status
        == "already_rolled_back"
    )


def test_installer_identity_refuses_stale_base_and_tampered_retained_draft(
    private_tmp_path: Path,
) -> None:
    lifecycle = _lifecycle(private_tmp_path)
    first = lifecycle.stage(
        markdown="## Naam\nNoor",
        draft_id="installer-first",
        expected_base_sha256=canonical_sha256(lifecycle.current_identity()),
    )
    candidate = lifecycle.issue(
        draft_sha256=first.draft_sha256,
        expected_base_sha256=first.base_pack_sha256,
    )
    lifecycle.activate(
        candidate_sha256=candidate.candidate_sha256,
        expected_current_sha256=first.base_pack_sha256,
    )

    with pytest.raises(
        IdentityPackLifecycleError, match="current identity changed after preview"
    ):
        lifecycle.issue(
            draft_sha256=first.draft_sha256,
            expected_base_sha256=first.base_pack_sha256,
        )

    second = lifecycle.stage(
        markdown="## Naam\nNova",
        draft_id="installer-second",
        expected_base_sha256=canonical_sha256(lifecycle.current_identity()),
    )
    draft_path = lifecycle.workspace / f"draft-{second.draft_sha256}.json"
    payload = json.loads(draft_path.read_bytes())
    payload["draft_id"] = "installer-tampered"
    draft_path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    draft_path.chmod(0o600)
    with pytest.raises(IdentityPackLifecycleError, match="draft hash differs"):
        lifecycle.issue(
            draft_sha256=second.draft_sha256,
            expected_base_sha256=canonical_sha256(lifecycle.current_identity()),
        )


def test_installer_identity_requires_private_workspace_and_private_key(
    private_tmp_path: Path,
) -> None:
    lifecycle = _lifecycle(private_tmp_path)
    lifecycle.workspace.chmod(0o755)
    with pytest.raises(IdentityPackLifecycleError, match="owner-only directory"):
        InstallerIdentityLifecycle(
            current_path=lifecycle.current_path,
            trust_path=lifecycle.trust_path,
            private_key_path=lifecycle.private_key_path,
            workspace=lifecycle.workspace,
        )

    lifecycle.workspace.chmod(0o700)
    lifecycle.private_key_path.chmod(0o644)
    staged = lifecycle.stage(
        markdown="## Naam\nNoor",
        draft_id="installer-key",
        expected_base_sha256=canonical_sha256(lifecycle.current_identity()),
    )
    with pytest.raises(IdentityPackLifecycleError, match="key must be owner-only"):
        lifecycle.issue(
            draft_sha256=staged.draft_sha256,
            expected_base_sha256=staged.base_pack_sha256,
        )
