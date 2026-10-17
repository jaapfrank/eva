"""Behavioral regressions for the fixed E3 configuration/release boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


def test_shared_settings_imports_without_runtime_http_or_voice_composition():
    code = "from eva.bootstrap import RuntimeSettings; import sys; assert RuntimeSettings.__module__ == 'eva.bootstrap.runtime_settings'; assert 'eva.bootstrap.runtime' not in sys.modules; assert 'fastapi' not in sys.modules; assert not any(n.startswith('eva.voice_service') for n in sys.modules)"
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=os.environ.copy())


def test_digital_release_ignores_ui_composition_but_seals_shared_execution(tmp_path, monkeypatch):
    from eva.bootstrap import delegated_digital_profile as profiles
    from tests.contract.test_delegated_digital_work import profile
    host = profile()
    root = profiles._ROOT
    for name in {*profiles._SOURCES, *(name for name, _ in host.execution_source_sha256)}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    monkeypatch.setattr(profiles, "_ROOT", tmp_path)
    reference = host.release
    ui = tmp_path / "bootstrap/runtime.py"
    ui.write_text("# unrelated dashboard or voice composition change\n")
    assert host.release == reference
    settings = tmp_path / "bootstrap/runtime_settings.py"
    settings.write_bytes(settings.read_bytes() + b"\n# shared configuration changed\n")
    assert host.release.worker_program_sha256 != reference.worker_program_sha256
    execution = tmp_path / host.execution_source_sha256[0][0]
    execution.write_bytes(execution.read_bytes() + b"\n# executable changed\n")
    with pytest.raises(ValueError, match="execution source differs"):
        host.release


def test_public_source_work_uses_work_model_not_contact_model(monkeypatch):
    lookup = pytest.importorskip("eva.bootstrap.public_lookup", reason="Optional public lookup source is owned by the concurrent WSL executor; E42 model-role delta is recorded separately.")
    build_public_lookup = lookup.build_public_lookup
    from eva.adapters.model import public_lookup
    seen = []
    monkeypatch.setattr(public_lookup, "PublicLookupModel", lambda **kw: seen.append(kw) or object())
    settings = SimpleNamespace(conversation_contact_model="gpt-6-sol",
        conversation_contact_reasoning_effort="none", conversation_model="gpt-6-astra",
        conversation_reasoning_effort="medium", runtime_resource_budget=object(),
        conversation_auth_file="/unread-test-auth")
    actions = build_public_lookup(settings, object(), object())
    assert actions is not None
    assert seen[0]["model"] == "gpt-6-astra"
    assert seen[0]["reasoning_effort"] == "medium"


def test_operator_dry_run_still_serializes_shared_import_receipt(tmp_path, monkeypatch, capsys):
    from tools import import_component_release as cli
    monkeypatch.setattr(cli, "prepare", lambda *args: (tmp_path / "source", b"before", b"after", {"state": "validated"}))
    monkeypatch.setattr(sys, "argv", ["import-component", "--package", "package", "--review", "review", "--checkout", str(tmp_path)])
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"state": "validated"}


def test_runtime_updater_default_command_retains_capture_defaults(monkeypatch):
    from eva.operations.runtime_image_update import _command
    from eva.operations import component_evaluation
    calls = []
    def capture(args, *, deadline, limit):
        calls.append((args, deadline, limit))
        return {"exit_code": 0, "timed_out": False, "stdout_truncated": False, "stderr_truncated": False, "stdout": "ok"}
    monkeypatch.setattr(component_evaluation, "_capture", capture)
    assert _command(["read-only-test"]) == "ok"
    assert calls == [(["read-only-test"], 15, 1024 * 1024)]
    assert _command(["apply-test"], deadline=None) == "ok"
    assert calls[-1][1:] == (None, 1024 * 1024)
