"""Original aggregate admission persists without inference or hidden case data."""
from pathlib import Path
import json
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"))
from cp001_accounting import PersistentAggregateLedger, ProviderSampleLedger, SampleStopped


LIMITS = {"providerAttempts": 2, "providerInputTokens": 100, "providerOutputTokens": 20,
          "wallSeconds": 60, "toolDispatches": 2, "speechRequests": 0}
PROFILES = {"contact": ("fixture-model", "none")}
BINDING = {"plan_sha256": "a" * 64, "dataset_sha256": "b" * 64}


def plan():
    ceilings = {k: v * 2 for k, v in LIMITS.items()}
    return {"families": [{"family": "conversation_memory", "cases": 2, "epochs": 1,
                           "perSampleLimits": LIMITS, "aggregateLimitsPerParticipant": ceilings}],
            "aggregateLimits": {"samplesPerParticipant": 2, "perParticipant": ceilings}}


def store(tmp_path, participant="eva2"):
    return PersistentAggregateLedger(tmp_path / "aggregate.db", plan(), BINDING, participant)


def sample(aggregate, key="case1"):
    return ProviderSampleLedger(key, LIMITS, PROFILES, aggregate=aggregate,
                                family="conversation_memory")


def complete(ledger):
    attempt = ledger.begin("contact", "fixture-model", "none")
    ledger.finish(attempt, {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})


def test_restart_restores_attempts_and_usage_without_reset(tmp_path):
    complete(sample(store(tmp_path)))
    resumed = sample(store(tmp_path))
    assert resumed.report()["provider_attempts"] == 1
    complete(resumed)
    with pytest.raises(SampleStopped, match="sample attempts"):
        sample(store(tmp_path)).begin("contact", "fixture-model", "none")
    assert store(tmp_path).report()["known_observed"] == {
        "provider_attempts": 2, "input_tokens": 20, "output_tokens": 4}


def test_existing_history_debits_aggregate_and_unused_allowance_not_donated(tmp_path):
    aggregate = store(tmp_path)
    summary = {"provider_attempts": 1, "input_tokens": 10, "output_tokens": 2,
               "provider_usage_complete": True}
    aggregate.seed_history("old", "conversation_memory", summary, "c" * 64)
    aggregate.seed_history("old", "conversation_memory", summary, "c" * 64)
    complete(sample(aggregate, "next"))
    with pytest.raises(SampleStopped, match="aggregate sample count"):
        sample(store(tmp_path), "third")
    report = store(tmp_path).report()
    assert report["admitted_samples"] == 2
    assert report["reserved_original_envelopes"]["providerInputTokens"] == 200
    assert report["known_observed"]["input_tokens"] == 20


def test_pending_after_crash_is_unknown_not_fresh_usage(tmp_path):
    first = sample(store(tmp_path))
    first.begin("contact", "fixture-model", "none")
    with pytest.raises(SampleStopped, match="unresolved"):
        sample(store(tmp_path))
    assert store(tmp_path).report()["usage_unknown"]
    with pytest.raises(SampleStopped, match="aggregate usage is unknown"):
        sample(store(tmp_path), "different-case")


def test_preopened_sample_cannot_bypass_another_unsettled_sample(tmp_path):
    aggregate = store(tmp_path)
    first, preopened = sample(aggregate), sample(aggregate, "case2")
    first.begin("contact", "fixture-model", "none")
    with pytest.raises(SampleStopped, match="unresolved or exceeded"):
        preopened.begin("contact", "fixture-model", "none")
    assert preopened.report()["provider_attempts"] == 0
    assert store(tmp_path).report()["known_observed"]["provider_attempts"] == 1


