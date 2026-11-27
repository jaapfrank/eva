from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from eva.adapters.api.appliance_wizard import create_appliance_wizard_app
from eva.application.appliance_setup import build_budget_for_tier, recommend_hardware_tier
from eva.operations.appliance_setup import ApplianceSetupService, is_local_network_client


def _service(tmp_path: Path) -> ApplianceSetupService:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    repo_root = Path(__file__).resolve().parents[3]
    return ApplianceSetupService(
        workspace=workspace,
        runtime_root=runtime_root,
        repo_root=repo_root,
        codex_auth_file=tmp_path / "codex" / "auth.json",
        dry_run_apply=True,
        http_get_json=lambda _url: (0, None),
    )


def _paired_client(service: ApplianceSetupService) -> TestClient:
    code = service.initial_pairing_code()
    assert code and code.startswith("EVA-")
    client = TestClient(
        create_appliance_wizard_app(service),
        headers={"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"},
        client=("127.0.0.1", 51000),
    )
    res = client.post("/internal/v1/appliance/pair", json={"code": code})
    assert res.status_code == 204
    return client


def test_lan_gate_rejects_public_ips_and_rate_limits(tmp_path: Path) -> None:
    assert is_local_network_client("127.0.0.1") is True
    assert is_local_network_client("192.168.1.42") is True
    assert is_local_network_client("10.0.20.5") is True
    assert is_local_network_client("8.8.8.8") is False

    service = _service(tmp_path)
    app = create_appliance_wizard_app(service)

    public_client = TestClient(app, client=("8.8.8.8", 4444))
    assert public_client.get("/pair").status_code == 403

    lan_client = TestClient(
        app,
        headers={"host": "eva.local:8765", "origin": "http://eva.local:8765"},
        client=("192.168.1.25", 45000),
    )
    assert lan_client.get("/internal/v1/appliance/status").status_code == 401
    for _ in range(5):
        bad = lan_client.post("/internal/v1/appliance/pair", json={"code": "EVA-WRONG-CODE-0000"})
        assert bad.status_code == 401
    locked = lan_client.post("/internal/v1/appliance/pair", json={"code": "EVA-WRONG-CODE-0000"})
    assert locked.status_code == 429


def test_hardware_tier_and_budget_contracts() -> None:
    assert recommend_hardware_tier(7.8, 4) == "compact"
    assert recommend_hardware_tier(15.6, 8) == "standard"
    assert recommend_hardware_tier(31.5, 12) == "performance"
    budget = build_budget_for_tier(
        "standard",
        tenant_id="11111111-1111-1111-8111-111111111111",
        installation_id="22222222-2222-2222-8222-222222222222",
        monthly_spend_eur=15.0,
    )
    assert budget.external_monthly_spend_cap_eur_cents == 1500
    assert budget.recall_context_tokens == 3000


def test_full_character_module_and_apply_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _paired_client(service)

    snap = client.get("/internal/v1/appliance/status")
    assert snap.status_code == 200
    assert snap.json()["contract"] == "EvaApplianceSnapshot/v1"

    # Step 1: Profile & hardware
    prof = client.post(
        "/internal/v1/appliance/profile",
        json={
            "owner_name": "Jaap",
            "family_members": "Lola",
            "installation_name": "Thuis bij Jaap",
            "language": "nl-NL",
            "time_zone": "Europe/Amsterdam",
            "tier": "standard",
        },
    )
    assert prof.status_code == 200
    assert prof.json()["profile"]["family_members"] == ["Lola"]

    # Step 2: Character bootstrap, live update, idempotent re-save, and rollback
    pv = client.post(
        "/internal/v1/appliance/character/preview",
        json={"preset_id": "creatief_nieuwsgierig", "display_name": "Eva", "owner_name": "Jaap"},
    )
    assert pv.status_code == 200
    assert "Jaap" in pv.json()["assistant_reply"]

    ch1 = client.post(
        "/internal/v1/appliance/character",
        json={
            "preset_id": "warm_huisgenoot",
            "display_name": "Eva",
            "pronouns": "zij, haar",
            "temperament": "warm, attent, rustig",
            "humour": "lichte humor",
            "voice_qualities": "natuurlijk, helder",
            "proactivity": "balanced",
            "custom_rules": "Houd berichten doordeweeks kort.",
        },
    )
    assert ch1.status_code == 200
    assert ch1.json()["status"] == "bootstrapped"

    # Re-saving identical character fields succeeds idempotently
    ch_same = client.post(
        "/internal/v1/appliance/character",
        json={
            "preset_id": "warm_huisgenoot",
            "display_name": "Eva",
            "pronouns": "zij, haar",
            "temperament": "warm, attent, rustig",
            "humour": "lichte humor",
            "voice_qualities": "natuurlijk, helder",
            "proactivity": "quiet",
        },
    )
    assert ch_same.status_code == 200
    assert ch_same.json()["status"] == "unchanged"

    # Modifying temperament triggers stage -> issue -> activate and enables rollback
    ch2 = client.post(
        "/internal/v1/appliance/character",
        json={
            "preset_id": "nuchter_direct",
            "display_name": "Evi",
            "pronouns": "zij, haar",
            "temperament": "nuchter, direct",
            "humour": "droge humor",
            "voice_qualities": "beknopt, concreet",
            "proactivity": "quiet",
        },
    )
    assert ch2.status_code == 200
    assert ch2.json()["character"]["display_name"] == "Evi"
    assert ch2.json()["character"]["can_rollback"] is True

    rb = client.post("/internal/v1/appliance/character/rollback", json={})
    assert rb.status_code == 200
    assert rb.json()["character"]["display_name"] == "Eva"

    soul_res = client.get("/internal/v1/appliance/character/soul-export")
    assert soul_res.status_code == 200
    assert "# Eva" in soul_res.text

    # Step 3 & 4: Model budget + modules
    b_res = client.post(
        "/internal/v1/appliance/model/budget",
        json={"monthly_spend_eur": 10, "reasoning_effort": "medium"},
    )
    assert b_res.status_code == 200

    tg = client.post(
        "/internal/v1/appliance/modules/telegram",
        json={"enabled": True, "bot_token": "12345678:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPP", "owner_user_id": "99887766"},
    )
    assert tg.status_code == 200
    ha = client.post(
        "/internal/v1/appliance/modules/smart_home",
        json={"enabled": True, "url": "http://192.168.1.50:8123", "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_test", "allow_effects": False},
    )
    assert ha.status_code == 200

    # Step 5: Apply & export
    applied = client.post("/internal/v1/appliance/apply", json={})
    assert applied.status_code == 200
    assert applied.json()["ok"] is True

    runtime_root = tmp_path / "runtime"
    env_text = (runtime_root / "runtime.env").read_text(encoding="utf-8")
    assert "EVA_IDENTITY_PACK_FILE=" in env_text
    assert "EVA_TELEGRAM_BOT_TOKEN_FILE=" in env_text
    assert "EVA_HOME_ASSISTANT_URL=http://192.168.1.50:8123" in env_text
    assert "EVA_HOME_ASSISTANT_EFFECTS_ENABLED=false" in env_text
    assert "EVA_EXTERNAL_PROVIDERS_DISABLED=agentmail" in env_text
    assert (runtime_root / "identity" / "current.json").is_file()
    assert (runtime_root / "identity" / "trust.json").is_file()
    assert not (runtime_root / "identity" / "private-key.bin").exists()

    export = client.get("/internal/v1/appliance/export")
    assert export.status_code == 200
    exp_data = export.json()
    assert exp_data["contract"] == "EvaApplianceExport/v1"
    dumped = json.dumps(exp_data)
    assert "AABBCCDDEEFF" not in dumped
    assert "eyJhbGciOi" not in dumped


