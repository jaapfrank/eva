"""Regression checks for the current-route managed-B data transition probe."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "experiments" / "managed-b-clean-graph-v1" / "probe.py"
SPEC = importlib.util.spec_from_file_location("managed_b_clean_graph_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.mark.parametrize(
    "changes",
    [
        {"clone_current_route": False},
        {"retain_slot": True},
        {"core_image": None},
        {"candidate_local_image_id": "sha256:" + "a" * 64},
        {"telegram_canary_client_sha256": None},
        {"conversation_memory_word": None},
        {"conversation_prompt_file": Path("/tmp/not-a-probe-prompt")},
    ],
)
def test_canary_options_refuse_non_disposable_paths_before_io(monkeypatch, changes):
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        probe, "_canary_probe_settings", lambda *a: pytest.fail("no file I/O")
    )
    monkeypatch.setattr(probe, "_live_status", lambda *a: pytest.fail("no resources"))
    options = dict(
        clone_current_route=True,
        core_image="eva2.local/test@sha256:" + "a" * 64,
        conversation_memory_word="merelvink",
        telegram_canary_user_sha256="a" * 64,
        telegram_canary_client_sha256="b" * 64,
    )
    assert probe.main(**{**options, **changes}) == 2


@pytest.mark.parametrize(
    "problem", [None, "duplicate", "group", "missing", "scope", "client"]
)
def test_canary_selects_only_hash_bound_existing_private_user(monkeypatch, problem):
    row = {
        "telegram_user_id": 7,
        "allowed_chat_ids": ["7"],
        "principal_id": "person-seven",
        "principal_binding_id": "44444444-4444-4444-8444-444444444444",
    }
    if problem == "group":
        row["allowed_chat_ids"] = ["-7"]
    rows = (
        [row, row]
        if problem == "duplicate"
        else ([] if problem == "missing" else [row])
    )
    raw = json.dumps(
        {"contract": "EvaTelegramPrincipalBindings/v2", "bindings": rows}
    ).encode()
    values = {
        "EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE_HOST": "/private/bindings",
        "EVA_RUNTIME_TENANT_ID": "other" if problem == "scope" else "live-test-tenant",
        "EVA_RUNTIME_INSTALLATION_ID": "live-test-installation",
    }
    monkeypatch.setattr(probe, "_current_environment_value", values.__getitem__)
    monkeypatch.setattr(
        probe,
        "_canary_client_digest",
        lambda: "c" * 64 if problem == "client" else "b" * 64,
    )

    def read(path, *, setting_name, effective_uid):
        assert path == "/private/bindings" and effective_uid == 65532
        return raw

    monkeypatch.setattr(probe, "read_private_runtime_file_bytes", read)
    args = (hashlib.sha256(b"7").hexdigest(), "b" * 64)
    if problem:
        with pytest.raises(probe.ProbeError):
            probe._canary_probe_settings(*args)
    else:
        settings = probe._canary_probe_settings(*args)
        assert settings["user_id"] == 7 and settings["bindings"] == raw
        assert settings["tenant_id"] == "live-test-tenant"


def test_canary_client_has_loopback_only_input_and_no_inherited_secrets(monkeypatch):
    monkeypatch.setattr(probe, "_canary_client_digest", lambda: "b" * 64)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "must-not-inherit")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[:3] == ["/usr/bin/node", "--input-type=module", "-e"]
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        data = json.loads(kwargs["input"])
        assert data["baseUrl"] == "http://127.0.0.1:54321"
        assert data["userId"] == 7 and data["messageId"] == 123
        assert "onDraft:" in command[-1] and "fetchImpl" not in command[-1]
        return subprocess.CompletedProcess(command, 0, json.dumps({"drafts": 1}), "")

    monkeypatch.setattr(probe.subprocess, "run", run)
    turn = {
        "message_id": "telegram:7:123",
        "occurred_at": "2026-09-05T12:00:00Z",
        "text": "probe",
    }
    settings = {"user_id": 7, "client_sha256": "b" * 64}
    assert probe._canary_client_reply(54321, settings, turn)["drafts"] == 1
    with pytest.raises(probe.ProbeError):
        probe._canary_client_reply(0, settings, turn)
    settings["client_sha256"] = "c" * 64
    with pytest.raises(probe.ProbeError):
        probe._canary_client_reply(54321, settings, turn)
    assert len(calls) == 1


def test_count_window_accepts_one_consistent_logical_snapshot() -> None:
    probe._validate_count_window(
        {"public.a": 4, "public.b": 7},
        {"public.a": 5, "public.b": 7},
        {"public.a": 6, "public.b": 7},
    )


@pytest.mark.parametrize("drafts", [0, 1, 3])
def test_canary_delivery_keeps_tool_gated_streaming_separate(drafts):
    probe._verify_canary_delivery(
        {"principal_id": "telegram:7"},
        "telegram:7",
        {
            "drafts": drafts,
            "streamEqualsFinal": drafts > 0,
            "firstDraftMs": 20 if drafts else None,
        },
    )


@pytest.mark.parametrize(
    "change",
    [
        {"drafts": -1},
        {"drafts": True},
        {"streamEqualsFinal": True},
        {"firstDraftMs": 20},
    ],
)
def test_canary_delivery_rejects_false_streaming_evidence(change):
    with pytest.raises(probe.ProbeError, match="draft observation"):
        probe._verify_canary_delivery(
            {"principal_id": "telegram:7"},
            "telegram:7",
            {"drafts": 0, "streamEqualsFinal": False, "firstDraftMs": None, **change},
        )


def test_canary_delivery_rejects_wrong_person_without_drafts():
    with pytest.raises(probe.ProbeError, match="private identity"):
        probe._verify_canary_delivery(
            {"principal_id": "someone-else"},
            "telegram:7",
            {"drafts": 0, "streamEqualsFinal": False, "firstDraftMs": None},
        )


@pytest.mark.parametrize(
    ("before", "restored", "after"),
    [
        ({"public.a": 4}, {"public.a": 7}, {"public.a": 6}),
        ({"public.a": 4}, {"public.b": 4}, {"public.a": 4}),
        ({"public.a": 4}, {"public.a": 4}, {"public.a": 4, "public.b": 0}),
    ],
)
def test_count_window_rejects_out_of_window_or_changed_tables(
    before: dict[str, int], restored: dict[str, int], after: dict[str, int]
) -> None:
    with pytest.raises(probe.ProbeError):
        probe._validate_count_window(before, restored, after)


def test_count_parser_is_closed_and_digest_is_order_independent() -> None:
    assert probe._parse_counts("public.b=2\npublic.a=1\n") == {
        "public.a": 1,
        "public.b": 2,
    }
    assert probe._count_digest({"public.a": 1, "public.b": 2}) == (
        probe._count_digest({"public.b": 2, "public.a": 1})
    )
    with pytest.raises(probe.ProbeError):
        probe._parse_counts("public.a=1\npublic.a=2\n")


def test_current_route_clone_is_logical_read_only_and_disposable() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")

    assert 'CURRENT_ROUTE_CORE = "eva2-managed-b-slot4-core"' in source
    assert 'CURRENT_ROUTE_POSTGRES = "eva2-managed-b-slot4-postgres-1"' in source
    assert (
        'CURRENT_ROUTE_HINDSIGHT = "eva2-managed-b-slot4-hindsight-postgres-1"'
        in source
    )
    assert 'CURRENT_ROUTE_PORT = "44711"' in source
    assert '"/var/lib/eva2/managed-b-slot4/secrets/home-assistant-token"' in source
    assert '"/var/lib/eva2/managed-b-slot4/secrets/dashboard-token"' in source
    assert 'Path("/var/lib/eva2/managed-b-slot4/candidate.env")' in source
    assert '"EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST"' in source
    assert '"EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST"' in source
    assert '"EvaTelegramPrincipalBindings/v2"' in source
    assert "--format=custom" in source
    assert '"--exit-on-error"' in source
    assert '"--no-owner"' in source
    assert '"--no-privileges"' in source
    assert '"down",' in source
    assert '"--volumes",' in source
    assert '"--remove-orphans",' in source
    assert "--clone-current-route" in source
    assert "--core-image" in source
    assert "candidate core image must be an immutable local digest reference" in source
    assert "attempts: int = 3" in source
    assert "--retain-slot" in source
    assert "choices=(2, 3, 4)" in source
    assert "port = 44707 + retained_slot_number" in source
    assert "plaintextDumpsRetained" in source
    assert "CURRENT_ROUTE_HINDSIGHT_NAMESPACE" in source
    assert "retained managed-B Docker volume already exists" in source
    assert '"requiresScopeMigrationBeforeActivation": False' in source
    assert '"sourceRuntimeScope": "legacy-live-test"' in source
    assert '"EVA_DASHBOARD_TENANT_ID"' in source
    assert '"EVA_DASHBOARD_INSTALLATION_ID"' in source
    assert '"EVA_PORTABLE_RESTORE_AUTHORITY_DIR_HOST"' in source
    assert '"EVA_RUNTIME_RESOURCE_BUDGET_FILE_HOST"' in source
    assert "CURRENT_ROUTE_DASHBOARD_TOKEN" in source
    assert "dashboard-authority-readback" in source
    assert "if environment_file.exists() and not keep_retained:" in source
    assert "and staging_created" in source
    assert "docker stop eva2-candidate" not in source
    assert "docker rm eva2-candidate" not in source
    assert "docker volume rm eva2-candidate" not in source


def test_existing_retained_slot_is_never_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    staging_parent = tmp_path / "retained"
    staging_parent.mkdir()
    existing = staging_parent / "managed-b-slot4"
    existing.mkdir()
    marker = existing / "belongs-to-existing-slot"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(probe, "STAGING_PARENT", staging_parent)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(probe, "_live_status", lambda _url: 0)

    status = probe.main(
        clone_current_route=True,
        retain_slot=True,
        retained_slot_number=4,
    )

    assert status == 2
    assert marker.read_text(encoding="utf-8") == "keep"
    assert "retained managed-B slot already exists" in capsys.readouterr().out


def test_dashboard_readback_requires_bound_owner_and_rejects_anonymous(
    tmp_path: Path,
) -> None:
    token = "d" * 32

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Authorization") == f"Bearer {token}":
                self.send_response(200)
            else:
                self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token_file = tmp_path / "dashboard-token"
    token_file.write_text(token, encoding="utf-8")
    try:
        probe._verify_dashboard_access(server.server_address[1], token_file)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
