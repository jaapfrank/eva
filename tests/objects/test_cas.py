from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
from pathlib import Path

import pytest

from tools.cas import (
    CasIntegrityError,
    CasRecoveryError,
    CasStateError,
    FileSystemCAS,
)
from eva.adapters.objects.envelope import XChaCha20ObjectCipher
from tests.support.fixture_keys import FixtureObjectKeyResolver
from eva.application.objects import (
    CasInstallStatus,
    CandidateBindingCommitError,
    DatabaseSelectedObject,
    DurableCandidateBindingCommit,
    ObjectApplicationService,
    ObjectBinding,
    ObjectIntegrityError,
    PreparedCandidateBinding,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = EVA2_ROOT / "design/vertical/noor-4d-execution-profile-v1.json"
PLAINTEXT = b"immutable object bytes\n"
BINDING = ObjectBinding(
    tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
    installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
    object_id="8fa3f97c-dc66-52f2-840c-d441b55f6a60",
    key_ref="6cb79e1a-220a-5780-b544-e2b4dcf1f19e",
    mime_type="text/plain",
)


def _resolver() -> FixtureObjectKeyResolver:
    return FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)


def _candidate():
    return XChaCha20ObjectCipher().encrypt(BINDING, PLAINTEXT, key_resolver=_resolver())


def _commit(binding: PreparedCandidateBinding) -> DurableCandidateBindingCommit:
    return DurableCandidateBindingCommit(
        prepared_binding_sha256=binding.canonical_sha256,
        committed_at="2026-08-20T12:00:00Z",
        commit_receipt_sha256="b" * 64,
    )


def test_cas_prepare_commit_install_and_independent_readback_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    cas = FileSystemCAS(root)
    candidate = _candidate()

    prepared = cas.prepare(candidate)
    final_path = root / cas.relative_path(candidate.ciphertext_sha256)
    assert not final_path.exists()
    assert len(list(root.rglob(".eva-candidate-*"))) == 1

    first = cas.install_prepared_and_readback(prepared, _commit(prepared.binding))
    second_prepared = cas.prepare(candidate)
    second = cas.install_prepared_and_readback(
        second_prepared, _commit(second_prepared.binding)
    )

    assert first.status is CasInstallStatus.CREATED
    assert second.status is CasInstallStatus.ALREADY_SAME
    assert first.envelope == candidate.envelope == second.envelope
    assert first.path == Path(
        "sha256",
        candidate.ciphertext_sha256[:2],
        f"{candidate.ciphertext_sha256}.eva2xo",
    )
    assert len(list(root.rglob(".eva-candidate-*"))) == 0


def test_recover_prepared_after_real_store_restart_resumes_install(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    first_process_store = FileSystemCAS(root)
    prepared = first_process_store.prepare(candidate)
    durable_binding = prepared.binding
    commit = _commit(durable_binding)
    del prepared
    del first_process_store

    restarted_store = FileSystemCAS(root)
    recovered = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(), store=restarted_store
    ).recover_candidate_and_authenticate(
        candidate,
        durable_binding=durable_binding,
        expected_plaintext=PLAINTEXT,
        key_resolver=_resolver(),
        durable_commit=commit,
    )

    assert recovered.install_status is CasInstallStatus.CREATED
    assert recovered.ciphertext_sha256 == candidate.ciphertext_sha256
    assert recovered.authenticated
    assert list(root.rglob(".eva-candidate-*")) == []


def test_recover_after_final_cleanup_returns_authenticated_already_same(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    first_process_store = FileSystemCAS(root)
    prepared = first_process_store.prepare(candidate)
    durable_binding = prepared.binding
    commit = _commit(durable_binding)
    installed = first_process_store.install_prepared_and_readback(prepared, commit)
    final = root / installed.path
    before = final.stat()
    assert list(root.rglob(".eva-candidate-*")) == []
    del prepared
    del first_process_store

    restarted_store = FileSystemCAS(root)
    recovered = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(), store=restarted_store
    ).recover_candidate_and_authenticate(
        candidate,
        durable_binding=durable_binding,
        expected_plaintext=PLAINTEXT,
        key_resolver=_resolver(),
        durable_commit=commit,
    )

    after = final.stat()
    assert recovered.install_status is CasInstallStatus.ALREADY_SAME
    assert recovered.ciphertext_sha256 == candidate.ciphertext_sha256
    assert recovered.authenticated
    assert (after.st_dev, after.st_ino, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
    )
    assert list(root.rglob(".eva-candidate-*")) == []


