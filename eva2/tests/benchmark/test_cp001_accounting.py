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