def test_paired_lan_dashboard_proxy(tmp_path: Path, monkeypatch) -> None:
    import http.server
    import threading

    received: dict[str, object] = {}

    class _FakeCoreHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received["path"] = self.path
            received["auth"] = self.headers.get("Authorization")
            received["cookie"] = self.headers.get("Cookie")
            received["host"] = self.headers.get("Host")
            body = b"<html><body>Eva Dashboard OK</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "upstream=ignored")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeCoreHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        workspace = tmp_path / "workspace"
        runtime_root = tmp_path / "runtime"
        repo_root = Path(__file__).resolve().parents[3]
        service = ApplianceSetupService(
            workspace=workspace,
            runtime_root=runtime_root,
            repo_root=repo_root,
            codex_auth_file=tmp_path / "codex" / "auth.json",
            core_base_url=f"http://127.0.0.1:{port}",
            dry_run_apply=True,
            http_get_json=lambda _url: (0, None),
        )
        token = "eva-owner-test-token-with-at-least-32-chars"
        tok_path = runtime_root / "secrets" / "dashboard-token"
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(token + "\n", encoding="utf-8")
        tok_path.chmod(0o600)

        app = create_appliance_wizard_app(service)

        unpaired = TestClient(
            app,
            headers={"host": "eva.local:8765"},
            client=("192.168.1.40", 51001),
        )
        redir = unpaired.get("/internal/v1/dashboard", follow_redirects=False)
        assert redir.status_code == 302
        assert redir.headers["location"] == "/pair"
        assert unpaired.get("/internal/v1/dashboard/status").status_code == 401

        code = service.initial_pairing_code()
        assert code
        paired = TestClient(
            app,
            headers={"host": "eva.local:8765", "origin": "http://eva.local:8765"},
            client=("192.168.1.40", 51002),
        )
        assert paired.post("/internal/v1/appliance/pair", json={"code": code}).status_code == 204

        snap = paired.get("/internal/v1/appliance/status")
        assert snap.status_code == 200
        assert snap.json()["runtime"]["dashboard_url"] == "/internal/v1/dashboard"

        proxied = paired.get("/internal/v1/dashboard?view=home")
        assert proxied.status_code == 200
        assert "Eva Dashboard OK" in proxied.text
        assert "set-cookie" not in {k.lower() for k in proxied.headers}
        assert received["path"] == "/internal/v1/dashboard?view=home"
        assert received["auth"] == f"Bearer {token}"
        assert received["cookie"] is None
        assert received["host"] == f"localhost:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_public_lan_ping_and_portable_import(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = create_appliance_wizard_app(service)

    pub = TestClient(app, client=("8.8.8.8", 4444))
    assert pub.get("/internal/v1/appliance/ping").status_code == 403

    lan = TestClient(app, client=("192.168.1.55", 50000))
    ping = lan.get("/internal/v1/appliance/ping")
    assert ping.status_code == 200
    assert ping.headers.get("access-control-allow-origin") == "*"
    pdata = ping.json()
    assert pdata["contract"] == "EvaAppliancePing/v1"
    assert pdata["eva_appliance"] is True
    assert pdata["phase"] == "ready"

    client = _paired_client(service)
    client.post(
        "/internal/v1/appliance/profile",
        json={"owner_name": "Jaap", "family_members": "Lola", "installation_name": "Eva Mini", "tier": "standard"},
    )
    client.post(
        "/internal/v1/appliance/character",
        json={
            "preset_id": "creatief_nieuwsgierig",
            "display_name": "Nova",
            "pronouns": "zij, haar",
            "temperament": "creatief, scherp",
            "humour": "speels",
            "voice_qualities": "levendig, helder",
            "proactivity": "proactive",
        },
    )
    exported = client.get("/internal/v1/appliance/export").json()

    # Import into a brand-new appliance instance (e.g., Monday's laptop or new mini-PC)
    fresh_service = _service(tmp_path / "second")
    fresh_client = _paired_client(fresh_service)
    imp = fresh_client.post("/internal/v1/appliance/import", json=exported)
    assert imp.status_code == 200
    snap = imp.json()["snapshot"]
    assert snap["profile"]["installation_name"] == "Eva Mini"
    assert snap["profile"]["family_members"] == ["Lola"]
    assert snap["character"]["display_name"] == "Nova"
    assert snap["hardware"]["selected_tier"] == "standard"