def test_recover_zero_temp_and_missing_final_fails_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    durable_binding = prepared.binding
    commit = _commit(durable_binding)
    temporary = next(root.rglob(".eva-candidate-*"))
    temporary.unlink()

    with pytest.raises(CasRecoveryError, match="final"):
        FileSystemCAS(root).recover_prepared(
            candidate,
            durable_binding,
            commit,
        )

    assert list(root.rglob(".eva-candidate-*")) == []
    assert list(root.rglob("*.eva2xo")) == []


def test_recover_zero_temp_and_corrupt_final_fails_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    first_process_store = FileSystemCAS(root)
    prepared = first_process_store.prepare(candidate)
    durable_binding = prepared.binding
    commit = _commit(durable_binding)
    installed = first_process_store.install_prepared_and_readback(prepared, commit)
    final = root / installed.path
    corrupt = bytearray(final.read_bytes())
    corrupt[-1] ^= 1
    final.write_bytes(corrupt)

    with pytest.raises(CasRecoveryError, match="final"):
        FileSystemCAS(root).recover_prepared(
            candidate,
            durable_binding,
            commit,
        )

    assert final.read_bytes() == bytes(corrupt)
    assert list(root.rglob(".eva-candidate-*")) == []


def test_recover_prepared_rejects_receipt_binding_mismatch_without_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    wrong_commit = DurableCandidateBindingCommit(
        prepared_binding_sha256="0" * 64,
        committed_at="2026-08-20T12:00:00Z",
        commit_receipt_sha256="b" * 64,
    )

    with pytest.raises(CasRecoveryError, match="durable receipt differs"):
        FileSystemCAS(root).recover_prepared(candidate, prepared.binding, wrong_commit)

    assert prepared.binding.canonical_sha256 != wrong_commit.prepared_binding_sha256
    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert list(root.rglob("*.eva2xo")) == []


def test_recovered_handle_accepts_only_the_exact_durable_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    commit = _commit(prepared.binding)
    restarted_store = FileSystemCAS(root)
    recovered = restarted_store.recover_prepared(candidate, prepared.binding, commit)
    other_receipt = DurableCandidateBindingCommit(
        prepared_binding_sha256=commit.prepared_binding_sha256,
        committed_at=commit.committed_at,
        commit_receipt_sha256="c" * 64,
    )

    with pytest.raises(CasStateError, match="recovered receipt"):
        restarted_store.install_prepared_and_readback(recovered, other_receipt)

    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert list(root.rglob("*.eva2xo")) == []


def test_recover_prepared_rejects_mutated_temp_without_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    temporary = next(root.rglob(".eva-candidate-*"))
    mutated = bytearray(temporary.read_bytes())
    mutated[-1] ^= 1
    temporary.write_bytes(mutated)

    with pytest.raises(CasRecoveryError, match="digest differs"):
        FileSystemCAS(root).recover_prepared(
            candidate, prepared.binding, _commit(prepared.binding)
        )

    assert temporary.read_bytes() == bytes(mutated)
    assert list(root.rglob("*.eva2xo")) == []


def test_recover_prepared_rejects_multiple_links_without_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    temporary = next(root.rglob(".eva-candidate-*"))
    unexpected_link = temporary.with_name(".eva-candidate-unexpected-link")
    os.link(temporary, unexpected_link)

    with pytest.raises(CasRecoveryError, match="multiple unexpected links"):
        FileSystemCAS(root).recover_prepared(
            candidate, prepared.binding, _commit(prepared.binding)
        )

    assert temporary.exists()
    assert unexpected_link.exists()
    assert list(root.rglob("*.eva2xo")) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_recover_prepared_opens_matching_temp_nofollow_and_preserves_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    temporary = next(root.rglob(".eva-candidate-*"))
    preserved = temporary.with_name("durable-temp-preserved")
    temporary.rename(preserved)
    temporary.symlink_to(preserved.name)

    with pytest.raises(CasRecoveryError, match="without following links"):
        FileSystemCAS(root).recover_prepared(
            candidate, prepared.binding, _commit(prepared.binding)
        )

    assert temporary.is_symlink()
    assert preserved.read_bytes() == candidate.envelope
    assert list(root.rglob("*.eva2xo")) == []


def test_recover_prepared_accepts_final_already_linked_and_cleans_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    first_process_store = FileSystemCAS(root)
    prepared = first_process_store.prepare(candidate)
    temporary = next(root.rglob(".eva-candidate-*"))
    final = root / first_process_store.relative_path(candidate.ciphertext_sha256)
    os.link(temporary, final)
    durable_binding = prepared.binding
    commit = _commit(durable_binding)
    del prepared
    del first_process_store

    restarted_store = FileSystemCAS(root)
    recovered = restarted_store.recover_prepared(candidate, durable_binding, commit)
    readback = restarted_store.install_prepared_and_readback(recovered, commit)

    assert readback.status is CasInstallStatus.ALREADY_SAME
    assert readback.envelope == candidate.envelope
    assert final.read_bytes() == candidate.envelope
    assert list(root.rglob(".eva-candidate-*")) == []


