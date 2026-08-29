from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path

import pytest

from tools import managed_b_reboot_probe as probe


BEFORE = probe.BootIdentity(
    windows_boot_utc="2026-08-01T10:00:00.0000000Z",
    kernel_boot_id="11111111-1111-1111-1111-111111111111",
    pid1_start_ticks=100,
)
WINDOWS_AFTER = probe.BootIdentity(
    windows_boot_utc="2026-08-29T10:00:00.0000000Z",
    kernel_boot_id="22222222-2222-2222-2222-222222222222",
    pid1_start_ticks=200,
)


def _runtime() -> dict[str, object]:
    return {
        "containers": {
            "core": {"health": "healthy", "restart_count": 0, "status": "running"}
        },
        "dashboard": {
            "authorized_status": 200,
            "contract": "EvaWorkBoard/v1",
            "unauthorized_status": 401,
        },
        "eva1_http_statuses": {"/": 200},
        "hindsight": {"memory_units": 12, "mental_models": 3},
        "ready": {"active": {"ready": True}},
        "rollback": {"databases_healthy": True, "port": 44710, "writers_stopped": True},
        "route_dropin_sha256": "a" * 64,
        "services": {
            "docker": "active_enabled",
            "eva1": "active_enabled",
            "eva1_restarts": 0,
        },
    }


def _state(path: Path, *, required: str = "windows_host_reboot") -> None:
    payload = {
        "armed_at": "2026-08-29T09:00:00+00:00",
        "boot": asdict(BEFORE),
        "contract": probe.CONTRACT_ARM,
        "conversation": {
            "chat_id": "8000000001",
            "marker": "koperen wolk",
            "stored_message_id": 1,
            "stored_reply_sha256": "b" * 64,
            "verify_message_id": 2,
            "user_id": 8000000001,
        },
        "pre_reboot": {
            "eva1_restarts": 0,
            "hindsight": {"memory_units": 11, "mental_models": 3},
            "route_dropin_sha256": "a" * 64,
        },
        "required_restart": required,
        "source_sha256": probe._source_sha256(),
    }
    probe._write_new_json(path, payload)


def _successful_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "_read_boot_identity", lambda: WINDOWS_AFTER)
    monkeypatch.setattr(probe, "_dashboard_token", lambda: "private-dashboard-token")
    monkeypatch.setattr(probe, "_runtime_snapshot", lambda _token: _runtime())
    monkeypatch.setattr(
        probe,
        "_canary_turn",
        lambda **_values: {"text": "koperen wolk"},
    )


def test_restart_classification_distinguishes_host_kernel_and_systemd() -> None:
    assert probe.classify_restart(BEFORE, WINDOWS_AFTER) == "windows_host_reboot"
    assert (
        probe.classify_restart(
            BEFORE,
            probe.BootIdentity(
                BEFORE.windows_boot_utc, WINDOWS_AFTER.kernel_boot_id, 200
            ),
        )
        == "wsl_kernel_restart"
    )
    assert (
        probe.classify_restart(
            BEFORE,
            probe.BootIdentity(BEFORE.windows_boot_utc, BEFORE.kernel_boot_id, 200),
        )
        == "wsl_systemd_restart"
    )
    assert probe.classify_restart(BEFORE, BEFORE) == "none"


def test_synthetic_turn_uses_the_sealed_live_canary_scope() -> None:
    payload = probe._canary_payload(
        chat_id="8000000001", user_id=8000000001, message_id=1, text="test"
    )
    assert payload["tenant_id"] == "live-test-tenant"
    assert payload["installation_id"] == "live-test-installation"


def test_reboot_topology_requires_active_slot4_and_quiescent_slot3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert probe.ACTIVE_PORT == 44711
    assert probe.ROLLBACK_PORT == 44710
    assert "managed-b-slot4" in probe.CONTAINERS["core"][0]
    assert probe.CONTAINERS["rollback_core"][1] == "exited"
    lines = []
    for name, expected_status, needs_health in probe.CONTAINERS.values():
        health = "healthy" if needs_health else "none"
        if expected_status == "exited":
            health = "unhealthy"
        lines.append(f"/{name}|{expected_status}|{health}|0|unless-stopped")
    monkeypatch.setattr(probe, "_run", lambda _command: "\n".join(lines))

    snapshot = probe._container_snapshot()

    assert snapshot["core"]["status"] == "running"
    assert snapshot["rollback_core"]["status"] == "exited"


def test_windows_requirement_rejects_only_wsl_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    _state(state)
    monkeypatch.setattr(
        probe,
        "_read_boot_identity",
        lambda: probe.BootIdentity(
            BEFORE.windows_boot_utc, WINDOWS_AFTER.kernel_boot_id, 200
        ),
    )
    with pytest.raises(probe.RebootProbeError, match="windows_host_reboot"):
        probe.verify(state, tmp_path / "receipt.json")
    assert not (tmp_path / "receipt.json").exists()