def test_contact_hub_qr_pairing_telegram_magic_and_streaming_chat(tmp_path: Path, monkeypatch) -> None:
    import http.server
    import threading
    from eva.adapters.credentials.telegram_bindings import load_principal_bindings

    last_stage_headers: dict[str, object] = {}

    class _StreamingCoreHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            last_stage_headers["auth"] = self.headers.get("Authorization")
            last_stage_headers["lan_proxy"] = self.headers.get("X-HoloStage-Lan-Proxy")
            last_stage_headers["cookie"] = self.headers.get("Cookie")
            body = b"<html><body>HoloStage Pocket OK</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "eva_holostage_session=stg_ok; Path=/internal/v1/dashboard/holostage; HttpOnly")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            lines = (
                json.dumps({"contract": "EvaTextDelta/v1", "text": "Hoi Jaap! "}) + "\n"
                + json.dumps({"contract": "EvaTextDelta/v1", "text": "Ons contact werkt direct."}) + "\n"
                + json.dumps(
                    {
                        "contract": "EvaInternalGroundedReply/v1",
                        "conversation_id": "conv-1",
                        "evidence_memory_ids": [],
                        "generated_at": "2026-11-26T18:00:00Z",
                        "message_id": "msg-1",
                        "provider": "openai-codex",
                        "text": "Hoi Jaap! Ons contact werkt direct.",
                        "text_sha256": "0" * 64,
                    }
                )
                + "\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("X-Eva-Input-Receipt", "Ontvangen")
            self.end_headers()
            self.wfile.write(lines)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _StreamingCoreHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        workspace = tmp_path / "workspace"
        runtime_root = tmp_path / "runtime"
        repo_root = Path(__file__).resolve().parents[3]
        service = ApplianceSetupService(
            workspace=workspace,
            runtime_root=runtime_root,
            repo_root=repo_root,
            codex_auth_file=tmp_path / "codex" / "auth.json",
            core_base_url=f"http://127.0.0.1:{port}",
            dry_run_apply=True,
            http_get_json=lambda _url: (200, {"ready": True, "status": "healthy"}),
        )
        monkeypatch.setattr(
            ApplianceSetupService,
            "_fetch_telegram_bot_username",
            staticmethod(lambda _tok: "EvaThuisBot"),
        )

        client = TestClient(
            create_appliance_wizard_app(service),
            headers={"host": "192.168.1.45:8765", "origin": "http://192.168.1.45:8765"},
            client=("192.168.1.20", 51010),
        )
        code = service.initial_pairing_code()
        assert code
        assert client.post("/internal/v1/appliance/pair", json={"code": code}).status_code == 204
        assert client.post("/internal/v1/appliance/apply", json={}).status_code == 200

        env_text = (runtime_root / "runtime.env").read_text(encoding="utf-8")
        assert "EVA_MODEL_PROVIDER_DIRECTORY=" in env_text
        assert "EVA_LIVING_CORE_URL=" in env_text

        # 1. Eva Pocket QR pairing + revoke
        dev = client.post(
            "/internal/v1/appliance/contact/device",
            json={"mode": "pocket", "label": "iPhone van Jaap"},
        )
        assert dev.status_code == 200
        ddata = dev.json()
        assert ddata["url"].startswith("http://192.168.1.45:8765/internal/v1/dashboard/holostage?mode=pocket#stg_")
        assert ddata["qr_svg"].startswith("<svg ")
        grant_id = ddata["grant"]["grant_id"]

        # Unpaired phone on LAN opening /internal/v1/dashboard/holostage gets scoped pass-through (NO owner Bearer token!)
        phone = TestClient(
            create_appliance_wizard_app(service),
            headers={"host": "192.168.1.45:8765"},
            client=("192.168.1.88", 51011),
        )
        phone.cookies.set("eva_holostage_session", "stg_testcookie")
        stage_res = phone.get("/internal/v1/dashboard/holostage?mode=pocket")
        assert stage_res.status_code == 200
        assert "HoloStage Pocket OK" in stage_res.text
        assert last_stage_headers["auth"] is None
        assert last_stage_headers["lan_proxy"] == "1"
        assert last_stage_headers["cookie"] == "eva_holostage_session=stg_testcookie"
        assert "eva_holostage_session=" in (stage_res.headers.get("set-cookie") or "")

        rev = client.post(f"/internal/v1/appliance/contact/device/{grant_id}/revoke", json={})
        assert rev.status_code == 200
        assert rev.json()["device_grants"] == []

        # 2. Single-use secondary owner browser QR invite
        inv = client.post(
            "/internal/v1/appliance/contact/device",
            json={"mode": "owner_browser", "label": "Tweede Laptop"},
        )
        assert inv.status_code == 200
        inv_code = inv.json()["url"].split("#", 1)[1]
        second_browser = TestClient(
            create_appliance_wizard_app(service),
            headers={"host": "192.168.1.45:8765", "origin": "http://192.168.1.45:8765"},
            client=("192.168.1.99", 51012),
        )
        assert second_browser.post("/internal/v1/appliance/pair", json={"code": inv_code}).status_code == 204
        # Replay of burned single-use invite must fail
        third_browser = TestClient(
            create_appliance_wizard_app(service),
            headers={"host": "192.168.1.45:8765", "origin": "http://192.168.1.45:8765"},
            client=("192.168.1.100", 51013),
        )
        assert third_browser.post("/internal/v1/appliance/pair", json={"code": inv_code}).status_code == 401

        # 3. 1-Scan Zero-User-ID Telegram pairing for Owner (Jaap) + Household (Lola)
        tg_start = client.post(
            "/internal/v1/appliance/contact/telegram/start",
            json={
                "bot_token": "12345678:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPP",
                "role": "owner",
                "person_name": "Jaap",
                "skip_poller": True,
            },
        )
        assert tg_start.status_code == 200
        assert tg_start.json()["deep_link"].startswith("https://t.me/EvaThuisBot?start=eva_")
        assert tg_start.json()["qr_svg"].startswith("<svg ")

        service._complete_telegram_magic_link(uid=111222333, person_name="Jaap", role="owner", tg_username="jaapf")
        service._complete_telegram_magic_link(uid=444555666, person_name="Lola", role="household", tg_username="lolaf")

        bindings_file = runtime_root / "secrets" / "telegram-principal-bindings.json"
        loaded_bindings = load_principal_bindings(str(bindings_file))
        assert loaded_bindings.resolve(111222333, "111222333") is not None
        lola_principal = loaded_bindings.resolve(444555666, "444555666")
        assert lola_principal is not None and lola_principal.startswith("household:lola:")

        tg_status = client.get("/internal/v1/appliance/contact/telegram/status")
        assert len(tg_status.json()["contacts"]) == 2

        rem = client.post("/internal/v1/appliance/contact/telegram/444555666/remove", json={})
        assert rem.status_code == 200
        assert len(rem.json()["contacts"]) == 1

        # 4. Live Contact Test + Streaming NDJSON Proxy
        live_test = client.post("/internal/v1/appliance/contact/test", json={"text": "Hoi Eva!"})
        assert live_test.status_code == 200
        assert live_test.json()["reply_text"] == "Hoi Jaap! Ons contact werkt direct."
        assert live_test.json()["provider"] == "openai-codex"

        stream_res = client.post(
            "/internal/v1/dashboard/conversation/respond",
            headers={"Accept": "application/x-ndjson"},
            json={"text": "Stream test"},
        )
        assert stream_res.status_code == 200
        assert stream_res.headers.get("x-eva-input-receipt") == "Ontvangen"
        assert "EvaTextDelta/v1" in stream_res.text
        assert "EvaInternalGroundedReply/v1" in stream_res.text
    finally:
        server.shutdown()
        server.server_close()


