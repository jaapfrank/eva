from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[3] / "src" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import render_household_voice_ingress as ingress_mod  # noqa: E402


def test_resolve_source_environment_falls_back_to_active_eva2_telegram_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs):
        calls.append(cmd)
        if cmd[1] == "is-active":
            unit = cmd[2]
            out = "active\n" if unit == "eva2-telegram.service" else "inactive\n"
            return subprocess.CompletedProcess(cmd, 0 if unit == "eva2-telegram.service" else 3, stdout=out, stderr="")
        if cmd[1] == "show":
            assert cmd[2] == "eva2-telegram.service"
            return subprocess.CompletedProcess(cmd, 0, stdout="EVA2_CANARY_URL=http://127.0.0.1:44710\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(ingress_mod.subprocess, "run", fake_run)
    env = ingress_mod.resolve_source_environment("auto")
    assert "EVA2_CANARY_URL=http://127.0.0.1:44710" in env


def test_resolve_source_environment_fails_closed_when_zero_or_both_services_active(monkeypatch: pytest.MonkeyPatch) -> None:
    for active_set in (set(), {"eva2-telegram.service", "eva.service"}):
        def fake_run(cmd: list[str], _active=active_set, **kwargs):
            unit = cmd[2]
            is_up = unit in _active
            return subprocess.CompletedProcess(cmd, 0 if is_up else 3, stdout="active\n" if is_up else "inactive\n", stderr="")

        monkeypatch.setattr(ingress_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="Eva ingress is not active"):
            ingress_mod.resolve_source_environment("auto")


def test_rendered_owner_voice_ingress_supports_livekit_v1_signaling_and_validates_nginx(tmp_path: Path) -> None:
    for sub in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
        (tmp_path / sub).mkdir()
    conf_text = ingress_mod.render(
        environment="EVA2_CANARY_URL=http://127.0.0.1:44710",
        listen="10.0.20.46",
        home_assistant="10.0.20.74",
        runtime=str(tmp_path),
        owner_voice=True,
    )
    assert r'location ~ "^/owner-voice/livekit/rtc(?:/v1)?(?:/validate)?$"' in conf_text
    conf_path = tmp_path / "nginx.conf"
    conf_path.write_text(conf_text, encoding="utf-8")
    nginx_bin = Path("/opt/eva2/voice-ingress/nginx")
    if nginx_bin.is_file():
        res = subprocess.run(
            [str(nginx_bin), "-t", "-c", str(conf_path), "-e", "stderr"],
            check=False, capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr

    unit_text = (Path(__file__).resolve().parents[3] / "deploy/home-assistant/eva2-voice-ingress.service").read_text(
        encoding="utf-8"
    )
    assert "BindsTo=eva2-telegram.service" in unit_text
    assert "PartOf=eva2-telegram.service" in unit_text
    assert "After=eva2-telegram.service network-online.target" in unit_text
