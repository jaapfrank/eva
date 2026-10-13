"""Native export normalization never admits an incomplete product comparison."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"
sys.path.insert(0, str(HERE))
import cp001_accounting as accounting  # noqa: E402
import cp001_native as native  # noqa: E402


def export():
    _, request = native.request_for(
        "eva2",
        run_id="0123456789ab",
        task="A",
        repeat=1,
        step=0,
        text="fixture",
        occurred_at="2026-09-12T00:00:00Z",
    )
    return {
        "request": request,
        "journal_reply": {
            "text": "fixture reply",
            "text_sha256": native.digest("fixture reply"),
            **{
                k: request[k] for k in ("principal_id", "conversation_id", "message_id")
            },
            "provider": "fixture-provider",
            "model_usage": {
                "measurement": "measured",
                "input_tokens": 12,
                "output_tokens": 7,
                "model_attempts": 1,
                "tool_calls": 0,
            },
        },
        "provider_request": {
            "model": "fixture-model",
            "reasoning": {"effort": "medium"},
            "max_output_tokens": 1200,
        },
        "provider_response": {
            "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}
        },
    }


@pytest.mark.parametrize("participant", accounting.PARTICIPANTS)
@pytest.mark.parametrize("field", ("input_tokens", "output_tokens", "total_tokens"))
@pytest.mark.parametrize("bad", [None, -1, True, 1.5, "12"])
def test_missing_invalid_raw_usage_never_becomes_zero(participant, field, bad):
    data = export()
    data["provider_response"]["usage"][field] = bad
    result = accounting.normalize(participant, data)
    assert result["raw_provider_usage"][field] is None
    assert "provider_usage_missing_or_invalid:" + field in result["blockers"]
    assert not result["raw_provider_usage_coherent"]
    assert set(result["total_usage"].values()) == {None}


@pytest.mark.parametrize("field", accounting.USAGE_FIELDS)
def test_missing_conversation_field_discards_incomplete_receipt(field):
    data = export()
    del data["journal_reply"]["model_usage"][field]
    assert accounting.normalize("eva2", data)["conversation_usage"] is None


@pytest.mark.parametrize(
    "field", ("principal_id", "conversation_id", "message_id", "text_sha256")
)
def test_wrong_journal_binding_rejected(field):
    data = export()
    data["journal_reply"][field] = "wrong"
    assert accounting.normalize("eva2", data)["conversation_usage"] is None


def test_partial_is_not_complete_and_lossy_timeline_is_not_raw_usage():
    data = export()
    data["journal_reply"]["model_usage"]["measurement"] = "partial"
    result = accounting.normalize("eva2", data)
    assert result["conversation_usage"]["measurement"] == "partial"
    assert result["total_usage"]["input_tokens"] is None
    data = {
        "timeline_meta": {"inputTokens": 0, "outputTokens": 7, "requestAttempts": 2}
    }
    result = accounting.normalize("eva1-bottie", data)
    assert result["timeline_diagnostic"]["input_tokens"] == 0
    assert result["raw_provider_usage"]["input_tokens"] is None


def test_incoherent_raw_total_and_real_zero():
    data = export()
    data["provider_response"]["usage"]["total_tokens"] = 20
    assert "provider_usage_incoherent" in accounting.normalize("eva2", data)["blockers"]
    data["provider_response"]["usage"] = dict(
        input_tokens=0, output_tokens=0, total_tokens=0
    )
    assert accounting.normalize("eva2", data)["raw_provider_usage_coherent"]


@pytest.mark.parametrize("field", ("model", "reasoning_effort", "output_tokens"))
def test_equal_changed_and_missing_observed_limits(field):
    pair = {p: export() for p in accounting.PARTICIPANTS}
    assert accounting.preflight(pair)["observed_limit_equality"][field] == "equal"
    wire = pair["eva1-bottie"]["provider_request"]
    container, key = (
        (wire["reasoning"], "effort")
        if field == "reasoning_effort"
        else (wire, "max_output_tokens" if field == "output_tokens" else "model")
    )
    container[key] = 1100 if field == "output_tokens" else "different"
    assert accounting.preflight(pair)["observed_limit_equality"][field] == "mismatch"
    del container[key]
    assert accounting.preflight(pair)["observed_limit_equality"][field] == "unknown"


@pytest.mark.parametrize("field", accounting.LIMIT_FIELDS)
def test_nulls_never_establish_equal_limits(field):
    report = accounting.preflight()
    assert report["observed_limit_equality"][field] == "unknown"
    for result in report["participants"].values():
        assert "observed_limit_unknown:" + field in result["blockers"]
        assert result["server_sample_limits"][field] is None


def test_claims_cannot_override_gate_or_convert_config_to_enforcement():
    pair = {p: export() for p in accounting.PARTICIPANTS}
    for data in pair.values():
        data.update(
            live_enabled=True,
            complete=True,
            server_quiescent=True,
            total_usage=dict(input_tokens=12, output_tokens=7),
            server_sample_limits={k: 1 for k in accounting.LIMIT_FIELDS},
            runtime_resource_budget=dict(max_input_tokens=4096, max_output_tokens=1200),
        )
    report = accounting.preflight(pair)
    assert not report["live_enabled"] and report["admission"] == "unknown"
    assert (
        report["participants"]["eva2"]["configured_resource_tokens"]["input_tokens"]
        == 4096
    )
    assert (
        report["participants"]["eva2"]["server_sample_limits"]["input_tokens"] is None
    )
    assert all(
        v["total_usage"]["input_tokens"] is None
        for v in report["participants"].values()
    )
    assert native.preflight()["accounting"]["live_enabled"] is False


def test_cli_export_and_missing_source(tmp_path):
    data = tmp_path / "exports.json"
    data.write_text(json.dumps({p: export() for p in accounting.PARTICIPANTS}))
    result = subprocess.run(
        [sys.executable, str(HERE / "cp001_accounting.py"), "--exports", str(data)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["network_used"] is False
    assert "fixture reply" not in result.stdout
    for value in accounting.preflight(root=tmp_path)["participants"].values():
        assert "native_source_missing" in value["blockers"]
    data.write_text('{"unknown-product":{}}')
    result = subprocess.run(
        [sys.executable, str(HERE / "cp001_accounting.py"), "--exports", str(data)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and not result.stdout


SAMPLE_LIMITS = {"providerAttempts": 2, "providerInputTokens": 50, "providerOutputTokens": 10}
SAMPLE_PROFILES = {"contact": ("fast", "none"), "helper": ("work", "medium")}


def test_native_sample_combines_foreground_and_helper_usage_once():
    ledger = accounting.ProviderSampleLedger("bound-sample", SAMPLE_LIMITS, SAMPLE_PROFILES)
    contact = ledger.begin("contact", "fast", "none")
    helper = ledger.begin("helper", "work", "medium")
    ledger.finish(helper, {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23})
    ledger.finish(contact, {"input_tokens": 15, "output_tokens": 2, "total_tokens": 17})
    report = ledger.report()
    assert report["complete"] and report["provider_attempts"] == 2
    assert report["total_usage"] == {"input_tokens": 35, "output_tokens": 5, "total_tokens": 40}
    assert {x["sample_id"] for x in report["attempts"]} == {"bound-sample"}
    assert not report["live_admission"]


def test_native_sample_cancelled_helper_is_not_zero_or_complete():
    ledger = accounting.ProviderSampleLedger("bound-sample", SAMPLE_LIMITS, SAMPLE_PROFILES)
    contact = ledger.begin("contact", "fast", "none")
    helper = ledger.begin("helper", "work", "medium")
    ledger.finish(contact, {"input_tokens": 15, "output_tokens": 2, "total_tokens": 17})
    ledger.incomplete(helper, "cancelled_usage_unknown")
    report = ledger.report()
    assert not report["complete"] and set(report["total_usage"].values()) == {None}
    assert report["known_partial_usage"]["input_tokens"] == 15
    with pytest.raises(accounting.SampleStopped):
        ledger.begin("contact", "fast", "none")


def test_native_sample_rejects_wrong_profile_before_sdk_attempt():
    ledger = accounting.ProviderSampleLedger("bound-sample", SAMPLE_LIMITS, SAMPLE_PROFILES)
    with pytest.raises(accounting.SampleStopped):
        ledger.begin("contact", "fast", "high")
    assert ledger.report()["provider_attempts"] == 0
    assert not ledger.report()["complete"]


def test_native_sample_concurrent_admission_cannot_exceed_shared_attempts():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    ledger = accounting.ProviderSampleLedger("bound-sample", SAMPLE_LIMITS, SAMPLE_PROFILES)
    barrier = threading.Barrier(3)
    def begin():
        barrier.wait()
        try:
            return ledger.begin("helper", "work", "medium")
        except accounting.SampleStopped:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        values = list(pool.map(lambda _: begin(), range(3)))
    assert sum(x is not None for x in values) == 2
    assert ledger.report()["provider_attempts"] == 2
    assert ledger.report()["stop"]


def test_native_sample_observed_overrun_stops_next_call_without_output_cap_claim():
    ledger = accounting.ProviderSampleLedger("bound-sample", SAMPLE_LIMITS, SAMPLE_PROFILES)
    call = ledger.begin("helper", "work", "medium")
    with pytest.raises(accounting.SampleStopped):
        ledger.finish(call, {"input_tokens": 25, "output_tokens": 11, "total_tokens": 36})
    with pytest.raises(accounting.SampleStopped):
        ledger.begin("contact", "fast", "none")
    assert ledger.report()["attempts"][0]["usage"]["output_tokens"] == 11


def test_native_mock_stream_close_keeps_missing_usage_unknown():
    import asyncio
    import p1_agentzero_native_probe as probe
    limits = {"providerAttempts": 2, "providerInputTokens": 100, "providerOutputTokens": 100}
    ledger = accounting.ProviderSampleLedger("stream-close", limits, probe.PROFILES)
    mock = probe.MockSDK(ledger)

    async def close_after_text():
        token = probe.ROLE.set("helper")
        try:
            stream = await mock.completion(model="openai/gpt-6-astra", reasoning_effort="medium", stream=True)
            await anext(stream)
            await stream.aclose()
        finally:
            probe.ROLE.reset(token)
    asyncio.run(close_after_text())
    report = ledger.report()
    assert report["provider_attempts"] == 1 and not report["complete"]
    assert set(report["total_usage"].values()) == {None}
    assert report["attempts"][0]["status"] == "stream_closed_usage_unknown"


def test_native_mock_late_helper_stays_with_original_context():
    import asyncio
    from types import SimpleNamespace
    import p1_agentzero_native_probe as probe
    limits = {"providerAttempts": 2, "providerInputTokens": 100, "providerOutputTokens": 100}
    first = accounting.ProviderSampleLedger("first", limits, probe.PROFILES)
    second = accounting.ProviderSampleLedger("second", limits, probe.PROFILES)
    saved = dict(probe.MOCKS)
    probe.MOCKS.clear()
    probe.MOCKS.update(first=probe.MockSDK(first), second=probe.MockSDK(second))

    async def interleave():
        release = asyncio.Event()
        async def helper(self):
            await release.wait()
            return await probe.native_completion(model="openai/gpt-6-astra", reasoning_effort="medium")
        tagged = probe.tag_method(helper, "helper")
        late = asyncio.create_task(tagged(SimpleNamespace(context=SimpleNamespace(id="first"))))
        await asyncio.sleep(0)
        release.set()
        await tagged(SimpleNamespace(context=SimpleNamespace(id="second")))
        await late
    try:
        asyncio.run(interleave())
    finally:
        probe.MOCKS.clear()
        probe.MOCKS.update(saved)
    assert first.report()["provider_attempts"] == second.report()["provider_attempts"] == 1
    assert first.report()["attempts"][0]["sample_id"] == "first"
    assert second.report()["attempts"][0]["sample_id"] == "second"


def test_native_mock_unbound_context_is_rejected_before_sdk():
    import asyncio
    import p1_agentzero_native_probe as probe
    token = probe.CONTEXT.set(None)
    try:
        with pytest.raises(accounting.SampleStopped, match="no bound AgentContext"):
            asyncio.run(probe.native_completion(model="openai/gpt-6-sol", reasoning_effort="none"))
    finally:
        probe.CONTEXT.reset(token)


def test_native_sample_stop_cancels_only_its_pending_helper_across_threads():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import p1_agentzero_native_probe as probe
    limits = {"providerAttempts": 2, "providerInputTokens": 100, "providerOutputTokens": 100}
    first = accounting.ProviderSampleLedger("first-stop", limits, probe.PROFILES)
    second = accounting.ProviderSampleLedger("second-live", limits, probe.PROFILES)
    mocks = [probe.MockSDK(first, block=True), probe.MockSDK(second, block=True)]
    def run(mock):
        async def pending():
            token = probe.ROLE.set("helper")
            try:
                await mock.completion(model="openai/gpt-6-astra", reasoning_effort="medium")
            except asyncio.CancelledError:
                return "cancelled"
            finally:
                probe.ROLE.reset(token)
        return asyncio.run(pending())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, m) for m in mocks]
        try:
            assert all(m.started["helper"].wait(5) for m in mocks)
            first.stop_async_attempts("stop first only")
            assert futures[0].result(timeout=5) == "cancelled"
            assert not futures[1].done() and not second.report()["stop_requested"]
            assert first.report()["active_async_attempts"] == 0
            assert second.report()["active_async_attempts"] == 1
            assert first.report()["stop"] == "stop first only"
            assert first.report()["total_usage"]["output_tokens"] is None
        finally:
            first.stop_async_attempts()
            second.stop_async_attempts()
        assert futures[1].result(timeout=5) == "cancelled"


def test_native_sample_stop_between_admission_and_task_binding_is_preserved():
    import asyncio
    ledger = accounting.ProviderSampleLedger("stop-race", SAMPLE_LIMITS, SAMPLE_PROFILES)
    attempt = ledger.begin("helper", "work", "medium")
    ledger.stop_async_attempts("requested before task binding")
    async def bind_after_stop():
        try:
            ledger.track_async_attempt(attempt)
            await asyncio.Future()
        except asyncio.CancelledError:
            ledger.incomplete(attempt, "cancelled_usage_unknown")
    asyncio.run(bind_after_stop())
    report = ledger.report()
    assert report["active_async_attempts"] == 0 and not report["cancel_errors"]
    assert report["stop"] == "requested before task binding"
    assert report["total_usage"]["input_tokens"] is None
    with pytest.raises(accounting.SampleStopped):
        ledger.begin("helper", "work", "medium")


def test_native_sample_closed_loop_is_unknown_cancellation_not_quiescence():
    import asyncio
    ledger = accounting.ProviderSampleLedger("closed-loop", SAMPLE_LIMITS, SAMPLE_PROFILES)
    attempt = ledger.begin("helper", "work", "medium")
    async def bind_without_settlement():
        ledger.track_async_attempt(attempt)
    asyncio.run(bind_without_settlement())
    ledger.stop_async_attempts()
    report = ledger.report()
    assert report["cancel_errors"] and report["active_async_attempts"] == 1
    assert not report["complete"] and report["total_usage"]["output_tokens"] is None


def test_native_sample_settled_attempt_cannot_cancel_later_work_in_same_task():
    import asyncio
    first = accounting.ProviderSampleLedger("settles-before-stop-callback", SAMPLE_LIMITS, SAMPLE_PROFILES)
    second = accounting.ProviderSampleLedger("later-work", SAMPLE_LIMITS, SAMPLE_PROFILES)
    async def continue_after_settlement():
        earlier = first.begin("helper", "work", "medium")
        first.track_async_attempt(earlier)
        first.stop_async_attempts()
        first.finish(earlier, {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3})
        later = second.begin("helper", "work", "medium")
        second.track_async_attempt(later)
        await asyncio.sleep(0)  # Execute the queued stop for the already settled call.
        second.finish(later, {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
    asyncio.run(continue_after_settlement())
    assert first.report()["complete"] and first.report()["stop_requested"]
    assert second.report()["complete"] and not second.report()["stop_requested"]
    assert second.report()["total_usage"]["total_tokens"] == 4