def _make_synthetic_pcm16_b64(freq_hz: float = 185.0, duration_sec: float = 0.5) -> str:
    import base64
    import math
    import struct

    n_samples = int(16000 * duration_sec)
    samples = [
        int(14000 * math.sin(2.0 * math.pi * freq_hz * (i / 16000.0)) + 4000 * math.sin(2.0 * math.pi * (freq_hz * 2.1) * (i / 16000.0)))
        for i in range(n_samples)
    ]
    raw = struct.pack(f"<{n_samples}h", *samples)
    return base64.b64encode(raw).decode("ascii")


def test_immersive_3d_awakening_reversible_preferences_and_guest_mode(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = create_appliance_wizard_app(service)

    # Unpaired client cannot fetch 3D stage assets
    unpaired = TestClient(app, client=("192.168.1.40", 52000))
    assert unpaired.get("/internal/v1/appliance/stage-assets/eva.glb").status_code == 401

    client = _paired_client(service)

    # GET / serves fullscreen 3D Immersive Stage; GET /beheer serves classic 6-tab wizard
    root_html = client.get("/")
    assert root_html.status_code == 200
    assert "eva.glb" in root_html.text
    assert "/beheer" in root_html.text

    beheer_html = client.get("/beheer")
    assert beheer_html.status_code == 200
    assert "1. Jij &amp; Hardware" in beheer_html.text

    # 3D assets load locally with zero external CDN dependencies
    glb_res = client.get("/internal/v1/appliance/stage-assets/eva.glb")
    assert glb_res.status_code == 200
    assert len(glb_res.content) > 100_000
    three_res = client.get("/internal/v1/appliance/stage-assets/three.module.min.js")
    assert three_res.status_code == 200

    # 1. Conversational Awakening + 16 kHz local voiceprint enrollment
    pcm_jaap = _make_synthetic_pcm16_b64(145.0, 0.55)
    t1 = client.post(
        "/internal/v1/appliance/living/turn",
        json={
            "text": "Hoi Eva! Ik ben Jaap en ik woon hier samen met mijn dochter Lola.",
            "audio_pcm_b64": pcm_jaap,
        },
    )
    assert t1.status_code == 200
    d1 = t1.json()
    assert d1["ok"] is True
    assert d1["voice_enrolled"] is True
    assert "Lola" in d1["stage"]["household"]["family_members"]
    assert d1["stage"]["awakening_completed"] is True
    owner_entry = next(m for m in d1["stage"]["household"]["members"] if m["role"] == "gezinshoofd")
    assert owner_entry["voice_enrolled"] is True

    # 2. Live 4-Sphere Character Tasting
    t_sphere = client.post(
        "/internal/v1/appliance/living/turn",
        json={"preset_id": "nuchter_direct"},
    )
    assert t_sphere.status_code == 200
    assert t_sphere.json()["stage"]["eva"]["preset_id"] == "nuchter_direct"

    # 3. Natural Dutch Household Rule + 1-click Undo ("Ik heb je gehoord")
    t_rule = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Na 21:00 uur alleen storen als het echt dringend is."},
    )
    assert t_rule.status_code == 200
    d_rule = t_rule.json()
    assert any("21:00" in r for r in d_rule["stage"]["household"]["custom_rules"])
    rule_change_id = next(
        c["change_id"] for c in d_rule["applied_changes"] if c["kind"] == "household_rule"
    )

    undone = client.post(f"/internal/v1/appliance/living/undo/{rule_change_id}", json={})
    assert undone.status_code == 200
    assert not any("21:00" in r for r in undone.json()["stage"]["household"]["custom_rules"])

    # 4. 1-Phrase "Visite / Gastmodus" locks BiometricPresenceFusion Privacy Shield
    t_guest_on = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Eva, we krijgen visite!"},
    )
    assert t_guest_on.status_code == 200
    st_guest = t_guest_on.json()["stage"]
    assert st_guest["household"]["guest_mode_active"] is True
    assert st_guest["household"]["privacy_shield_active"] is True

    t_guest_off = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "De visite is weer weg."},
    )
    assert t_guest_off.status_code == 200
    assert t_guest_off.json()["stage"]["household"]["guest_mode_active"] is False