def test_recover_prepared_rejects_expected_binding_that_differs_from_envelope(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    candidate = _candidate()
    prepared = FileSystemCAS(root).prepare(candidate)
    wrong_binding = ObjectBinding(
        tenant_id=candidate.binding.tenant_id,
        installation_id=candidate.binding.installation_id,
        object_id="00000000-0000-4000-8000-000000000001",
        key_ref=candidate.binding.key_ref,
        mime_type=candidate.binding.mime_type,
    )

    with pytest.raises(CasRecoveryError, match="binding differs from envelope"):
        FileSystemCAS(root).recover_prepared(
            replace(candidate, binding=wrong_binding),
            prepared.binding,
            _commit(prepared.binding),
        )

    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert list(root.rglob("*.eva2xo")) == []


def test_cas_refuses_final_install_without_durable_binding_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    cas = FileSystemCAS(root)
    candidate = _candidate()
    prepared = cas.prepare(candidate)

    with pytest.raises(CasStateError, match="durable"):
        cas.install_prepared_and_readback(prepared, None)  # type: ignore[arg-type]

    assert not (root / cas.relative_path(candidate.ciphertext_sha256)).exists()
    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert cas.discard_uncommitted(prepared)


def test_concurrent_committed_preparations_get_one_create_and_one_found_same(
    tmp_path: Path,
) -> None:
    cas = FileSystemCAS(tmp_path / "objects")
    candidate = _candidate()
    prepared = [cas.prepare(candidate), cas.prepare(candidate)]

    def install(item):
        return cas.install_prepared_and_readback(item, _commit(item.binding))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(install, prepared))

    assert sorted(item.status.value for item in results) == [
        "already_same",
        "created",
    ]
    assert all(item.envelope == candidate.envelope for item in results)


def test_mutated_final_object_is_never_overwritten_or_returned(tmp_path: Path) -> None:
    cas = FileSystemCAS(tmp_path / "objects")
    candidate = _candidate()
    prepared = cas.prepare(candidate)
    readback = cas.install_prepared_and_readback(prepared, _commit(prepared.binding))
    target = tmp_path / "objects" / readback.path
    mutated = bytearray(target.read_bytes())
    mutated[-1] ^= 1
    target.write_bytes(mutated)

    with pytest.raises(CasIntegrityError, match="SHA-256"):
        cas.read(candidate.ciphertext_sha256)
    retry = cas.prepare(candidate)
    with pytest.raises(CasIntegrityError):
        cas.install_prepared_and_readback(retry, _commit(retry.binding))
    with pytest.raises(CasStateError, match="durably committed"):
        cas.discard_uncommitted(retry)
    assert target.read_bytes() == bytes(mutated)
    assert len(list((tmp_path / "objects").rglob(".eva-candidate-*"))) == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_cas_read_rejects_a_symlink_at_the_content_address(tmp_path: Path) -> None:
    cas = FileSystemCAS(tmp_path / "objects")
    candidate = _candidate()
    relative = cas.relative_path(candidate.ciphertext_sha256)
    target = tmp_path / "objects" / relative
    target.parent.mkdir(parents=True)
    attacker = tmp_path / "attacker"
    attacker.write_bytes(candidate.envelope)
    target.symlink_to(attacker)

    with pytest.raises(CasIntegrityError, match="regular immutable file"):
        cas.read(candidate.ciphertext_sha256)


def test_product_service_requires_durable_binding_commit_before_final_install(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    cas = FileSystemCAS(root)
    service = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(),
        store=cas,
    )
    order: list[str] = []

    def commit(binding: PreparedCandidateBinding) -> DurableCandidateBindingCommit:
        order.append("commit")
        assert len(list(root.rglob(".eva-candidate-*"))) == 1
        assert not (root / cas.relative_path(binding.ciphertext_sha256)).exists()
        return _commit(binding)

    created = service.create_candidate_and_authenticate(
        BINDING,
        PLAINTEXT,
        key_resolver=_resolver(),
        binding_committer=commit,
    )

    order.append("authenticated")
    assert order == ["commit", "authenticated"]
    assert created.install_status is CasInstallStatus.CREATED
    assert created.binding == BINDING
    assert created.plaintext_sha256 == hashlib.sha256(PLAINTEXT).hexdigest()
    assert created.plaintext_byte_length == len(PLAINTEXT)
    assert created.authenticated
    assert not hasattr(created, "object_ref")