def test_stopping_other_sample_does_not_discard_inflight_settlement(tmp_path):
    aggregate = store(tmp_path)
    first, other = sample(aggregate), sample(aggregate, "case2")
    attempt = first.begin("contact", "fixture-model", "none")
    # The complete settlement must persist even while another sample is stopped.
    other.stop_async_attempts("no input")
    first.finish(attempt, {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    assert store(tmp_path).report()["known_observed"]["input_tokens"] == 10


def test_stale_writer_cannot_dispatch_duplicate_attempt(tmp_path):
    first, stale = sample(store(tmp_path)), sample(store(tmp_path))
    first.begin("contact", "fixture-model", "none")
    with pytest.raises(SampleStopped, match="stale"):
        stale.begin("contact", "fixture-model", "none")
    assert store(tmp_path).report()["known_observed"]["provider_attempts"] == 1


def test_unknown_historical_usage_rejected_and_not_zero(tmp_path):
    with pytest.raises(SampleStopped, match="complete attestation"):
        store(tmp_path).seed_history("old", "conversation_memory", {
            "provider_attempts": 0, "input_tokens": None, "output_tokens": None,
            "provider_usage_complete": False}, "c" * 64)
    assert store(tmp_path).report()["admitted_samples"] == 0


def test_attested_unreachable_provider_zero_preserves_raw_usage_unknown(tmp_path):
    aggregate = store(tmp_path)
    observed = {"provider_attempts": 0, "input_tokens": 0, "output_tokens": 0,
                "provider_usage_complete": False, "provider_dispatch_not_reached": True,
                "raw_usage_measurement": "UNKNOWN"}
    aggregate.seed_history("failed-before-dispatch", "conversation_memory", observed, "c" * 64)
    assert observed["provider_usage_complete"] is False
    assert observed["raw_usage_measurement"] == "UNKNOWN"
    assert aggregate.report()["known_observed"]["input_tokens"] == 0
    assert aggregate.report()["reserved_original_envelopes"]["providerInputTokens"] == 100
    with pytest.raises(SampleStopped, match="complete attestation"):
        aggregate.seed_history("invalid-zero", "conversation_memory",
                               {**observed, "input_tokens": 1}, "c" * 64)


def test_incomplete_attempt_blocks_new_sample_admission(tmp_path):
    ledger = sample(store(tmp_path))
    attempt = ledger.begin("contact", "fixture-model", "none")
    ledger.incomplete(attempt, "process_eof_usage_unknown")
    with pytest.raises(SampleStopped, match="aggregate usage is unknown"):
        sample(store(tmp_path), "next")


def test_rejected_profile_remains_stopped_after_restart(tmp_path):
    ledger = sample(store(tmp_path))
    with pytest.raises(SampleStopped, match="wrong native"):
        ledger.begin("contact", "different-model", "none")
    with pytest.raises(SampleStopped, match="wrong native"):
        sample(store(tmp_path)).begin("contact", "fixture-model", "none")
    assert store(tmp_path).report()["known_observed"]["provider_attempts"] == 0


def test_token_overrun_retained_and_blocks_future_sample(tmp_path):
    ledger = sample(store(tmp_path))
    attempt = ledger.begin("contact", "fixture-model", "none")
    with pytest.raises(SampleStopped, match="token envelope exceeded"):
        ledger.finish(attempt, {"input_tokens": 101, "output_tokens": 2, "total_tokens": 103})
    with pytest.raises(SampleStopped, match="aggregate usage is unknown or exceeded"):
        sample(store(tmp_path), "next")
    assert store(tmp_path).report()["known_observed"]["input_tokens"] == 101


def test_binding_and_history_are_immutable_and_participants_do_not_donate(tmp_path):
    aggregate = store(tmp_path)
    complete(sample(aggregate, "case1")); complete(sample(aggregate, "case2"))
    with pytest.raises(ValueError, match="binding changed"):
        PersistentAggregateLedger(tmp_path / "aggregate.db", plan(), {"other": "binding"}, "eva2")
    with pytest.raises(SampleStopped, match="aggregate sample count"):
        sample(store(tmp_path), "case3")
    complete(sample(store(tmp_path, "eva1-bottie"), "case1"))
    assert store(tmp_path, "eva1-bottie").report()["admitted_samples"] == 1


def test_native_ipc_import_does_not_require_host_sqlite_library():
    source = r'''
import builtins, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def native_import(name, *args, **kwargs):
    if name in ("sqlite3", "_sqlite3") or name.startswith("sqlite3."):
        raise ImportError("SQLite is absent from the native worker image")
    return original(name, *args, **kwargs)
builtins.__import__ = native_import
from cp001_accounting import SampleStopped, ProviderSampleLedger
from cp001_process_accounting import ProcessSampleLedger
'''
    result = subprocess.run([sys.executable, "-c", source,
                             str(Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def declared_plan():
    frozen = json.loads((Path(__file__).resolve().parents[2] /
                         "benchmark/inspect_ai_v1/ev003_execution_plan_20261004.json").read_text())
    return {**plan(), "executionOrder": frozen["executionOrder"]}


@pytest.mark.parametrize("participant", ["openclaw", "hermes", "agent_zero"])
def test_declared_native_participant_keeps_own_aggregate_and_unknown_guard(tmp_path, participant):
    original = declared_plan()
    path = tmp_path / "aggregate.db"
    eva = PersistentAggregateLedger(path, original, BINDING, "eva2")
    sample(eva).begin("contact", "fixture-model", "none")
    native = PersistentAggregateLedger(path, original, BINDING, participant)
    assert native.report()["admitted_samples"] == 0
    complete(sample(native))
    assert native.report()["known_observed"]["input_tokens"] == 10
    assert eva.report()["usage_unknown"] is True
    with pytest.raises(SampleStopped, match="aggregate usage is unknown"):
        sample(eva, "next")
    assert native.report()["admitted_samples"] == 1


@pytest.mark.parametrize("original,participant", [
    (plan(), "agent_zero"),
    ({**plan(), "executionOrder": {"epochParticipantOrder": [["eva2"]]}}, "agent_zero"),
    ({**plan(), "executionOrder": {"epochParticipantOrder": [["unregistered"]]}}, "unregistered"),
])
def test_undeclared_or_unknown_participant_rejected_before_store_creation(tmp_path, original, participant):
    path = tmp_path / "aggregate.db"
    with pytest.raises(ValueError, match="explicit participant"):
        PersistentAggregateLedger(path, original, BINDING, participant)
    assert not path.exists()