def test_personal_member_welcome_portal_for_lola_and_guest_isolation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = create_appliance_wizard_app(service)
    owner_client = _paired_client(service)

    wlk = owner_client.post(
        "/internal/v1/appliance/living/member-welcome",
        json={"member_name": "Lola", "role": "gezin"},
    )
    assert wlk.status_code == 200
    wlk_data = wlk.json()
    assert "/welkom/wlk_" in wlk_data["url"]
    assert wlk_data["qr_svg"].startswith("<svg ")
    raw_token = wlk_data["url"].rsplit("/", 1)[-1]

    # Separate unpaired phone browser for Lola on LAN
    lola_phone = TestClient(
        app,
        headers={"host": "192.168.1.45:8765", "origin": "http://192.168.1.45:8765"},
        client=("192.168.1.91", 53000),
    )

    page = lola_phone.get(f"/welkom/{raw_token}")
    assert page.status_code == 200
    assert "eva.glb" in page.text

    # Lola's phone can load 3D Eva assets via welcome cookie, but CANNOT access owner admin endpoints
    assert lola_phone.get("/internal/v1/appliance/stage-assets/three.module.min.js").status_code == 200
    assert lola_phone.get("/internal/v1/appliance/status").status_code == 401
    assert lola_phone.post("/internal/v1/appliance/living/turn", json={"text": "Hoi"}).status_code == 401

    info = lola_phone.get(f"/internal/v1/appliance/welcome/{raw_token}")
    assert info.status_code == 200
    assert info.json()["member_name"] == "Lola"

    done = lola_phone.post(
        f"/internal/v1/appliance/welcome/{raw_token}/complete",
        json={
            "wishes": "Houd antwoorden lekker vlot en gezellig",
            "audio_pcm_b64": _make_synthetic_pcm16_b64(235.0, 0.55),
        },
    )
    assert done.status_code == 200
    done_data = done.json()
    assert done_data["voice_enrolled"] is True
    assert "/internal/v1/dashboard/holostage?mode=pocket#stg_" in done_data["pocket_path"]

    # Verify owner stage now reflects Lola's enrolled voiceprint and personal wish
    owner_state = owner_client.get("/internal/v1/appliance/living/state").json()
    lola_member = next(
        m for m in owner_state["household"]["members"] if m["display_name"].lower() == "lola"
    )
    assert lola_member["voice_enrolled"] is True
    assert any("Voorkeur van Lola:" in r for r in owner_state["household"]["custom_rules"])
    lola_grant = next(
        g for g in owner_state["device_grants"] if "Lola" in g["label"]
    )
    assert lola_grant["allow_owner_voice"] is False
    assert lola_grant["allow_private_cards_when_alone"] is True