def test_failed_binding_commit_cleans_only_the_uncommitted_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    service = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(), store=FileSystemCAS(root)
    )

    with pytest.raises(CandidateBindingCommitError, match="durable"):
        service.create_candidate_and_authenticate(
            BINDING,
            PLAINTEXT,
            key_resolver=_resolver(),
            binding_committer=lambda _binding: None,
        )

    assert list(root.rglob(".eva-candidate-*")) == []
    assert list(root.rglob("*.eva2xo")) == []


def test_ambiguous_binding_commit_preserves_temp_for_reconciliation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    service = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(), store=FileSystemCAS(root)
    )

    def ambiguous(_binding: PreparedCandidateBinding):
        raise TimeoutError("commit acknowledgement lost")

    with pytest.raises(CandidateBindingCommitError, match="ambiguous"):
        service.create_candidate_and_authenticate(
            BINDING,
            PLAINTEXT,
            key_resolver=_resolver(),
            binding_committer=ambiguous,
        )

    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert list(root.rglob("*.eva2xo")) == []


def test_mismatched_binding_commit_receipt_preserves_temp_for_reconciliation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    service = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(), store=FileSystemCAS(root)
    )

    def wrong_receipt(
        _binding: PreparedCandidateBinding,
    ) -> DurableCandidateBindingCommit:
        return DurableCandidateBindingCommit(
            prepared_binding_sha256="0" * 64,
            committed_at="2026-08-20T12:00:00Z",
            commit_receipt_sha256="b" * 64,
        )

    with pytest.raises(CandidateBindingCommitError, match="another preparation"):
        service.create_candidate_and_authenticate(
            BINDING,
            PLAINTEXT,
            key_resolver=_resolver(),
            binding_committer=wrong_receipt,
        )

    assert len(list(root.rglob(".eva-candidate-*"))) == 1
    assert list(root.rglob("*.eva2xo")) == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: replace(value, canonical_aad=b"{}"), "canonical AAD"),
        (lambda value: replace(value, plaintext_sha256="0" * 64), "plaintext SHA-256"),
        (
            lambda value: replace(
                value, plaintext_byte_length=value.plaintext_byte_length + 1
            ),
            "plaintext length",
        ),
        (
            lambda value: replace(
                value,
                binding=ObjectBinding(
                    tenant_id=value.binding.tenant_id,
                    installation_id=value.binding.installation_id,
                    object_id="00000000-0000-4000-8000-000000000001",
                    key_ref=value.binding.key_ref,
                    mime_type=value.binding.mime_type,
                ),
            ),
            "binding",
        ),
    ],
)
def test_candidate_metadata_attack_fails_before_commit_or_claim(
    tmp_path: Path, mutate, message: str
) -> None:
    candidate = mutate(_candidate())
    commit_calls = 0

    def commit(binding: PreparedCandidateBinding):
        nonlocal commit_calls
        commit_calls += 1
        return _commit(binding)

    service = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(),
        store=FileSystemCAS(tmp_path / "objects"),
    )
    with pytest.raises(ObjectIntegrityError, match=message):
        service.write_candidate_and_authenticate(
            candidate,
            expected_plaintext=PLAINTEXT,
            key_resolver=_resolver(),
            binding_committer=commit,
        )
    assert commit_calls == 0
    assert list((tmp_path / "objects").rglob("*")) == []


def test_found_same_reads_db_selected_other_digest_without_installing_contender(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    cas = FileSystemCAS(root)
    cipher = XChaCha20ObjectCipher()
    selected_candidate = cipher.encrypt(BINDING, PLAINTEXT, key_resolver=_resolver())
    contender = cipher.encrypt(BINDING, PLAINTEXT, key_resolver=_resolver())
    assert selected_candidate.ciphertext_sha256 != contender.ciphertext_sha256
    prepared = cas.prepare(selected_candidate)
    cas.install_prepared_and_readback(prepared, _commit(prepared.binding))
    selected = DatabaseSelectedObject(
        binding=BINDING,
        ciphertext_sha256=selected_candidate.ciphertext_sha256,
        plaintext_sha256=selected_candidate.plaintext_sha256,
        plaintext_byte_length=selected_candidate.plaintext_byte_length,
        stored_envelope_byte_length=selected_candidate.stored_envelope_byte_length,
    )
    service = ObjectApplicationService(cipher=cipher, store=cas)

    found = service.authenticate_found_same(
        selected=selected,
        reserved_candidate=contender,
        expected_plaintext=PLAINTEXT,
        key_resolver=_resolver(),
    )

    assert found.selected_authority == selected
    assert found.ciphertext_sha256 == selected_candidate.ciphertext_sha256
    assert found.ciphertext_sha256 != contender.ciphertext_sha256
    assert found.authenticated
    assert not (root / cas.relative_path(contender.ciphertext_sha256)).exists()
    assert len(list(root.rglob("*.eva2xo"))) == 1
