from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.cas import FileSystemCAS
from eva.adapters.objects.envelope import XChaCha20ObjectCipher
from tests.support.fixture_keys import (
    DurableFixtureTraceCommit,
    FixtureHarnessError,
    FixtureObjectKeyResolver,
    OneShotFixtureNonce,
    create_traced_eva_fixture_candidate,
)
from eva.application.objects import (
    DurableCandidateBindingCommit,
    ObjectApplicationService,
    ObjectBinding,
    PreparedCandidateBinding,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = EVA2_ROOT / "design/vertical/noor-4d-execution-profile-v1.json"
SCHEDULE_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
PLAINTEXT = (
    "Bevestiging afspraak Noor BV\n\n"
    "De afspraak met Noor BV staat gepland op woensdag 26 augustus 2026 "
    "van 15.00 tot 15.30 uur.\n"
).encode()


def _authority() -> dict[str, object]:
    schedule = json.loads(SCHEDULE_PATH.read_bytes())
    return schedule["harnessEntropyInjectionAuthority"]


def _binding() -> ObjectBinding:
    scope = _authority()["exact_fixture_scope"]
    return ObjectBinding(
        tenant_id=scope["tenant_id"],
        installation_id=scope["installation_id"],
        object_id=scope["object_id"],
        key_ref=scope["key_ref"],
        mime_type="text/plain",
    )


def test_fixed_nonce_candidate_is_returned_only_after_durable_trace_commit(
    tmp_path: Path,
) -> None:
    authority = _authority()
    scope = authority["exact_fixture_scope"]
    attempt = scope["allowed_effect_attempts"][0]
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    nonce_source = OneShotFixtureNonce.from_sealed_schedule(SCHEDULE_PATH)
    observed: list[object] = []
    order: list[str] = []

    def commit(draft):
        result = DurableFixtureTraceCommit(
            ledger_sequence=1,
            committed_at="2026-08-20T12:00:00Z",
            ledger_commit_receipt_sha256="a" * 64,
        )
        observed.append(draft.complete(result))
        order.append("fixture_entropy_trace")
        return result

    candidate = create_traced_eva_fixture_candidate(
        authority_path=SCHEDULE_PATH,
        scenario_run_id="fixture:scenario-run:baseline:001",
        identity="Eva",
        object_store_id=scope["object_store_id"],
        effect_attempt_id=attempt["portable_effect_attempt_id"],
        binding=_binding(),
        plaintext=PLAINTEXT,
        key_resolver=resolver,
        nonce_source=nonce_source,
        trace_committer=commit,
    )

    assert candidate.ciphertext_sha256 == scope["complete_envelope_sha256"]
    assert (
        candidate.stored_envelope_byte_length == scope["complete_envelope_byte_length"]
    )
    assert nonce_source.consumed
    assert len(observed) == 1
    trace = observed[0]
    assert trace.authority_sha256 == authority["authority_sha256"]
    assert trace.effect_attempt_id == attempt["portable_effect_attempt_id"]
    assert trace.complete_envelope_sha256 == candidate.ciphertext_sha256
    assert trace.ledger_sequence == 1
    assert trace.ledger_commit_receipt_sha256 == "a" * 64

    def bind_candidate(
        binding: PreparedCandidateBinding,
    ) -> DurableCandidateBindingCommit:
        order.append("candidate_binding_commit")
        return DurableCandidateBindingCommit(
            prepared_binding_sha256=binding.canonical_sha256,
            committed_at="2026-08-20T12:00:00Z",
            commit_receipt_sha256="b" * 64,
        )

    authenticated = ObjectApplicationService(
        cipher=XChaCha20ObjectCipher(),
        store=FileSystemCAS(tmp_path / "objects"),
    ).write_candidate_and_authenticate(
        candidate,
        expected_plaintext=PLAINTEXT,
        key_resolver=resolver,
        binding_committer=bind_candidate,
    )
    assert authenticated.authenticated
    assert authenticated.cas_path.as_posix() == (
        "sha256/49/"
        "49e346abbafcd71855b96f72418b844624426aca5c3540f697098a189495731c"
        ".eva2xo"
    )
    assert authenticated.binding == _binding()
    assert authenticated.plaintext_sha256 == scope["plaintext_sha256"]
    assert not hasattr(authenticated, "object_ref")
    assert order == ["fixture_entropy_trace", "candidate_binding_commit"]


def test_missing_trace_receipt_prevents_candidate_adoption() -> None:
    authority = _authority()
    scope = authority["exact_fixture_scope"]
    attempt = scope["allowed_effect_attempts"][0]
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    nonce_source = OneShotFixtureNonce.from_sealed_schedule(SCHEDULE_PATH)

    with pytest.raises(FixtureHarnessError, match="durable trace"):
        create_traced_eva_fixture_candidate(
            authority_path=SCHEDULE_PATH,
            scenario_run_id="fixture:scenario-run:no-commit:001",
            identity="Eva",
            object_store_id=scope["object_store_id"],
            effect_attempt_id=attempt["portable_effect_attempt_id"],
            binding=_binding(),
            plaintext=PLAINTEXT,
            key_resolver=resolver,
            nonce_source=nonce_source,
            trace_committer=lambda _trace: None,
        )

    assert nonce_source.consumed


def test_scope_mismatch_fails_before_one_shot_nonce_is_released() -> None:
    authority = _authority()
    scope = authority["exact_fixture_scope"]
    attempt = scope["allowed_effect_attempts"][0]
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    nonce_source = OneShotFixtureNonce.from_sealed_schedule(SCHEDULE_PATH)
    wrong = ObjectBinding(
        tenant_id=_binding().tenant_id,
        installation_id="00000000-0000-4000-8000-000000000001",
        object_id=_binding().object_id,
        key_ref=_binding().key_ref,
        mime_type=_binding().mime_type,
    )

    with pytest.raises(FixtureHarnessError, match="installation_id"):
        create_traced_eva_fixture_candidate(
            authority_path=SCHEDULE_PATH,
            scenario_run_id="fixture:scenario-run:wrong-scope:001",
            identity="Eva",
            object_store_id=scope["object_store_id"],
            effect_attempt_id=attempt["portable_effect_attempt_id"],
            binding=wrong,
            plaintext=PLAINTEXT,
            key_resolver=resolver,
            nonce_source=nonce_source,
            trace_committer=lambda _trace: None,
        )

    assert not nonce_source.consumed


def test_one_shot_nonce_refuses_second_or_reentrant_consumption() -> None:
    source = OneShotFixtureNonce.from_sealed_schedule(SCHEDULE_PATH)
    assert len(source()) == 24
    with pytest.raises(FixtureHarnessError, match="already consumed"):
        source()


def test_fixture_key_resolver_is_exactly_installation_tenant_and_key_scoped() -> None:
    resolver = FixtureObjectKeyResolver.from_sealed_profile(PROFILE_PATH)
    binding = _binding()
    assert len(resolver(binding)) == 32

    wrong = ObjectBinding(
        tenant_id="00000000-0000-4000-8000-000000000001",
        installation_id=binding.installation_id,
        object_id=binding.object_id,
        key_ref=binding.key_ref,
        mime_type=binding.mime_type,
    )
    with pytest.raises(FixtureHarnessError, match="fixture object key"):
        resolver(wrong)