def test_wizard_voice_verification_and_smart_home_conjuring_and_env(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _paired_client(service)

    # Wizard starts incomplete so new users always get the 6-step guided onboarding
    st0 = client.get("/internal/v1/appliance/living/state").json()
    assert st0["wizard_completed"] is False

    # Step 3: Explicit voice enrollment + live voice recognition check (without mutating profile)
    jaap_pcm = _make_synthetic_pcm16_b64(135.0, 0.6)
    enr = client.post(
        "/internal/v1/appliance/living/voice-enroll",
        json={"audio_pcm_b64": jaap_pcm},
    )
    assert enr.status_code == 200
    assert enr.json()["ok"] is True

    chk = client.post(
        "/internal/v1/appliance/living/voice-check",
        json={"audio_pcm_b64": jaap_pcm},
    )
    assert chk.status_code == 200
    match = chk.json()["match"]
    assert match["status"] == "household_voice_candidate"
    assert match["display_name"] == "Jaap"
    assert match["similarity_pct"] >= 85

    # Mentioning Home Assistant or a Long-Lived Token automatically conjures the secure smart_home card
    ha_turn = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Kan je verbinding maken met Home Assistant? Ik heb een long living token."},
    )
    assert ha_turn.status_code == 200
    assert ha_turn.json()["conjure_panel"] == "smart_home"

    # 1-Click Smart Home connect writes all required HomeAssistantProviderFactory env vars
    conn_res = client.post(
        "/internal/v1/appliance/living/smart-home/connect",
        json={
            "url": "http://10.0.20.74:8123",
            "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9_long_lived_test_token",
            "allow_effects": True,
            "skip_probe": True,
        },
    )
    assert conn_res.status_code == 200
    assert conn_res.json()["ok"] is True
    env_text = (tmp_path / "runtime" / "runtime.env").read_text(encoding="utf-8")
    assert "EVA_HOME_ASSISTANT_URL=http://10.0.20.74:8123" in env_text
    assert "EVA_HOME_ASSISTANT_API_ENABLED=true" in env_text
    assert "EVA_HOME_ASSISTANT_EFFECTS_ENABLED=true" in env_text
    assert "EVA_HOME_ASSISTANT_EFFECT_ALLOWLIST=" in env_text
    assert "EVA_HOME_ASSISTANT_EFFECT_PRINCIPAL_SHA256_ALLOWLIST=" in env_text

    # Step 6: Completing wizard sets wizard_completed=True
    fin = client.post("/internal/v1/appliance/living/wizard-state", json={"completed": True})
    assert fin.status_code == 200
    assert fin.json()["wizard_completed"] is True