def test_same_boot_is_rejected_before_runtime_or_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    _state(state, required="wsl_systemd_restart")
    touched: list[str] = []
    monkeypatch.setattr(probe, "_read_boot_identity", lambda: BEFORE)
    monkeypatch.setattr(
        probe, "_runtime_snapshot", lambda _token: touched.append("runtime")
    )
    with pytest.raises(probe.RebootProbeError, match="was not observed"):
        probe.verify(state, tmp_path / "receipt.json")
    assert touched == []


def test_success_writes_secret_free_exclusive_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    receipt = tmp_path / "receipt.json"
    _state(state)
    _successful_dependencies(monkeypatch)
    result = probe.verify(state, receipt)
    assert result["status"] == "passed"
    assert result["restart"]["kind"] == "windows_host_reboot"
    raw = receipt.read_text(encoding="utf-8")
    assert "private-dashboard-token" not in raw
    assert "8000000001" not in raw
    assert "koperen wolk" not in raw
    assert os.stat(receipt).st_mode & 0o777 == 0o600
    with pytest.raises(probe.RebootProbeError, match="must not already exist"):
        probe.verify(state, receipt)


def test_count_regression_and_marker_mismatch_fail_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    _state(state)
    _successful_dependencies(monkeypatch)
    regressed = _runtime()
    regressed["hindsight"] = {"memory_units": 10, "mental_models": 3}
    monkeypatch.setattr(probe, "_runtime_snapshot", lambda _token: regressed)
    with pytest.raises(probe.RebootProbeError, match="count decreased"):
        probe.verify(state, tmp_path / "count.json")
    assert not (tmp_path / "count.json").exists()

    monkeypatch.setattr(probe, "_runtime_snapshot", lambda _token: _runtime())
    monkeypatch.setattr(probe, "_canary_turn", lambda **_values: {"text": "verkeerd"})
    with pytest.raises(probe.RebootProbeError, match="not recalled exactly"):
        probe.verify(state, tmp_path / "marker.json")
    assert not (tmp_path / "marker.json").exists()


def test_source_drift_is_rejected_before_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    _state(state)
    touched: list[str] = []
    monkeypatch.setattr(probe, "_source_sha256", lambda: "f" * 64)
    monkeypatch.setattr(
        probe, "_runtime_snapshot", lambda _token: touched.append("runtime")
    )
    with pytest.raises(probe.RebootProbeError, match="source changed"):
        probe.verify(state, tmp_path / "receipt.json")
    assert touched == []


def test_postboot_runtime_may_settle_before_single_model_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    _state(state)
    calls = {"runtime": 0, "canary": 0}
    monkeypatch.setattr(probe, "_read_boot_identity", lambda: WINDOWS_AFTER)
    monkeypatch.setattr(probe, "_dashboard_token", lambda: "private")

    def runtime(_token: str) -> dict[str, object]:
        calls["runtime"] += 1
        if calls["runtime"] == 1:
            raise probe.RebootProbeError("still starting")
        return _runtime()

    def canary(**_values: object) -> dict[str, str]:
        calls["canary"] += 1
        return {"text": "koperen wolk"}

    monkeypatch.setattr(probe, "_runtime_snapshot", runtime)
    monkeypatch.setattr(probe, "_canary_turn", canary)
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    monotonic = iter((0.0, 1.0, 1.0))
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(monotonic))
    probe.verify(state, tmp_path / "receipt.json", startup_wait_seconds=2)
    assert calls == {"runtime": 2, "canary": 1}


def test_private_state_rejects_open_mode_and_symlink(tmp_path: Path) -> None:
    state = tmp_path / "armed.json"
    _state(state)
    state.chmod(0o644)
    with pytest.raises(probe.RebootProbeError, match="metadata is unsafe"):
        probe._load_state(state)
    state.chmod(0o600)
    linked = tmp_path / "linked.json"
    linked.symlink_to(state)
    with pytest.raises(probe.RebootProbeError, match="metadata is unsafe"):
        probe._load_state(linked)


def test_arm_waits_for_hindsight_and_writes_private_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "armed.json"
    snapshots = _runtime()
    counts = iter(
        (
            {"memory_units": 12, "mental_models": 3},
            {"memory_units": 13, "mental_models": 4},
        )
    )
    monkeypatch.setattr(probe, "_dashboard_token", lambda: "private")
    monkeypatch.setattr(probe, "_read_boot_identity", lambda: BEFORE)
    monkeypatch.setattr(probe, "_runtime_snapshot", lambda _token: snapshots)
    monkeypatch.setattr(probe, "_hindsight_counts", lambda: next(counts))
    monkeypatch.setattr(probe, "_canary_turn", lambda **_values: {"text": "opgeslagen"})
    monkeypatch.setattr(probe, "_source_sha256", lambda: "e" * 64)
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    result = probe.arm(
        state, required_restart="windows_host_reboot", memory_wait_seconds=2
    )
    assert result["pre_reboot"]["hindsight"]["memory_units"] == 13
    assert json.loads(state.read_text())["contract"] == probe.CONTRACT_ARM
    assert os.stat(state).st_mode & 0o777 == 0o600