def test_persistent_sessions_loopback_auto_pair_universal_vault_and_self_building(tmp_path: Path) -> None:
    import asyncio
    import threading
    from eva.adapters.connectors.mcp_gateway import McpGatewayConfig, McpGatewayToolAdapter
    from eva.operations.appliance_mcp_gateway import (
        ApplianceMcpGatewayServer,
        UniversalIntegrationHub,
        build_appliance_gateway_catalog,
    )
    from eva.providers.mcp_gateway import parse_mcp_gateway_catalog

    service1 = _service(tmp_path)
    app1 = create_appliance_wizard_app(service1)

    # 1. Loopback auto-pairing works on 127.0.0.1 and persists across service restarts!
    local_client = TestClient(
        app1,
        headers={"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"},
        client=("127.0.0.1", 52001),
    )
    assert local_client.post("/internal/v1/appliance/pair-local", json={}).status_code == 204
    assert local_client.get("/internal/v1/appliance/living/state").status_code == 200
    saved_cookie = local_client.cookies.get("eva_appliance_session")
    assert saved_cookie

    # LAN clients (non-loopback) are strictly forbidden from pair-local
    lan_client = TestClient(
        app1,
        headers={"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"},
        client=("192.168.1.99", 52002),
    )
    assert lan_client.post("/internal/v1/appliance/pair-local", json={}).status_code == 403

    # Simulate service restart: brand-new ApplianceSetupService instance reloads sessions.private.json
    service2 = _service(tmp_path)
    app2 = create_appliance_wizard_app(service2)
    restarted_client = TestClient(
        app2,
        headers={"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"},
        client=("127.0.0.1", 52003),
    )
    restarted_client.cookies.set("eva_appliance_session", saved_cookie)
    assert restarted_client.get("/internal/v1/appliance/living/state").status_code == 200

    # 2. Asking Eva to connect UniFi Protect / Agenda conjures universal integrations & sets pending request
    unifi_turn = restarted_client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Eva, maak verbinding met mijn UniFi Protect camera's."},
    )
    assert unifi_turn.status_code == 200
    assert unifi_turn.json()["conjure_panel"] == "integrations"
    pend = unifi_turn.json()["stage"]["integrations"]["pending_request"]
    assert pend["service_kind"] == "unifi_protect"

    # 3. Connecting UniFi Network & Agenda stores AES-256-GCM credentials without exposing secrets in state
    conn_unifi = restarted_client.post(
        "/internal/v1/appliance/living/integrations/connect",
        json={
            "service_id": "unifi_network",
            "title": "UniFi Thuisnetwerk",
            "service_kind": "unifi_network",
            "base_url": "https://10.0.20.1",
            "auth_mode": "api_key",
            "secret": "unifi_super_secret_key_998877",
            "skip_probe": True,
        },
    )
    assert conn_unifi.status_code == 200
    stage_json = json.dumps(conn_unifi.json()["stage"])
    assert "unifi_super_secret_key_998877" not in stage_json
    assert any(s["service_id"] == "unifi_network" for s in conn_unifi.json()["stage"]["integrations"]["services"])

    # Pre-LLM /kluis command stores secret directly in vault
    kluis_turn = restarted_client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "/kluis unifi_protect protect_secret_token_12345"},
    )
    assert kluis_turn.status_code == 200
    assert "protect_secret_token_12345" not in json.dumps(kluis_turn.json())

    # 4. Verify sealed EvaMcpGatewayProvider/v4 catalog & Eva's self-programming capability sandbox
    hub = UniversalIntegrationHub(tmp_path / "runtime")
    gw_srv = ApplianceMcpGatewayServer(
        ("127.0.0.1", 0),
        hub=hub,
        gateway_token="gw_test_token_with_32_characters_minimum",
        registration_token="reg_test_token_with_32_characters_minimum",
    )
    gw_port = gw_srv.server_address[1]
    threading.Thread(target=gw_srv.serve_forever, daemon=True).start()
    try:
        catalog_dict = build_appliance_gateway_catalog()
        specs = parse_mcp_gateway_catalog(json.dumps(catalog_dict))
        adapter = McpGatewayToolAdapter(
            McpGatewayConfig(
                base_url=f"http://127.0.0.1:{gw_port}",
                bearer_token="gw_test_token_with_32_characters_minimum",
                allow_plain_http=True,
            ),
            specs,
        )
        verified_hashes = asyncio.run(adapter.verify_catalog())
        assert set(verified_hashes) == {
            "discover_network_and_vault",
            "request_owner_connection",
            "use_vault_service",
            "build_and_run_capability",
        }

        # Eva synthesizes, AST-validates, sandbox-tests, saves, and runs a brand-new Python capability!
        create_res = hub.handle_capability_tool(
            {
                "action": "create_or_update",
                "capability_id": "zonnepanelen_opbrengst",
                "title": "Zonnepanelen Dagopbrengst",
                "description": "Berekent netto kWh en slaat laatste stand op",
                "service_id": "",
                "python_code": (
                    "def run_capability(ctx, params):\n"
                    "    prev = ctx.get_state().get('runs', 0) + 1\n"
                    "    ctx.set_state({'runs': prev})\n"
                    "    watt = float(params.get('watt', 1200))\n"
                    "    return {'kwh_schatting': round(watt * 4.5 / 1000.0, 2), 'run_nr': prev}\n"
                ),
                "params_json": json.dumps({"watt": 2000}),
            }
        )
        assert create_res["ok"] is True
        assert create_res["test_output"]["kwh_schatting"] == 9.0

        run_res = hub.handle_capability_tool(
            {
                "action": "run",
                "capability_id": "zonnepanelen_opbrengst",
                "title": "",
                "description": "",
                "service_id": "",
                "python_code": "",
                "params_json": json.dumps({"watt": 3000}),
            }
        )
        assert run_res["ok"] is True
        assert run_res["output"]["kwh_schatting"] == 13.5
        assert run_res["output"]["run_nr"] == 2

        # Ensure /rpc float outputs sanitize cleanly for canonical_sha256 in McpGatewayToolAdapter
        from eva.contracts.canonical_json import canonical_sha256
        rpc_out = adapter._call_tool(
            "build_and_run_capability",
            {
                "action": "run",
                "capability_id": "zonnepanelen_opbrengst",
                "title": "",
                "description": "",
                "service_id": "",
                "python_code": "",
                "params_json": json.dumps({"watt": 3000}),
            },
            "rpc_test_id_01",
        )
        assert canonical_sha256(rpc_out)
        assert rpc_out["output"]["kwh_schatting"] == "13.5"
    finally:
        gw_srv.shutdown()
        gw_srv.server_close()


def test_beheer_js_integrity_dual_unifi_pair_and_pending_expiry(tmp_path: Path) -> None:
    import time
    from eva.operations.appliance_mcp_gateway import UniversalIntegrationHub

    service = _service(tmp_path)
    client = _paired_client(service)

    # 1. /beheer HTML must preserve escaped '\\n' inside JS stream parser (no unescaped newline SyntaxError)
    beheer = client.get("/beheer")
    assert beheer.status_code == 200
    assert "buf.indexOf('\\n')" in beheer.text

    # 2. Pure observational LAN question must NOT prematurely conjure a credential drawer
    obs_turn = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Hoi Eva, zie jij mijn Home Assistant en UniFi op het netwerk?"},
    )
    assert obs_turn.status_code == 200
    assert obs_turn.json()["conjure_panel"] is None

    # 3. Combined UniFi Network + UniFi Protect request opens aligned preset tab
    both_turn = client.post(
        "/internal/v1/appliance/living/turn",
        json={"text": "Eva, maak verbinding met mijn UniFi netwerk en UniFi Protect."},
    )
    assert both_turn.status_code == 200
    assert both_turn.json()["conjure_panel"] == "integrations"
    assert both_turn.json()["conjure_preset"] == "unifi_network"

    # 4. 1-Click dual UniFi connect activates BOTH unifi_network and unifi_protect in the AES-256 Vault
    dual_res = client.post(
        "/internal/v1/appliance/living/integrations/connect",
        json={
            "service_id": "unifi_network",
            "title": "UniFi Netwerk & Wi-Fi",
            "service_kind": "unifi_network",
            "base_url": "https://10.0.20.1",
            "auth_mode": "api_key",
            "secret": "unifi_os_shared_api_key_abcdef",
            "also_connect_unifi_pair": True,
            "skip_probe": True,
        },
    )
    assert dual_res.status_code == 200
    svcs = {s["service_id"]: s for s in dual_res.json()["stage"]["integrations"]["services"]}
    assert "unifi_network" in svcs and svcs["unifi_network"]["has_credential"] is True
    assert "unifi_protect" in svcs and svcs["unifi_protect"]["has_credential"] is True

    # 5. Expired pending-connection.json is automatically cleaned up so it never hijacks unrelated turns
    hub = UniversalIntegrationHub(tmp_path / "runtime")
    hub.record_connection_request({"service_id": "oud_verzoek", "service_kind": "calendar"})
    raw_pend = json.loads(hub.pending_file.read_text(encoding="utf-8"))
    raw_pend["expires_epoch"] = int(time.time()) - 5
    hub.pending_file.write_text(json.dumps(raw_pend), encoding="utf-8")
    assert hub.get_pending_connection() is None
    assert not hub.pending_file.exists()


def test_realtime_voice_models_vault_keys_and_turn_streaming_fillers(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _paired_client(service)

    # 1. Stage exposes gpt-realtime-2.1, gpt-realtime-2.1-mini, and gemini-3.8-live
    st = client.get("/internal/v1/appliance/living/state").json()
    rt = st["realtime_voice"]
    assert rt["selected_model"] == "gpt-realtime-2.1"
    model_ids = [m["model_id"] for m in rt["models"]]
    assert model_ids == ["gpt-realtime-2.1", "gpt-realtime-2.1-mini", "gemini-3.8-live"]

    # 2. Switching to gemini-3.8-live and storing Gemini key in AES-256 Vault keeps secret out of state
    cfg_res = client.post(
        "/internal/v1/appliance/living/realtime/config",
        json={
            "model": "gemini-3.8-live",
            "voice": "Aoede",
            "api_key": "AIzaSy_test_gemini_live_secret_key_999",
        },
    )
    assert cfg_res.status_code == 200
    cfg_json = cfg_res.json()
    assert cfg_json["realtime_voice"]["selected_model"] == "gemini-3.8-live"
    assert cfg_json["stage"]["realtime_voice"]["selected_model"] == "gemini-3.8-live"
    assert cfg_json["stage"]["realtime_voice"]["selected_voice"] == "Aoede"
    assert cfg_json["realtime_voice"]["has_gemini_key"] is True
    assert "AIzaSy_test_gemini_live_secret_key_999" not in json.dumps(cfg_json)

    # Switch back to gpt-realtime-2.1-mini and verify stage persistence across reload
    mini_res = client.post(
        "/internal/v1/appliance/living/realtime/config",
        json={"model": "gpt-realtime-2.1-mini", "voice": "coral"},
    )
    assert mini_res.status_code == 200
    assert mini_res.json()["realtime_voice"]["selected_model"] == "gpt-realtime-2.1-mini"
    assert mini_res.json()["realtime_voice"]["selected_voice"] == "coral"
    reloaded_st = client.get("/internal/v1/appliance/living/state").json()
    assert reloaded_st["realtime_voice"]["selected_model"] == "gpt-realtime-2.1-mini"
    assert reloaded_st["realtime_voice"]["selected_voice"] == "coral"

    # 3. Streaming NDJSON turn returns speech clauses + final payload cleanly
    stream_res = client.post(
        "/internal/v1/appliance/living/turn-stream",
        json={"text": "Eva, we krijgen visite!"},
    )
    assert stream_res.status_code == 200
    events = [json.loads(line) for line in stream_res.text.splitlines() if line.strip()]
    assert any(e["type"] == "speech_clause" for e in events)
    assert any(e["type"] == "final" and e["ok"] is True for e in events)

    # 4. Main Display (UniFi Connect Display) HTTPS CA cert + 365-day EVA-DISP kiosk pairing
    ca_res = client.get("/internal/v1/appliance/lan-ca.crt")
    assert ca_res.status_code == 200
    assert "BEGIN CERTIFICATE" in ca_res.text

    md_res = client.post(
        "/internal/v1/appliance/living/main-display",
        json={
            "label": "Keuken UC-Display-SE-21",
            "room": "keuken",
            "camera_entity_id": "camera.keuken_protect",
            "wake_entity_id": "notify.keuken_scherm",
        },
    )
    assert md_res.status_code == 200
    md_json = md_res.json()
    assert md_json["pairing_code"].startswith("EVA-DISP-")
    assert "https://" in md_json["kiosk_https_url"] and "?kiosk=1#EVA-DISP-" in md_json["kiosk_https_url"]
    assert md_json["stage"]["main_display"]["camera_entity_id"] == "camera.keuken_protect"

    from fastapi.testclient import TestClient
    from eva.adapters.api.appliance_wizard import create_appliance_wizard_app

    kiosk_client = TestClient(create_appliance_wizard_app(service), base_url="http://127.0.0.1:8765")
    pair_kiosk = kiosk_client.post(
        "/internal/v1/appliance/pair",
        json={"code": md_json["pairing_code"]},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert pair_kiosk.status_code == 204
    assert "Max-Age=31536000" in pair_kiosk.headers.get("set-cookie", "")





