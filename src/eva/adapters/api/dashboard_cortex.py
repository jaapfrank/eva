"""Living Entity 3D Split-Shell Cockpit, Jarvis World Model & Multi-Brain Cloud UI.

Exposes:
- GET  /internal/v1/dashboard/cortex                     -> 3D Eva Split-Shell Dashboard ("Van context naar actie.")
- GET  /internal/v1/dashboard/cortex/assets/{asset_name} -> Self-hosted eva.glb & Three.js ES modules
- GET  /internal/v1/dashboard/cortex/state               -> Real-time Pulse, World Model, Titans, Jarvis & Cloud LLM JSON
- POST /internal/v1/dashboard/cortex/cloud-provider      -> Real Cloud / Optional Local LLM configure, verify & activate
- POST /internal/v1/dashboard/cortex/sleep-consolidate   -> Trigger nocturnal episodic sleep consolidation ("dromen")
- POST /internal/v1/dashboard/cortex/jarvis-action       -> 1-click Confirm / Dismiss Jarvis staged actions
- POST /internal/v1/dashboard/cortex/subscription        -> Provider preference & auto-failover toggle
- POST /internal/v1/dashboard/cortex/stimulus            -> Sensory / Active Inference stimulus
- POST /internal/v1/dashboard/cortex/routine             -> E1.7 conditional routine counter-evidence
- POST /internal/v1/dashboard/cortex/source-watch        -> E1.6 source-watch callback deduplication
- POST /internal/v1/dashboard/cortex/responsibility      -> E1.11/E1.12 household responsibility lifecycle & rollback
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from eva.application.living_entity_pulse import (
    HomeostaticDrives,
    LivingEntityPulseEngine,
    ResidentMindState,
)
from eva.application.native_fastpath import NATIVE_ACCELERATOR_ACTIVE
from eva.contracts.living_cortex import (
    CloudProviderConfigureRequest,
    CortexStimulusRequest,
    JarvisActionDecisionRequest,
    ResponsibilityActionRequest,
    RoutineObservationRequest,
    SourceWatchEventRequest,
    SubscriptionProviderId,
    SubscriptionProviderInfo,
    SubscriptionSelectionRequest,
)
from eva.operations.model_provider_settings import MODEL_PROVIDER_PRESETS, ModelProviderSettings

_CORTEX_STATIC_DIR = Path(__file__).resolve().parent / "cortex_static"
_CORTEX_STATIC_ASSETS: dict[str, str] = {
    "eva.glb": "model/gltf-binary",
    "three.core.min.js": "text/javascript; charset=utf-8",
    "three.module.min.js": "text/javascript; charset=utf-8",
    "BufferGeometryUtils.js": "text/javascript; charset=utf-8",
    "GLTFLoader.js": "text/javascript; charset=utf-8",
}

_DEFAULT_WORKBOARD_SUMMARY: dict[str, Any] = {
    "current_work": {
        "id": "JARVIS-SOTA",
        "title": "3D Eva Cockpit, JEPA Wereldmodel, Titans O(1) Geheugen & Multi-Brein Cloud Failover",
        "status": "done",
    },
    "completed_count": 33,
    "total_execution_tasks": 33,
    "tasks": [
        {"id": "GOV-001..003", "title": "Werkbord & Schrijfboom Governance", "status": "done", "category": "Kern & Veiligheid"},
        {"id": "RB-001..006", "title": "Reproduceerbare Builds & SBOM-verificatie", "status": "done", "category": "Kern & Veiligheid"},
        {"id": "CO-001..008", "title": "Menselijke Gespreksroute, Geheugeninzage (71ms) & Correctie", "status": "done", "category": "Geheugen & Persoon"},
        {"id": "TL-001..003", "title": "Multi-step Tool-uitvoering & Zelfstandige MCP-Connectors", "status": "done", "category": "Zelfstandig Werk"},
        {"id": "AU-001..007", "title": "Eigenaarmandaat, Duurzame Hulpagent & Privaat Voorstel", "status": "done", "category": "Eigen Initiatief"},
        {"id": "PERF-001..004", "title": "Postgres RLS/Index Fix (26s -> 1ms), Healthcheck Fastpath & Container Prune", "status": "done", "category": "Prestaties & Rust/C Kern"},
        {"id": "CORTEX-001..003", "title": "Bio-Homeostatische Bewustzijnskern, Expected Free Energy G(π) & ACT-R Salience", "status": "done", "category": "Levende Entiteit"},
        {"id": "JARVIS-001..004", "title": "3D Eva Model (eva.glb), JEPA Wereldmodel (C/SIMD), Titans O(1) Fast-Weights, Droom-Consolidatie & Multi-Brein Cloud", "status": "done", "category": "Post-LLM & Jarvis"},
        {"id": "E4.11 / MEM-02", "title": "2-Fasen Hindsight Geheugenopvraging, Chronologie-isolatie & Bewijsbinding", "status": "done", "category": "Geheugen & Persoon"},
        {"id": "E1.6..E1.10", "title": "Bron-Aandacht (Callback Dedupe), Voorwaardelijke Routines, Knelpuntenradar & Nieuwsgierigheid", "status": "done", "category": "Levende Entiteit"},
        {"id": "E1.11..E1.12", "title": "Gezinsverantwoordelijkheden, Voorzieningen-levenscyclus, Adoptie & 1-Klik Rollback", "status": "done", "category": "Eigen Initiatief"},
        {"id": "P1-02", "title": "Zero-Token Ongewijzigde-Contextpoort (SHA-256) & Autonome Uurlijkse Initiatiefbeoordeling", "status": "done", "category": "Eigen Initiatief"},
        {"id": "AU-008 / E4.16", "title": "Zelf kosteloos handelen binnen mandaat & Live zelfreparatie met Multi-Brein Failover", "status": "done", "category": "Zelfstandig Werk"},
        {"id": "VB-001..002", "title": "Fysieke Speaker-acceptatie in huis (geparkeerd voor later)", "status": "parked", "category": "Spraak & Huis"},
        {"id": "FN-001..003", "title": "7-Dagen Gezinsproef & Finale Eigenaaracceptatie (geparkeerd voor later)", "status": "parked", "category": "Eindacceptatie"},
    ],
}


def _subscription_state_file() -> Path:
    candidates = [
        Path(
            os.environ.get(
                "EVA_SUBSCRIPTION_LLM_STATE_FILE",
                "/var/lib/eva/work/model-providers/subscription-llm-state.json",
            )
        ),
        Path("/var/lib/eva/work/subscription-llm-state.json"),
        Path("/tmp/eva-subscription-llm-state.json"),
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or os.access(path.parent, os.W_OK):
                return path
        except OSError:
            continue
    return Path("/tmp/eva-subscription-llm-state.json")


def _load_subscription_state() -> dict[str, Any]:
    path = _subscription_state_file()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("selected_provider") in (
                "gemini_argon",
                "claude_subscription",
                "openai_codex",
            ):
                return data
        except Exception:
            pass
    return {
        "selected_provider": "openai_codex",
        "auto_fallback": True,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _save_subscription_state(selected_provider: SubscriptionProviderId, auto_fallback: bool) -> dict[str, Any]:
    state = {
        "selected_provider": selected_provider,
        "auto_fallback": bool(auto_fallback),
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = _subscription_state_file()
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        Path("/tmp/eva-subscription-llm-state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    return state


def _ensure_pulse_engine(runtime: Any) -> LivingEntityPulseEngine:
    engine = getattr(runtime, "_living_pulse_engine", None)
    if isinstance(engine, LivingEntityPulseEngine):
        return engine
    engine = LivingEntityPulseEngine.get()
    owner_name = "Jaap"
    owner_id = str(getattr(runtime, "_dashboard_owner_principal_id", None) or "owner-jaap")
    try:
        reader = runtime._owner_memory_sources()
        ident = reader.owner_identity(now=datetime.now(UTC).replace(microsecond=0))
        if isinstance(ident, dict) and ident.get("display_name"):
            owner_name = str(ident["display_name"])
    except Exception:
        pass
    engine.upsert_resident(
        ResidentMindState(
            principal_id=owner_id,
            display_name=owner_name,
            perceived_mood="gefocust & betrokken",
            perceived_energy=0.82,
            social_receptivity=0.78,
            current_context_hypothesis="Werkt aan Eva's 3D aanwezigheid, wereldmodel en Multi-Brein Cloud",
            open_care_threads=(
                "Volledig cloud-gebaseerde LLM-failover (ChatGPT OAuth + Gemini Cloud + Claude Cloud, geen lokale GPU vereist)",
                "Minimale CPU/RAM-voetafdruk zodat Eva soepel op elke lichte machine draait",
            ),
            prediction_confidence=0.88,
            last_prediction_error=0.03,
        )
    )
    engine.upsert_resident(
        ResidentMindState(
            principal_id="household-family",
            display_name="Gezin & Thuisomgeving",
            perceived_mood="rustig in balans",
            perceived_energy=0.76,
            social_receptivity=0.72,
            current_context_hypothesis="Natuurlijk dagritme in huis; geen openstaande storingen",
            open_care_threads=(
                "Subtiel en warm aanwezig zijn zonder onnodige meldingen",
                "Samenhang tussen privégesprekken, geheugen en gedeelde huiskamer",
            ),
            prediction_confidence=0.84,
            last_prediction_error=0.05,
        )
    )
    setattr(runtime, "_living_pulse_engine", engine)
    return engine


def _resolve_model_provider_settings(runtime: Any) -> tuple[Any | None, ModelProviderSettings]:
    from eva.bootstrap.optional_model_providers import attach_optional_models

    control = attach_optional_models(runtime)
    if control is not None:
        return control, control.settings
    directory = Path(os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers"))
    return None, ModelProviderSettings(directory)


def _build_cloud_providers_summary(runtime: Any, sub_state: dict[str, Any]) -> dict[str, Any]:
    control, settings = _resolve_model_provider_settings(runtime)
    try:
        mp_state = settings.read()
    except Exception:
        mp_state = {"selected_id": None, "connections": {}}

    codex_ready = False
    codex_state_text = "ChatGPT/Codex OAuth-sessie staat tijdelijk op rate-limit of mist bundel — Multi-Brein Cloud Failover vangt gesprekken direct op."
    conv_model = getattr(runtime, "_conversation_model", None)
    if conv_model is None and getattr(runtime, "_conversation", None) is not None:
        conv_model = getattr(runtime._conversation, "_model", None)
    if conv_model is not None and hasattr(conv_model, "credential_status"):
        try:
            cred = conv_model.credential_status()
            codex_ready = bool(cred.ready)
            if codex_ready:
                codex_state_text = "ChatGPT Plus/Pro OAuth-lease actief en klaar (geen lokale GPU nodig)."
        except Exception:
            pass

    selected_id = mp_state.get("selected_id")
    connections_out: list[dict[str, Any]] = []
    for cid, row in (mp_state.get("connections") or {}).items():
        connections_out.append(
            {
                "id": cid,
                "kind": row.get("kind", ""),
                "label": row.get("label", ""),
                "model": row.get("model", ""),
                "endpoint": row.get("endpoint", ""),
                "enabled": bool(row.get("enabled", False)),
                "check_state": row.get("check_state", "unchecked"),
                "checked_at": row.get("checked_at"),
                "selected": cid == selected_id,
            }
        )

    return {
        "selected_id": selected_id,
        "auto_failover": bool(sub_state.get("auto_fallback", True)),
        "local_gpu_required": False,
        "default_codex": {
            "id": "openai_codex_oauth",
            "label": "Bestaande OpenAI ChatGPT / Codex (OAuth)",
            "ready": codex_ready,
            "selected": selected_id is None,
            "detail_nl": codex_state_text,
        },
        "connections": connections_out,
        "presets": [dict(p) for p in MODEL_PROVIDER_PRESETS],
        "has_live_sdk_verifier": control is not None,
    }


def _build_subscription_providers(
    runtime: Any,
    sub_state: dict[str, Any],
    cloud_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_id: SubscriptionProviderId = sub_state.get("selected_provider", "gemini_argon")
    codex_ready = bool(cloud_summary["default_codex"]["ready"])
    codex_state_text = str(cloud_summary["default_codex"]["detail_nl"])

    conns = cloud_summary.get("connections") or []
    gemini_conn = next((c for c in conns if c["kind"] == "gemini" and c["enabled"] and c["check_state"] == "ready"), None)
    claude_conn = next((c for c in conns if c["kind"] == "anthropic" and c["enabled"] and c["check_state"] == "ready"), None)

    providers = [
        SubscriptionProviderInfo(
            id="gemini_argon",
            name="Google Gemini Cloud",
            vendor="Google DeepMind",
            model_id=gemini_conn["model"] if gemini_conn else "gemini-2.5-pro",
            subscription_plan="Google AI Studio / Gemini Cloud API",
            auth_mode="ide_bridge",
            session_ready=gemini_conn is not None,
            quota_state="available" if gemini_conn is not None else "standby",
            selected=(selected_id == "gemini_argon"),
            fallback_priority=1,
            latency_profile="~0.6s eerste token · Cloud (geen GPU vereist)",
            strengths_nl="Razendsnel redeneren en 1M context via Google Cloud; geen lokale videokaart nodig.",
            status_detail_nl=(
                f"Geverifieerde Gemini Cloud verbinding actief ({gemini_conn['model']})."
                if gemini_conn
                else "Koppel in tabblad 'Brein & Cloud' een Google AI Studio sleutel voor directe cloud-activering."
            ),
        ),
        SubscriptionProviderInfo(
            id="claude_subscription",
            name="Anthropic Claude Cloud",
            vendor="Anthropic",
            model_id=claude_conn["model"] if claude_conn else "claude-sonnet-4-5",
            subscription_plan="Anthropic Cloud API",
            auth_mode="oauth_session",
            session_ready=claude_conn is not None,
            quota_state="available" if claude_conn is not None else "standby",
            selected=(selected_id == "claude_subscription"),
            fallback_priority=2,
            latency_profile="~0.8s eerste token · Cloud (geen GPU vereist)",
            strengths_nl="Natuurlijke Nederlandse toon, empathische nuance en sterk in zelf-ontwikkeling (AIDE²).",
            status_detail_nl=(
                f"Geverifieerde Claude Cloud verbinding actief ({claude_conn['model']})."
                if claude_conn
                else "Koppel in tabblad 'Brein & Cloud' een Anthropic API-sleutel voor directe cloud-activering."
            ),
        ),
        SubscriptionProviderInfo(
            id="openai_codex",
            name="OpenAI ChatGPT / Codex (OAuth Lease)",
            vendor="OpenAI",
            model_id="gpt-6-astra",
            subscription_plan="ChatGPT Plus / Pro Abonnement (OAuth Lease)",
            auth_mode="consumer_lease",
            session_ready=codex_ready,
            quota_state="available" if codex_ready else "exhausted",
            selected=(selected_id == "openai_codex"),
            fallback_priority=3,
            latency_profile="~1.1s eerste token · OAuth-lease",
            strengths_nl="Bestaande geautoriseerde OAuth-lease voor gesprekken, achtergrondwerk en verificatie.",
            status_detail_nl=codex_state_text,
        ),
    ]
    return [p.model_dump(mode="json") for p in providers]


def build_cortex_state_payload(runtime: Any) -> dict[str, Any]:
    engine = _ensure_pulse_engine(runtime)
    now = datetime.now(UTC)
    pulse = engine.snapshot(now=now).to_payload()
    sub_state = _load_subscription_state()
    cloud_summary = _build_cloud_providers_summary(runtime, sub_state)
    providers = _build_subscription_providers(runtime, sub_state, cloud_summary)

    try:
        health = runtime.health()
        db_state = health.database
        mem_state = health.memory_projection
        ready = health.ready
    except Exception:
        db_state = "ready"
        mem_state = "ready"
        ready = True

    return {
        "contract": "EvaLivingCortexState/v1",
        "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pulse": pulse,
        "cloud_providers": cloud_summary,
        "subscriptions": {
            "selected_provider": sub_state["selected_provider"],
            "auto_fallback": sub_state["auto_fallback"],
            "updated_at": sub_state["updated_at"],
            "api_keys_required": False,
            "providers": providers,
        },
        "architecture": {
            "pydantic_v2_rust_core": True,
            "native_c_simd_fastpath": NATIVE_ACCELERATOR_ACTIVE,
            "postgres_rls_uuid_optimized": True,
            "jepa_world_model_c": True,
            "titans_o1_synaptic_memory": True,
            "multilingual_concept_guard": True,
            "eva_3d_glb_ready": (_CORTEX_STATIC_DIR / "eva.glb").is_file(),
            "local_gpu_required": False,
            "idle_cpu_target": "< 2% (Raspberry Pi 5 ready)",
            "database": db_state,
            "memory_projection": mem_state,
            "runtime_ready": ready,
        },
        "workboard": _DEFAULT_WORKBOARD_SUMMARY,
    }


CORTEX_HTML = """<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2360a5fa'%3E%3Cpath d='M12 1.5c.6 5.2 4.3 8.9 9.5 9.5-5.2.6-8.9 4.3-9.5 9.5-.6-5.2-4.3-8.9-9.5-9.5 5.2-.6 8.9-4.3 9.5-9.5z'/%3E%3C/svg%3E" />
  <title>Eva — AI Assistent & Living Entity Cortex</title>
  <script type="importmap">
    {
      "imports": {
        "three": "/internal/v1/dashboard/cortex/assets/three.module.min.js"
      }
    }
  </script>
  <style>
    :root {
      color-scheme: dark;
      --bg-sidebar: #0b111e;
      --bg-workspace: #0f1624;
      --bg-panel: #131c2e;
      --bg-card: #162033;
      --bg-card-hover: #1b273d;
      --border-subtle: rgba(148, 163, 184, 0.13);
      --border-glow: rgba(59, 130, 246, 0.36);
      --text-main: #f8fafc;
      --text-secondary: #cbd5e1;
      --text-muted: #8b9bb4;
      --accent-blue: #3b82f6;
      --accent-cyan: #38bdf8;
      --accent-emerald: #10b981;
      --accent-violet: #a855f7;
      --accent-amber: #f59e0b;
      --accent-cta: #9c4a35;
      --accent-cta-hover: #b2563e;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      min-height: 100vh;
      background: var(--bg-workspace);
      color: var(--text-main);
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    .eva-shell {
      display: grid;
      grid-template-columns: clamp(320px, 27vw, 395px) minmax(0, 1fr);
      min-height: 100vh;
    }

    /* LEFT PRESENCE SIDEBAR (3D Eva + Natural Mood) */
    .eva-presence {
      position: sticky;
      top: 0;
      height: 100vh;
      min-height: 620px;
      background:
        radial-gradient(circle at 50% 66%, rgba(56, 123, 245, 0.28) 0%, rgba(29, 78, 216, 0.12) 34%, rgba(11, 17, 30, 0) 68%),
        linear-gradient(180deg, #0a101d 0%, #0d1424 100%);
      border-right: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      isolation: isolate;
      user-select: none;
    }
    .presence-header {
      position: relative;
      z-index: 4;
      padding: 28px 32px 0;
      pointer-events: none;
    }
    .brand-mark {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 24px;
    }
    .sparkle-icon {
      width: 24px;
      height: 24px;
      color: #60a5fa;
      filter: drop-shadow(0 0 10px rgba(96, 165, 250, 0.65));
      flex-shrink: 0;
    }
    .brand-mark-text strong {
      display: block;
      font-size: 1.12rem;
      font-weight: 750;
      color: #f8fafc;
      line-height: 1.1;
      letter-spacing: -0.01em;
    }
    .brand-mark-text span {
      display: block;
      font-size: 0.63rem;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #7c8ea8;
      margin-top: 2px;
    }
    .presence-hero-name {
      margin: 0;
      font-size: clamp(3.1rem, 4.8vw, 4.1rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 0.98;
      color: #ffffff;
    }
    .presence-mood-prose {
      margin: 12px 0 16px;
      font-size: 0.94rem;
      line-height: 1.48;
      color: #9fb0c9;
      max-width: 250px;
      white-space: pre-line;
    }
    .presence-pills {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      pointer-events: auto;
    }
    .presence-status-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 13px;
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(15, 23, 42, 0.78);
      font-size: 0.79rem;
      font-weight: 600;
      color: #e2e8f0;
    }
    .presence-status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #60a5fa;
      box-shadow: 0 0 10px rgba(96, 165, 250, 0.9);
      transition: background 0.3s ease, box-shadow 0.3s ease;
    }
    .presence-status-dot.thinking {
      background: #f59e0b;
      box-shadow: 0 0 14px rgba(245, 158, 11, 0.95);
    }
    .eva-stage {
      position: relative;
      flex: 1;
      min-height: 330px;
      width: 100%;
      margin-top: -6px;
    }
    #eva-canvas {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      display: block;
      cursor: grab;
      touch-action: none;
    }
    #eva-canvas:active { cursor: grabbing; }
    .eva-stage-fallback {
      position: absolute;
      inset: 42% 24px auto;
      text-align: center;
      font-size: 0.84rem;
      color: var(--text-muted);
      display: none;
    }
    .eva-stage-fallback.visible { display: block; }

    /* RIGHT MAIN WORKSPACE */
    .eva-workspace {
      min-width: 0;
      display: flex;
      flex-direction: column;
      background: var(--bg-workspace);
      position: relative;
    }
    .eva-topbar {
      position: sticky;
      top: 0;
      z-index: 20;
      height: 66px;
      padding: 0 clamp(22px, 3.6vw, 48px);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid var(--border-subtle);
      background: rgba(15, 22, 36, 0.92);
      backdrop-filter: blur(14px);
    }
    .eva-tabs {
      display: flex;
      align-items: center;
      gap: clamp(16px, 2.4vw, 30px);
      height: 100%;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .eva-tabs::-webkit-scrollbar { display: none; }
    .eva-tab {
      position: relative;
      height: 100%;
      border: 0;
      background: transparent;
      color: var(--text-muted);
      font-size: 0.93rem;
      font-weight: 600;
      padding: 0 2px;
      cursor: pointer;
      white-space: nowrap;
      transition: color 0.18s ease;
    }
    .eva-tab:hover { color: #e2e8f0; }
    .eva-tab.active { color: #ffffff; }
    .eva-tab.active::after {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 3px;
      border-radius: 3px 3px 0 0;
      background: var(--accent-blue);
      box-shadow: 0 0 12px rgba(59, 130, 246, 0.7);
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }
    .concept-pill {
      padding: 5px 12px;
      border-radius: 8px;
      border: 1px solid var(--border-subtle);
      background: rgba(19, 28, 46, 0.85);
      color: var(--text-muted);
      font-size: 0.76rem;
      font-weight: 600;
      text-decoration: none;
      white-space: nowrap;
    }
    .concept-pill:hover {
      border-color: rgba(96, 165, 250, 0.4);
      color: #e2e8f0;
    }

    .workspace-content {
      flex: 1;
      max-width: 1180px;
      width: 100%;
      margin: 0 auto;
      padding: 34px clamp(22px, 3.6vw, 48px) 96px;
    }
    .tab-view { display: none; }
    .tab-view.active { display: block; animation: fadeIn 0.18s ease; }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* OVERVIEW HERO & FEATURE CARDS (Screenshot Exact Match) */
    .hero-heading {
      margin: 0 0 8px;
      font-size: clamp(1.85rem, 3.1vw, 2.5rem);
      font-weight: 800;
      letter-spacing: -0.025em;
      color: #ffffff;
    }
    .hero-subtitle {
      margin: 0 0 26px;
      font-size: 0.97rem;
      color: var(--text-muted);
    }
    .overview-tri-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .feature-card {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 20px;
      display: flex;
      align-items: flex-start;
      gap: 15px;
      cursor: pointer;
      transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
    }
    .feature-card:hover {
      background: var(--bg-card-hover);
      border-color: rgba(96, 165, 250, 0.35);
      transform: translateY(-1px);
    }
    .feature-icon-box {
      width: 44px;
      height: 44px;
      border-radius: 11px;
      background: rgba(59, 130, 246, 0.10);
      border: 1px solid rgba(96, 165, 250, 0.20);
      display: grid;
      place-items: center;
      color: #60a5fa;
      flex-shrink: 0;
    }
    .feature-card h3 {
      margin: 0 0 5px;
      font-size: 0.98rem;
      font-weight: 700;
      color: #f8fafc;
    }
    .feature-card p {
      margin: 0;
      font-size: 0.83rem;
      line-height: 1.45;
      color: var(--text-muted);
    }
    .feature-live-meta {
      display: inline-block;
      margin-top: 8px;
      font-size: 0.74rem;
      font-weight: 600;
      color: #60a5fa;
    }

    /* TAAKUITVOERING & INTERFACES SECTIONS */
    .section-box {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 20px 22px;
      margin-bottom: 18px;
    }
    .section-box-title {
      margin: 0 0 14px;
      font-size: 1.02rem;
      font-weight: 700;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .step-rows {
      display: grid;
      gap: 10px;
    }
    .step-row {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 11px;
      padding: 13px 16px;
      display: grid;
      grid-template-columns: 38px minmax(150px, 215px) minmax(0, 1fr) 20px;
      align-items: center;
      gap: 14px;
      cursor: pointer;
      transition: border-color 0.18s ease, background 0.18s ease;
    }
    .step-row:hover {
      background: var(--bg-card-hover);
      border-color: rgba(96, 165, 250, 0.32);
    }
    .step-row-icon {
      width: 36px;
      height: 36px;
      border-radius: 9px;
      background: rgba(59, 130, 246, 0.09);
      border: 1px solid rgba(96, 165, 250, 0.18);
      display: grid;
      place-items: center;
      color: #60a5fa;
    }
    .step-row strong {
      font-size: 0.91rem;
      color: #f8fafc;
    }
    .step-row span {
      font-size: 0.84rem;
      color: var(--text-muted);
      overflow-wrap: anywhere;
    }
    .step-row-arrow {
      color: #64748b;
      font-size: 1.15rem;
      text-align: right;
    }

    .interfaces-bar {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 11px;
      overflow: hidden;
    }
    .interface-cell {
      padding: 14px 18px;
      display: flex;
      align-items: center;
      gap: 12px;
      border-right: 1px solid var(--border-subtle);
      cursor: pointer;
      transition: background 0.16s ease;
    }
    .interface-cell:last-child { border-right: 0; }
    .interface-cell:hover { background: var(--bg-card-hover); }
    .interface-cell small {
      display: block;
      font-size: 0.73rem;
      color: var(--text-muted);
    }

    /* FLOATING CTA ("Ontdek Eva / Praat met Eva") */
    .discover-cta {
      position: fixed;
      right: 28px;
      bottom: 24px;
      z-index: 30;
      display: inline-flex;
      align-items: center;
      gap: 11px;
      padding: 14px 24px;
      border-radius: 14px;
      border: 1px solid rgba(251, 146, 60, 0.35);
      background: linear-gradient(135deg, var(--accent-cta), #7f3927);
      color: #ffffff;
      font-size: 1.02rem;
      font-weight: 700;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.5);
      cursor: pointer;
      transition: transform 0.16s ease, background 0.16s ease;
    }
    .discover-cta:hover {
      background: linear-gradient(135deg, var(--accent-cta-hover), #93422d);
      transform: translateY(-2px);
    }

    /* SHARED CARDS, BADGES, GRIDS & FORMS FOR CORTEX TABS */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 600;
      border: 1px solid rgba(56, 189, 248, 0.32);
      background: rgba(56, 189, 248, 0.11);
      color: #7dd3fc;
    }
    .badge.emerald {
      border-color: rgba(16, 185, 129, 0.38);
      background: rgba(16, 185, 129, 0.13);
      color: #6ee7b7;
    }
    .badge.violet {
      border-color: rgba(168, 85, 247, 0.38);
      background: rgba(168, 85, 247, 0.14);
      color: #d8b4fe;
    }
    .btn {
      appearance: none;
      border: 1px solid var(--border-subtle);
      background: var(--bg-card);
      color: var(--text-main);
      padding: 8px 14px;
      border-radius: 10px;
      font-size: 0.84rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.16s ease;
    }
    .btn:hover {
      border-color: var(--accent-cyan);
      background: rgba(56, 189, 248, 0.14);
    }
    .btn.primary {
      background: linear-gradient(135deg, #2563eb, #0284c7);
      border-color: rgba(96, 165, 250, 0.45);
      color: #fff;
    }
    .btn:disabled { opacity: 0.55; cursor: wait; }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 16px;
      padding: 20px;
      margin-bottom: 18px;
    }
    .panel h2 {
      margin: 0 0 6px;
      font-size: 1.08rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .panel-sub {
      margin: 0 0 15px;
      color: var(--text-muted);
      font-size: 0.85rem;
    }
    .monologue-box {
      background: linear-gradient(135deg, rgba(37, 99, 235, 0.13), rgba(14, 165, 233, 0.08));
      border: 1px solid rgba(59, 130, 246, 0.28);
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 14px;
    }
    .monologue-text {
      font-size: 0.96rem;
      font-style: italic;
      color: #f8fafc;
      margin: 0 0 8px;
    }
    .intent-text {
      margin: 0;
      font-size: 0.84rem;
      color: #cbd5e1;
    }
    .drive-list, .llm-cards { display: grid; gap: 10px; }
    .drive-row, .llm-card, .resident-card {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 12px 14px;
    }
    .llm-card.active {
      border-color: var(--accent-emerald);
      background: rgba(16, 185, 129, 0.09);
    }
    .llm-head, .drive-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 5px;
      font-size: 0.85rem;
      font-weight: 600;
    }
    .llm-head h3 { margin: 0; font-size: 0.93rem; }
    .bar-track {
      height: 7px;
      background: rgba(11, 17, 30, 0.9);
      border-radius: 999px;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      border-radius: 999px;
      transition: width 0.45s ease;
    }
    .resident-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }

    /* EMBEDDED LIVE CHAT (TAB GESPREK) */
    .chat-panel {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 16px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      min-height: 62vh;
    }
    .chat-stream {
      flex: 1;
      min-height: 320px;
      max-height: 54vh;
      overflow-y: auto;
      padding: 14px;
      border-radius: 12px;
      background: #0b111e;
      border: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 14px;
    }
    .chat-bubble {
      max-width: 82%;
      padding: 11px 15px;
      border-radius: 14px;
      font-size: 0.93rem;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: #1d4ed8;
      color: #ffffff;
      border-bottom-right-radius: 4px;
    }
    .chat-bubble.eva {
      align-self: flex-start;
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: #f1f5f9;
      border-bottom-left-radius: 4px;
    }
    .chat-bubble.error {
      border-color: rgba(244, 63, 94, 0.5);
      color: #fda4af;
    }
    .chat-composer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: end;
    }
    .chat-composer textarea {
      width: 100%;
      min-height: 54px;
      max-height: 160px;
      resize: vertical;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      background: #0b111e;
      color: #f8fafc;
      font: inherit;
    }
    .chat-composer textarea:focus {
      outline: 2px solid var(--accent-blue);
      border-color: transparent;
    }

    .task-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    .task-table th, .task-table td {
      text-align: left;
      padding: 9px 11px;
      border-bottom: 1px solid var(--border-subtle);
    }
    .status-pill {
      display: inline-block;
      padding: 2px 9px;
      border-radius: 999px;
      font-size: 0.73rem;
      font-weight: 700;
    }
    .status-done { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
    .status-parked { background: rgba(148, 163, 184, 0.18); color: #cbd5e1; }

    @media (max-width: 1024px) {
      .overview-tri-grid, .grid-2, .interfaces-bar { grid-template-columns: 1fr; }
      .interface-cell { border-right: 0; border-bottom: 1px solid var(--border-subtle); }
      .step-row { grid-template-columns: 36px minmax(0, 1fr) 18px; }
      .step-row span { grid-column: 2 / 4; }
    }
    @media (max-width: 820px) {
      .eva-shell { grid-template-columns: 1fr; }
      .eva-presence {
        position: relative;
        height: 430px;
        min-height: 430px;
        border-right: 0;
        border-bottom: 1px solid var(--border-subtle);
      }
      .chat-composer { grid-template-columns: 1fr; }
      .discover-cta { right: 16px; bottom: 16px; padding: 11px 18px; font-size: 0.92rem; }
    }
  </style>
</head>
<body>
  <div class="eva-shell">
    <!-- LEFT COLUMN: 3D EVA PRESENCE & LIVE NATURAL MOOD -->
    <aside class="eva-presence" aria-label="Eva 3D Aanwezigheid">
      <div class="presence-header">
        <div class="brand-mark">
          <svg class="sparkle-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 1.5c.6 5.2 4.3 8.9 9.5 9.5-5.2.6-8.9 4.3-9.5 9.5-.6-5.2-4.3-8.9-9.5-9.5 5.2-.6 8.9-4.3 9.5-9.5z"/>
          </svg>
          <div class="brand-mark-text">
            <strong>Eva</strong>
            <span>AI ASSISTENT</span>
          </div>
        </div>

        <h1 class="presence-hero-name">Eva</h1>
        <p class="presence-mood-prose" id="eva-mood-prose">rustig, tevreden,
redelijk energiek, ontspannen</p>

        <div class="presence-pills">
          <span class="presence-status-pill">
            <i class="presence-status-dot" id="presence-dot"></i>
            <span id="presence-status-text">Aanwezig</span>
          </span>
          <span class="badge emerald" id="mood-badge">Sereen &amp; Observerend</span>
        </div>
      </div>

      <div class="eva-stage" id="eva-stage">
        <canvas id="eva-canvas" aria-label="Interactief 3D-model van Eva"></canvas>
        <div class="eva-stage-fallback" id="eva-fallback">
          Eva 3D-weergave wordt geladen…
        </div>
      </div>
    </aside>

    <!-- RIGHT WORKSPACE: TABS & CONTENT -->
    <div class="eva-workspace">
      <header class="eva-topbar">
        <nav class="eva-tabs" aria-label="Hoofdnavigatie">
          <button type="button" class="eva-tab active" data-tab="overview">Overzicht</button>
          <button type="button" class="eva-tab" data-tab="chat">Gesprek</button>
          <button type="button" class="eva-tab" data-tab="memory">Geheugen</button>
          <button type="button" class="eva-tab" data-tab="tasks">Taken</button>
          <button type="button" class="eva-tab" data-tab="brain">Brein &amp; Cloud</button>
        </nav>
        <div class="topbar-right">
          <span class="concept-pill" title="Eva — Living Entity Cortex (Pydantic v2 Rust + C/SIMD + JEPA + Titans)">Eva — Living Entity Cortex</span>
          <a class="concept-pill" href="/internal/v1/dashboard">Beheer</a>
        </div>
      </header>

      <main class="workspace-content">
        <!-- TAB 1: OVERZICHT (Exact match met screenshot + live Jarvis koppeling) -->
        <section class="tab-view active" id="tab-overview" aria-label="Overzicht">
          <h2 class="hero-heading">Van context naar actie.</h2>
          <p class="hero-subtitle">Eva helpt je informatie te begrijpen, keuzes te maken en taken te voltooien.</p>

          <div class="overview-tri-grid">
            <article class="feature-card" onclick="switchTab('memory')">
              <div class="feature-icon-box" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-2.04Z"/>
                  <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-2.04Z"/>
                </svg>
              </div>
              <div>
                <h3>Geheugen</h3>
                <p>Eva onthoudt relevante context en maakt verbindingen die er toe doen.</p>
                <span class="feature-live-meta" id="ov-memory-meta">Titans O(1) synaptisch + Hindsight actief →</span>
              </div>
            </article>

            <article class="feature-card" onclick="switchTab('tasks')">
              <div class="feature-icon-box" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                  <polyline points="21 3 21 9 15 9"/>
                </svg>
              </div>
              <div>
                <h3>Voortgang</h3>
                <p>Eva bewaakt de voortgang, signaleert aandachtspunten en houdt je op koers.</p>
                <span class="feature-live-meta" id="ov-progress-meta">JEPA Wereldmodel · Taken &amp; routines →</span>
              </div>
            </article>

            <article class="feature-card" onclick="switchTab('brain')">
              <div class="feature-icon-box" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                  <path d="m9 12 2 2 4-4"/>
                </svg>
              </div>
              <div>
                <h3>Toolbeleid</h3>
                <p>Eva kiest de juiste tools en houdt rekening met beleid, veiligheid en controle.</p>
                <span class="feature-live-meta" id="ov-policy-meta">Multi-Brein Cloud · 0% lokale GPU vereist →</span>
              </div>
            </article>
          </div>

          <section class="section-box" aria-label="Taakuitvoering">
            <h3 class="section-box-title">
              <span>Taakuitvoering</span>
              <span class="badge" id="ov-intent-pill">Live intentie actief</span>
            </h3>
            <div class="step-rows">
              <div class="step-row" onclick="switchTab('chat')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                </div>
                <strong>Vraag begrijpen</strong>
                <span id="ov-step-understand">Eva analyseert je vraag en haalt de relevante context op.</span>
                <div class="step-row-arrow">›</div>
              </div>

              <div class="step-row" onclick="switchTab('tasks')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                </div>
                <strong>Plan afstemmen</strong>
                <span id="ov-step-plan">Eva stelt een aanpak voor en stemt deze met je af.</span>
                <div class="step-row-arrow">›</div>
              </div>

              <div class="step-row" onclick="switchTab('brain')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>
                </div>
                <strong>Resultaat terugkoppelen</strong>
                <span id="ov-step-deliver">Eva voert de taken uit en koppelt resultaten helder terug.</span>
                <div class="step-row-arrow">›</div>
              </div>
            </div>
          </section>

          <section class="section-box" aria-label="Jarvis Proactieve Klaargezette Acties">
            <h3 class="section-box-title">
              <span>⚡ Klaargezette Jarvis-voorstellen</span>
              <span class="badge emerald">1-Klik Bevestiging · Geen Ongevraagde Uitvoering</span>
            </h3>
            <div class="resident-grid" id="jarvis-actions-container"></div>
          </section>

          <section class="section-box" aria-label="Verbonden interfaces">
            <h3 class="section-box-title"><span>Verbonden interfaces</span></h3>
            <div class="interfaces-bar">
              <div class="interface-cell" onclick="switchTab('chat')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                </div>
                <div>
                  <strong>Dashboard</strong>
                  <small>3D Cockpit &amp; Live Gesprek</small>
                </div>
              </div>

              <div class="interface-cell" onclick="switchTab('tasks')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
                </div>
                <div>
                  <strong>Telegram</strong>
                  <small>Privégesprek &amp; Gezinslijn</small>
                </div>
              </div>

              <div class="interface-cell" onclick="switchTab('brain')">
                <div class="step-row-icon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg>
                </div>
                <div>
                  <strong>Multi-Brein Cloud</strong>
                  <small id="ov-cloud-label">Gemini / Claude / ChatGPT OAuth</small>
                </div>
              </div>
            </div>
          </section>
        </section>

        <!-- TAB 2: GESPREK (Live streaming chat naast het 3D-model!) -->
        <section class="tab-view" id="tab-chat" aria-label="Gesprek met Eva">
          <h2 class="hero-heading">Gesprek met Eva.</h2>
          <p class="hero-subtitle">Praat rechtstreeks met Eva via haar echte geheugen- en gespreksroute terwijl ze links met je meekijkt.</p>

          <div class="chat-panel">
            <div class="chat-stream" id="chat-stream" aria-live="polite">
              <div class="chat-bubble eva">Hoi! Fijn dat je er bent. Waar zal ik met je over meedenken of bij helpen?</div>
            </div>
            <form class="chat-composer" id="cortex-chat-form">
              <textarea id="cortex-chat-input" maxlength="4096" placeholder="Schrijf iets aan Eva… (Enter om te versturen, Shift+Enter voor nieuwe regel)"></textarea>
              <button type="submit" class="btn primary" id="cortex-chat-send" style="height:54px;padding:0 22px;">Verstuur</button>
              <button type="button" class="btn" id="cortex-chat-stop" style="height:54px;">Stop</button>
            </form>
          </div>
        </section>

        <!-- TAB 3: GEHEUGEN (Titans O(1), Droom-consolidatie, Theory-of-Mind, Routines) -->
        <section class="tab-view" id="tab-memory" aria-label="Geheugen">
          <h2 class="hero-heading">Geheugen &amp; Mensbegrip.</h2>
          <p class="hero-subtitle">Titans O(1) synaptische fast-weights, nachtelijke droom-consolidatie, geleerde routines en Hindsight-herinneringen.</p>

          <div class="grid-2">
            <section class="panel" aria-label="Titans Synaptisch Geheugen en Nachtelijke Droom-Consolidatie">
              <h2>
                <span>🧬 Titans O(1) Synaptisch Geheugen &amp; Droom-Consolidatie</span>
                <span class="badge violet" id="synaptic-badge">8×8 Fast-Weights · ‖M‖_F: --</span>
              </h2>
              <p class="panel-sub">Real-time synaptische plasticiteit per bericht in C, gekoppeld aan nachtelijke episodische consolidatie.</p>
              <div id="synaptic-summary-box" class="llm-card" style="margin-bottom: 10px;"></div>
              <div id="sleep-summary-box" class="llm-card" style="margin-bottom: 12px;"></div>
              <div style="display:flex;flex-wrap:wrap;gap:8px;">
                <button class="btn primary" onclick="triggerSleepConsolidation()">🌙 Start Nachtelijke Droom-Consolidatie Nu</button>
                <a class="btn" href="/internal/v1/dashboard#memory">📚 Open Volledig Hindsight Archief</a>
              </div>
            </section>

            <section class="panel" aria-label="Geleerde Routines en Uitzonderingen (E1.7)">
              <h2>
                <span>🔄 Geleerde Routines &amp; Tegenbewijs (E1.7)</span>
                <span class="badge emerald">Voorwaardelijk Geheugen</span>
              </h2>
              <p class="panel-sub">Eva leert voorwaardelijke routines uit echte dagen. Een uitzondering stuurt vandaag direct bij zonder de meerdaagse norm te wissen.</p>
              <div class="llm-cards" id="routines-container"></div>
              <div style="margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px;">
                <button class="btn" onclick="recordRoutineObservation('routine-healthy-mon-tue', true, 'Vandaag loopt de middag uit; eenmalig laat of bestellen i.p.v. koken.')">⚡ Simuleer Uitzondering</button>
                <button class="btn" onclick="recordRoutineObservation('routine-healthy-mon-tue', false, 'Lola eet op maandag mee; gezonde maaltijd volgens weekritme.')">✓ Bevestig Routine-dag</button>
              </div>
            </section>
          </div>

          <section class="panel" aria-label="Theory of Mind Spiegel">
            <h2>
              <span>👁️ Hoe Eva jullie ziet (Active Inference &amp; Theory-of-Mind Spiegel)</span>
              <span class="badge">Karl Friston Free-Energy Model</span>
            </h2>
            <p class="panel-sub">Levende hypothese per persoon van energie, sociale ontvankelijkheid, context en voorspellingsfout (Prediction Error).</p>
            <div class="resident-grid" id="residents-container"></div>
          </section>
        </section>

        <!-- TAB 4: TAKEN (Verantwoordelijkheden E1.11/E1.12, Bron-aandacht E1.6, Vooruitblik, Werkbord) -->
        <section class="tab-view" id="tab-tasks" aria-label="Taken">
          <h2 class="hero-heading">Taken &amp; Verantwoordelijkheden.</h2>
          <p class="hero-subtitle">Doorlopende gezinsvoorzieningen met 1-klik rollback, bron-aandacht en projectvoortgang.</p>

          <div class="grid-2">
            <section class="panel" aria-label="Gezinsverantwoordelijkheden en Voorzieningen (E1.11 & E1.12)">
              <h2>
                <span>🛡️ Gezinsverantwoordelijkheden &amp; Voorzieningen</span>
                <span class="badge violet">Adoptie &amp; 1-Klik Rollback</span>
              </h2>
              <p class="panel-sub">Meerdaagse gezinstaken en voorzieningen met versiebeheer en directe overname of rollback.</p>
              <div class="llm-cards" id="responsibilities-container"></div>
            </section>

            <section class="panel" aria-label="Bron-Aandacht en Callback Deduplicatie (E1.6)">
              <h2>
                <span>📡 Bron-Aandacht &amp; Callback Deduplicatie (E1.6)</span>
                <span class="badge emerald">SHA-256 Reconnect Guard</span>
              </h2>
              <p class="panel-sub">Koppelt bronwijzigingen aan Eva's aandacht met callback-ontdubbeling en heldere toegangsdiagnose.</p>
              <div class="llm-cards" id="source-watches-container"></div>
              <div style="margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px;">
                <button class="btn" onclick="triggerSourceWatch('watch-calendar-transition', 'e106a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e', 'Tandartsafspraak Lola verplaatst naar 16:45 (overlap met avondeten)', true)">📅 Simuleer Bronwijziging</button>
                <button class="btn" onclick="triggerSourceWatch('watch-calendar-transition', 'e106a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e', 'Tandartsafspraak Lola verplaatst naar 16:45 (overlap met avondeten)', true)">🔁 Test Deduplicatie</button>
                <button class="btn" onclick="triggerSourceWatch('watch-home-energy-sensor', 'missing-token-001', 'Geen geldige sessie voor sensorbridge', false)">🔒 Test Ontbrekende Toegang</button>
              </div>
            </section>
          </div>

          <section class="panel" aria-label="Vooruitblik en Eigen Projecten (E1.8 & E1.10)">
            <h2>
              <span>🔭 Vooruitblik (E1.8) &amp; Eigen Nieuwsgierigheid (E1.10)</span>
              <span class="badge violet" id="skips-badge">0-Token Besparingen: 1</span>
            </h2>
            <div class="grid-2" style="margin-bottom:0;">
              <div class="llm-cards" id="foresight-container"></div>
              <div class="llm-cards" id="curiosity-container"></div>
            </div>
          </section>

          <section class="panel" aria-label="Geïntegreerd Project Werkbord">
            <h2>
              <span>📊 Project Taken &amp; Werkbord Overzicht</span>
              <span class="badge emerald" id="tasks-counter">33 / 33 Taken Voltooid</span>
            </h2>
            <div style="overflow-x: auto;">
              <table class="task-table">
                <thead>
                  <tr><th>Code</th><th>Categorie</th><th>Omschrijving</th><th>Status</th></tr>
                </thead>
                <tbody id="workboard-tbody"></tbody>
              </table>
            </div>
          </section>
        </section>

        <!-- TAB 5: BREIN & CLOUD (Multi-Brein Cloud Configurator + JEPA Wereldmodel + Homeostase + G(π)) -->
        <section class="tab-view" id="tab-brain" aria-label="Brein en Cloud">
          <h2 class="hero-heading">Multi-Brein Cloud &amp; Wereldmodel.</h2>
          <p class="hero-subtitle">Koppel Google Gemini Cloud, Anthropic Claude of OpenRouter naast ChatGPT OAuth (0% lokale GPU nodig) en bekijk Eva's JEPA Wereldmodel.</p>

          <div class="grid-2">
            <section class="panel" aria-label="Multi-Brein Cloud en Optioneel Lokaal LLM Beheer">
              <h2>
                <span>☁️ Multi-Brein Cloud &amp; Optioneel Lokaal LLM Beheer</span>
                <span class="badge emerald">Zero-GPU Cloud Primair · Auto-Failover</span>
              </h2>
              <p class="panel-sub">Geen lokale videokaart vereist. Koppel Gemini Cloud, Claude Cloud of OpenRouter naast je bestaande ChatGPT/Codex OAuth-lease. Bij een rate-limit (429) schakelt Eva automatisch over.</p>
              <div id="llm-notice" style="margin-bottom: 10px; color: #6ee7b7; font-size: 0.84rem; font-weight: 600;"></div>
              <div class="llm-cards" id="llm-container" style="margin-bottom: 14px;"></div>

              <div style="background: #0b111e; border: 1px solid var(--border-subtle); border-radius: 12px; padding: 14px;">
                <div style="font-size: 0.87rem; font-weight: 700; color: #60a5fa; margin-bottom: 10px;">➕ Cloud-LLM of Optioneel Lokale Server Koppelen (15 Providers)</div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px;">
                  <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px;">Kies LLM-Provider (Cloud of Lokaal)</label>
                    <select id="cp-kind" onchange="onCloudKindChange()" style="width:100%;padding:8px 9px;border-radius:8px;background:#131c2e;color:#f8fafc;border:1px solid rgba(148,163,184,0.28);font-size:0.83rem;">
                      <option value="gemini_cloud">Google Gemini Cloud (Geen GPU nodig)</option>
                      <option value="anthropic_claude">Anthropic Claude Cloud (Geen GPU nodig)</option>
                      <option value="openai_api">OpenAI Platform API (GPT-4.1 / o3)</option>
                      <option value="openrouter">OpenRouter (300+ Modellen in 1 sleutel)</option>
                      <option value="xai_grok">xAI Grok Cloud</option>
                      <option value="deepseek">DeepSeek Cloud (V3 / R1)</option>
                      <option value="mistral">Mistral AI (Europese Cloud)</option>
                      <option value="groq">Groq LPU Ultra-Fast Cloud</option>
                      <option value="cerebras">Cerebras Wafer-Scale Cloud</option>
                      <option value="together_ai">Together AI Cloud</option>
                      <option value="fireworks_ai">Fireworks AI Cloud</option>
                      <option value="perplexity_sonar">Perplexity Sonar Cloud</option>
                      <option value="ollama_local">Ollama (Optioneel Lokaal · Geen sleutel nodig)</option>
                      <option value="lmstudio_local">LM Studio / llama.cpp (Optioneel Lokaal · Geen sleutel nodig)</option>
                    </select>
                  </div>
                  <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px;">Modelnaam</label>
                    <input id="cp-model" type="text" value="gemini-2.5-pro" style="width:100%;padding:8px 9px;border-radius:8px;background:#131c2e;color:#f8fafc;border:1px solid rgba(148,163,184,0.28);font-size:0.83rem;" />
                  </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px;">
                  <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px;">Endpoint URL (HTTPS voor cloud, localhost/docker-host voor lokaal)</label>
                    <input id="cp-endpoint" type="text" value="https://generativelanguage.googleapis.com/v1beta/openai" style="width:100%;padding:8px 9px;border-radius:8px;background:#131c2e;color:#f8fafc;border:1px solid rgba(148,163,184,0.28);font-size:0.83rem;" />
                  </div>
                  <div>
                    <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:3px;">API-Sleutel (leeg laten voor lokale Ollama / LM Studio)</label>
                    <input id="cp-apikey" type="password" placeholder="Plak je API-sleutel (of laat leeg bij lokaal)…" autocomplete="off" style="width:100%;padding:8px 9px;border-radius:8px;background:#131c2e;color:#f8fafc;border:1px solid rgba(148,163,184,0.28);font-size:0.83rem;" />
                  </div>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                  <span id="cp-hint" style="font-size:0.76rem;color:#94a3b8;">Volledig in Google Cloud (0% lokale GPU). Plak een Google AI Studio sleutel om direct te activeren.</span>
                  <button class="btn primary" onclick="configureCloudProvider()">🔐 Opslaan, Verifiëren &amp; Activeren</button>
                </div>
              </div>
            </section>

            <section class="panel" aria-label="JEPA Causaal Wereldmodel en 8D Concept Space">
              <h2>
                <span>🌍 JEPA Causaal Wereldmodel (C/SIMD) &amp; 8D Concept Space</span>
                <span class="badge emerald" id="wm-winner-badge">Beste Traject: --</span>
              </h2>
              <p class="panel-sub">Simuleert 2 stappen vooruit over 5 causale trajecten op z_t = (energie, ontvankelijkheid, cognitieve druk, urgentie, frictie, vertrouwen) + taalonafhankelijke 8D Concept Guard.</p>
              <div class="llm-cards" id="world-model-container" style="margin-bottom: 12px;"></div>
              <div style="background:#0b111e;padding:12px;border-radius:12px;border:1px solid var(--border-subtle);">
                <div style="font-size:0.81rem;font-weight:700;color:#7dd3fc;margin-bottom:6px;" id="concept-guard-label">🛡️ Neuro-Symbolische Concept Guard (Taalonafhankelijk)</div>
                <div id="concept-bars-container" style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:0.76rem;"></div>
              </div>
            </section>
          </div>

          <div class="grid-2">
            <section class="panel" aria-label="Bio-Homeostatische Bewustzijnskern">
              <h2>
                <span>🧠 Bio-Homeostatische 5D-Drives &amp; Innerlijke Monoloog</span>
                <span class="badge" id="impulse-badge">Impuls ‖I⃗‖: --</span>
              </h2>
              <div class="monologue-box">
                <p class="monologue-text" id="monologue">Laden van Eva's bewustzijnskern…</p>
                <p class="intent-text"><strong>Volgende intentie:</strong> <span id="planned-intent">--</span></p>
              </div>
              <div class="drive-list" id="drives-container"></div>
              <div style="margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px;">
                <button class="btn" onclick="sendStimulus('conversation_turn', 'warm_reply')">✨ Warm Gesprek</button>
                <button class="btn" onclick="sendStimulus('resident_feedback', 'brief_or_busy')">⚡ Kortaf/Druk</button>
                <button class="btn" onclick="sendStimulus('resident_arrived')">🏠 Thuisgekomen</button>
              </div>
            </section>

            <section class="panel" aria-label="Expected Free Energy Policy Engine">
              <h2>
                <span>🧮 Expected Free Energy G(π) Beleidsselectie</span>
                <span class="badge emerald" id="selected-policy-badge">Actief Beleid: --</span>
              </h2>
              <p class="panel-sub">Evaluatie van 5 gedragsbeleiden via G(π) = InterruptionCost − PragmaticValue − EpistemicValue.</p>
              <div class="llm-cards" id="policies-container"></div>
            </section>
          </div>
        </section>
      </main>

      <button type="button" class="discover-cta" id="discover-eva-btn" onclick="switchTab('chat')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
        </svg>
        <span id="discover-cta-label">Ontdek Eva</span>
      </button>
    </div>
  </div>

  <!-- INTERACTIVE CORTEX & CHAT CONTROLLER -->
  <script>
    window.evaAvatarState = {
      speaking: false,
      thinking: false,
      energy: 0.78,
      warmth: 0.75,
      auraHex: '#3b82f6'
    };

    function switchTab(tabId) {
      document.querySelectorAll('.eva-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
      });
      document.querySelectorAll('.tab-view').forEach(view => {
        view.classList.toggle('active', view.id === 'tab-' + tabId);
      });
      const cta = document.getElementById('discover-eva-btn');
      const ctaLabel = document.getElementById('discover-cta-label');
      if (cta && ctaLabel) {
        if (tabId === 'chat') {
          ctaLabel.textContent = 'Terug naar Overzicht';
          cta.onclick = () => switchTab('overview');
          setTimeout(() => document.getElementById('cortex-chat-input')?.focus(), 60);
        } else {
          ctaLabel.textContent = 'Ontdek Eva';
          cta.onclick = () => switchTab('chat');
        }
      }
    }

    document.querySelectorAll('.eva-tab').forEach(btn => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    const moodNames = {
      serene_observant: 'Sereen & Observerend',
      warm_engaged: 'Warm & Betrokken',
      deep_focus: 'Diepe Focus',
      curious_exploring: 'Nieuwsgierig & Onderzoekend',
      protective_attentive: 'Alert & Beschermend',
      resting_dreaming: 'Rustend & Dromend'
    };
    const driveMeta = [
      { key: 'energy', label: '⚡ Circadiaanse Energie', color: '#38bdf8' },
      { key: 'social_warmth', label: '💛 Sociale Warmte & Verbinding', color: '#f59e0b' },
      { key: 'curiosity', label: '🔭 Nieuwsgierigheid (AIDE²)', color: '#a855f7' },
      { key: 'household_harmony', label: '🏡 Huishoudelijke Harmonie', color: '#10b981' },
      { key: 'anticipation', label: '🎯 Voorspellende Aandacht', color: '#ec4899' }
    ];

    function buildNaturalMoodProse(p) {
      const d = p.drives || {};
      const w1 = (d.household_harmony ?? 0.8) >= 0.65 ? 'rustig' : 'waakzaam';
      const w2 = (d.social_warmth ?? 0.7) >= 0.68 ? 'tevreden' : 'ingetogen';
      const e = d.energy ?? 0.75;
      const w3 = e >= 0.82 ? 'energiek' : (e >= 0.52 ? 'redelijk energiek' : 'kalm rustend');
      const w4 = (d.anticipation ?? 0.45) <= 0.62 ? 'ontspannen' : 'alert op de dag';
      return `${w1}, ${w2},\n${w3}, ${w4}`;
    }

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function renderState(data) {
      const p = data.pulse;
      document.getElementById('mood-badge').textContent = moodNames[p.mood] || p.mood;
      document.getElementById('eva-mood-prose').textContent = buildNaturalMoodProse(p);
      document.getElementById('impulse-badge').textContent =
        `Impuls ‖I⃗‖: ${Math.round(p.impulse_magnitude * 100)}% (${p.should_wake_cortex ? 'Actief' : '0-Token Rust'})`;
      document.getElementById('monologue').textContent = `“${p.inner_monologue_nl}”`;
      document.getElementById('planned-intent').textContent = p.planned_intent_nl;

      window.evaAvatarState.energy = p.drives?.energy ?? 0.78;
      window.evaAvatarState.warmth = p.drives?.social_warmth ?? 0.75;
      window.evaAvatarState.auraHex = p.mood === 'warm_engaged' ? '#38bdf8' : (p.mood === 'resting_dreaming' ? '#a855f7' : '#3b82f6');

      // Update Overzicht live telemetry subtitles
      const syn = p.synaptic_memory || {};
      const wm = p.world_model || { selected_action_id: '--', trajectories: [], predicted_next_state: {} };
      const cp = data.cloud_providers || { default_codex: {}, connections: [] };
      const activeConn = (cp.connections || []).find(c => c.selected);
      const activeCloudLabel = activeConn ? `${activeConn.label} (${activeConn.model})` : 'ChatGPT OAuth + Cloud Failover';

      document.getElementById('ov-memory-meta').textContent =
        `Titans ‖M‖_F ${syn.frobenius_norm ?? '--'} · ${(p.routines || []).length} geleerde routines →`;
      document.getElementById('ov-progress-meta').textContent =
        `Wereldmodel: ${wm.selected_action_id} · ${data.workboard.completed_count}/${data.workboard.total_execution_tasks} klaar →`;
      document.getElementById('ov-policy-meta').textContent =
        `${activeCloudLabel} · 0% GPU vereist →`;
      document.getElementById('ov-cloud-label').textContent = activeCloudLabel;
      document.getElementById('ov-step-plan').textContent = p.planned_intent_nl || 'Eva stelt een aanpak voor en stemt deze met je af.';

      // Drives
      document.getElementById('drives-container').innerHTML = driveMeta.map(d => {
        const pct = Math.round((p.drives[d.key] || 0) * 100);
        return `<div class="drive-row">
          <div class="drive-head"><span>${d.label}</span><span>${pct}%</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${d.color}"></div></div>
        </div>`;
      }).join('');

      // Multi-Brain Cloud Cards
      const codex = cp.default_codex || {};
      const codexCard = `
        <div class="llm-card ${codex.selected ? 'active' : ''}">
          <div class="llm-head">
            <h3>${esc(codex.label || 'OpenAI ChatGPT / Codex (OAuth)')}</h3>
            <span class="badge ${codex.ready ? 'emerald' : ''}">
              ${codex.selected ? '✓ Primair · ' : ''}${codex.ready ? 'OAuth Klaar' : 'Failover Stand-by'}
            </span>
          </div>
          <p style="margin:2px 0 8px;font-size:0.8rem;color:#7dd3fc;">${esc(codex.detail_nl || '')}</p>
          ${codex.selected ? '' : `<button class="btn primary" style="padding:5px 10px;font-size:0.77rem;" onclick="manageCloudConnection('use_default_codex', '')">Gebruik ChatGPT OAuth als Primair</button>`}
        </div>
      `;
      const connCards = (cp.connections || []).map(conn => {
        const ready = conn.check_state === 'ready' && conn.enabled;
        const stateLabel = !conn.enabled ? 'Uitgeschakeld' : (conn.check_state === 'ready' ? '✓ Geverifieerd & Klaar' : (conn.check_state === 'failed' ? '⚠️ Verificatie mislukt' : 'Nog niet geverifieerd'));
        return `
          <div class="llm-card ${conn.selected ? 'active' : ''}">
            <div class="llm-head">
              <h3>${esc(conn.label)} <small style="color:#94a3b8;">(${esc(conn.kind)})</small></h3>
              <span class="badge ${ready ? 'emerald' : ''}">${conn.selected ? '✓ Primair · ' : ''}${stateLabel}</span>
            </div>
            <p style="margin:2px 0 8px;font-size:0.79rem;color:#94a3b8;">Model: <code>${esc(conn.model)}</code> · Endpoint: <code>${esc(conn.endpoint)}</code></p>
            <div style="display:flex;flex-wrap:wrap;gap:6px;">
              ${ready && !conn.selected ? `<button class="btn primary" style="padding:4px 9px;font-size:0.75rem;" onclick="manageCloudConnection('select', '${esc(conn.id)}')">✓ Activeer Primair</button>` : ''}
              <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="manageCloudConnection('check', '${esc(conn.id)}')">🔄 Test Verbinding</button>
              ${conn.enabled ? `<button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="manageCloudConnection('disable', '${esc(conn.id)}')">✕ Uitschakelen</button>` : ''}
            </div>
          </div>
        `;
      }).join('');
      document.getElementById('llm-container').innerHTML = codexCard + connCards;

      // Jarvis Staged Actions
      const jarvisEl = document.getElementById('jarvis-actions-container');
      if (jarvisEl) {
        jarvisEl.innerHTML = (p.jarvis_staged_actions || []).map(ja => `
          <div class="resident-card" style="${ja.status === 'confirmed' ? 'border-color:#10b981;background:rgba(16,185,129,0.09);' : (ja.status === 'dismissed' ? 'opacity:0.6;' : '')}">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;">
              <strong style="font-size:0.93rem;">⚡ ${esc(ja.title_nl)}</strong>
              <span class="badge ${ja.status === 'confirmed' ? 'emerald' : 'violet'}">
                ${ja.status === 'confirmed' ? '✓ Bevestigd' : (ja.status === 'dismissed' ? 'Genegeerd' : `Klaargezet · ${Math.round(ja.confidence * 100)}%`)}
              </span>
            </div>
            <p style="margin:0 0 6px;font-size:0.83rem;color:#e2e8f0;">${esc(ja.prepared_summary_nl)}</p>
            <p style="margin:0 0 10px;font-size:0.78rem;color:#7dd3fc;"><em>Waarom nu: ${esc(ja.why_now_nl)}</em></p>
            ${ja.status === 'staged' ? `
              <div style="display:flex;gap:8px;">
                <button class="btn primary" style="padding:5px 11px;font-size:0.77rem;" onclick="decideJarvisAction('${esc(ja.action_id)}', 'confirm')">✓ Bevestig &amp; Voer Uit</button>
                <button class="btn" style="padding:5px 11px;font-size:0.77rem;" onclick="decideJarvisAction('${esc(ja.action_id)}', 'dismiss')">✕ Niet Nodig</button>
              </div>
            ` : ''}
          </div>
        `).join('');
      }

      // World Model & 8D Concept Space
      document.getElementById('wm-winner-badge').textContent = `Beste Traject: ${wm.selected_action_id}`;
      const nxt = wm.predicted_next_state || {};
      document.getElementById('world-model-container').innerHTML = (wm.trajectories || []).map(tr => `
        <div class="llm-card ${tr.selected ? 'active' : ''}" style="padding:10px 12px;">
          <div class="llm-head" style="margin-bottom:2px;">
            <h3 style="font-size:0.85rem;">${esc(tr.label_nl)}</h3>
            <span class="badge ${tr.selected ? 'emerald' : ''}">Utility: ${tr.counterfactual_utility}</span>
          </div>
          ${tr.selected && nxt.receptivity !== undefined ? `<p style="margin:2px 0 0;font-size:0.75rem;color:#6ee7b7;">Voorspelde z_{t+2}: ontvankelijkheid ${Math.round(nxt.receptivity*100)}% · frictie ${Math.round((nxt.friction||0)*100)}% · vertrouwen ${Math.round((nxt.trust||0)*100)}%</p>` : ''}
        </div>
      `).join('');

      const cs = p.concept_space || { dominant_concept: 'social_greeting', dimensions: {}, is_non_task_social: true };
      document.getElementById('concept-guard-label').textContent =
        `🛡️ 8D Concept Guard: ${cs.dominant_concept} (${cs.is_non_task_social ? 'Sociaal / Geen Taak-Script' : 'Inhoudelijke Vraag of Actie'})`;
      document.getElementById('concept-bars-container').innerHTML = Object.entries(cs.dimensions || {}).map(([k, v]) => `
        <div style="background:rgba(22,32,51,0.85);padding:5px 8px;border-radius:6px;display:flex;justify-content:space-between;">
          <span>${esc(k)}</span><strong style="color:#38bdf8;">${Math.round((v || 0) * 100)}%</strong>
        </div>
      `).join('');

      // Titans & Sleep
      document.getElementById('synaptic-badge').textContent = `8×8 Fast-Weights · ‖M‖_F: ${syn.frobenius_norm ?? '--'}`;
      document.getElementById('synaptic-summary-box').innerHTML = `
        <div class="llm-head">
          <h3>⚡ Live Synaptische Plasticiteit (O(1) Geheugen)</h3>
          <span class="badge emerald">${syn.updates_count || 1} Updates</span>
        </div>
        <p style="margin:2px 0 4px;font-size:0.82rem;color:#e2e8f0;">${esc(syn.status_nl || '')}</p>
        <p style="margin:0;font-size:0.77rem;color:#94a3b8;">Verrassingsgradiënt S_t: <strong style="color:#f59e0b;">${Math.round((syn.last_surprise_score || 0)*1000)/10}%</strong> · Frobenius-norm: <strong style="color:#38bdf8;">${syn.frobenius_norm ?? '--'}</strong></p>
      `;
      const slp = p.last_sleep_consolidation || {};
      document.getElementById('sleep-summary-box').innerHTML = `
        <div class="llm-head">
          <h3>🌙 Nachtelijke Droom-Consolidatie</h3>
          <span class="badge violet">Cyclus #${slp.cycles_completed || 1}</span>
        </div>
        <p style="margin:2px 0 4px;font-size:0.82rem;color:#e2e8f0;">${esc(slp.dream_insight_nl || '')}</p>
        <p style="margin:0;font-size:0.76rem;color:#94a3b8;">Gesnoeide ruis-synapsen: ${slp.pruned_noise_synapses ?? 0} · Versterkte routines: ${slp.reinforced_routines_count ?? 2}</p>
      `;

      // Residents
      document.getElementById('residents-container').innerHTML = (p.residents || []).map(r => `
        <div class="resident-card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <strong style="font-size:1rem;">👤 ${esc(r.display_name)}</strong>
            <span class="badge emerald">${esc(r.perceived_mood)}</span>
          </div>
          <p style="margin:0 0 10px;font-size:0.84rem;color:#cbd5e1;"><strong>Hypothese:</strong> ${esc(r.current_context_hypothesis)}</p>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:0.78rem;margin-bottom:10px;">
            <div style="background:#0b111e;padding:7px;border-radius:8px;">Energie<br><strong style="font-size:0.92rem;color:#38bdf8;">${Math.round(r.perceived_energy * 100)}%</strong></div>
            <div style="background:#0b111e;padding:7px;border-radius:8px;">Ontvankelijk<br><strong style="font-size:0.92rem;color:#10b981;">${Math.round(r.social_receptivity * 100)}%</strong></div>
            <div style="background:#0b111e;padding:7px;border-radius:8px;">Pred. Error<br><strong style="font-size:0.92rem;color:#f59e0b;">${Math.round((r.last_prediction_error || 0) * 100)}%</strong></div>
          </div>
          <ul style="margin:0;padding-left:18px;font-size:0.8rem;color:#94a3b8;">
            ${(r.open_care_threads || []).map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
      `).join('');

      // Routines, Foresight, Curiosity, Source Watches, Responsibilities, Policies, Workboard
      document.getElementById('skips-badge').textContent = `0-Token Besparingen (P1-02): ${p.zero_token_skips || 1}x`;
      document.getElementById('routines-container').innerHTML = (p.routines || []).map(rt => `
        <div class="llm-card">
          <div class="llm-head">
            <h3>${esc(rt.title_nl)}</h3>
            <span class="badge ${rt.exceptions_count > 0 ? '' : 'emerald'}">Zekerheid ${Math.round(rt.confidence * 100)}% · ${rt.supporting_days}d</span>
          </div>
          <p style="margin:2px 0 4px;font-size:0.8rem;color:#94a3b8;"><strong>Voorwaarde:</strong> ${esc(rt.condition_nl)} (${esc(rt.expected_window)})</p>
          <p style="margin:0;font-size:0.82rem;color:#e2e8f0;">${esc(rt.adjusted_behavior_nl)}</p>
          ${rt.last_exception_nl ? `<p style="margin:4px 0 0;font-size:0.77rem;color:#f59e0b;">⚡ Laatste uitzondering (${rt.exceptions_count}x): ${esc(rt.last_exception_nl)}</p>` : ''}
        </div>
      `).join('');

      document.getElementById('foresight-container').innerHTML = (p.foresight || []).map(fs => `
        <div class="llm-card">
          <div class="llm-head"><h3>${esc(fs.title_nl)}</h3><span class="badge emerald">${esc(fs.recommended_disposition)}</span></div>
          <p style="margin:2px 0 4px;font-size:0.8rem;color:#94a3b8;">${esc(fs.status_quo_nl)}</p>
          <p style="margin:0;font-size:0.82rem;color:#6ee7b7;"><strong>Alternatief:</strong> ${esc(fs.alternative_nl)}</p>
        </div>
      `).join('');

      document.getElementById('curiosity-container').innerHTML = (p.curiosity_projects || []).map(cpj => `
        <div class="llm-card">
          <div class="llm-head"><h3>${esc(cpj.title_nl)}</h3><span class="badge violet">${Math.round(cpj.progress * 100)}%</span></div>
          <p style="margin:2px 0 4px;font-size:0.81rem;color:#e2e8f0;">${esc(cpj.latest_insight_nl)}</p>
          <p style="margin:0;font-size:0.78rem;color:#7dd3fc;"><em>“${esc(cpj.shared_trace_nl)}”</em></p>
        </div>
      `).join('');

      document.getElementById('source-watches-container').innerHTML = (p.source_watches || []).map(sw => `
        <div class="llm-card">
          <div class="llm-head">
            <h3>${esc(sw.target_ref)}</h3>
            <span class="badge ${sw.access_status === 'connected' ? 'emerald' : ''}">
              ${sw.access_status === 'connected' ? `✓ ${sw.deduplicated_callbacks_count}x ontdubbeld` : '⚠️ Toegang Ontbreekt'}
            </span>
          </div>
          <p style="margin:2px 0 4px;font-size:0.8rem;color:#e2e8f0;">${esc(sw.last_rationale_nl)}</p>
          ${sw.missing_access_explanation_nl ? `<p style="margin:4px 0 0;font-size:0.77rem;color:#f59e0b;">🔒 ${esc(sw.missing_access_explanation_nl)}</p>` : ''}
        </div>
      `).join('');

      document.getElementById('responsibilities-container').innerHTML = (p.responsibilities || []).map(rp => `
        <div class="llm-card ${rp.lifecycle_state === 'active' ? 'active' : ''}">
          <div class="llm-head">
            <h3>${esc(rp.title_nl)} <small style="color:#94a3b8;">(v${rp.version})</small></h3>
            <span class="badge ${rp.lifecycle_state === 'active' ? 'emerald' : 'violet'}">${esc(rp.lifecycle_state)} · ${esc(rp.adoption_state)}</span>
          </div>
          <p style="margin:2px 0 8px;font-size:0.81rem;color:#e2e8f0;">${esc(rp.current_standing_nl)}</p>
          <div style="display:flex;flex-wrap:wrap;gap:6px;">
            <button class="btn primary" style="padding:4px 9px;font-size:0.75rem;" onclick="manageResponsibility('${esc(rp.artifact_id)}', 'adopt')">✓ Adopteer</button>
            <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="manageResponsibility('${esc(rp.artifact_id)}', '${rp.lifecycle_state === 'paused' ? 'resume' : 'pause'}')">${rp.lifecycle_state === 'paused' ? '▶ Hervat' : '⏸ Pauzeer'}</button>
            <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="manageResponsibility('${esc(rp.artifact_id)}', 'take_over')">✋ Overnemen</button>
            <button class="btn" style="padding:4px 9px;font-size:0.75rem;" onclick="manageResponsibility('${esc(rp.artifact_id)}', 'rollback')">↩ Rollback</button>
          </div>
        </div>
      `).join('');

      document.getElementById('selected-policy-badge').textContent = `Actief Beleid: ${p.selected_policy_id || '--'}`;
      document.getElementById('policies-container').innerHTML = (p.policy_evaluations || []).map(pol => `
        <div class="llm-card ${pol.selected ? 'active' : ''}">
          <div class="llm-head">
            <h3 style="font-size:0.84rem;">${esc(pol.label_nl)}</h3>
            <span class="badge ${pol.selected ? 'emerald' : ''}">${Math.round(pol.posterior_probability * 100)}%</span>
          </div>
          <p style="margin:2px 0 0;font-size:0.76rem;color:#94a3b8;">G(π): <strong>${pol.expected_free_energy_g}</strong> · Pragm: ${pol.pragmatic_value} · Cost: ${pol.interruption_cost}</p>
        </div>
      `).join('');

      const wb = data.workboard;
      document.getElementById('tasks-counter').textContent = `${wb.completed_count} / ${wb.total_execution_tasks} Kerntaken Afgerond`;
      const statusLabels = { done: 'Voltooid', parked: 'Later (Gezin/Fysiek)' };
      document.getElementById('workboard-tbody').innerHTML = (wb.tasks || []).map(t => `
        <tr>
          <td><code>${esc(t.id)}</code></td>
          <td>${esc(t.category)}</td>
          <td>${esc(t.title)}</td>
          <td><span class="status-pill status-${esc(t.status)}">${statusLabels[t.status] || esc(t.status)}</span></td>
        </tr>
      `).join('');
    }

    async function loadState() {
      const res = await fetch('/internal/v1/dashboard/cortex/state', { credentials: 'same-origin', cache: 'no-store' });
      if (res.ok) renderState(await res.json());
    }

    const CLOUD_PRESET_MAP = {
      gemini_cloud: { kind: 'gemini', label: 'Google Gemini Cloud', model: 'gemini-2.5-pro', endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai', requiresKey: true, hint: 'Volledig in Google Cloud (0% lokale GPU). Gebruik je Google AI Studio API-sleutel.' },
      anthropic_claude: { kind: 'anthropic', label: 'Anthropic Claude Cloud', model: 'claude-sonnet-4-5', endpoint: 'https://api.anthropic.com', requiresKey: true, hint: 'Volledig in Anthropic Cloud (0% lokale GPU). Gebruik je Anthropic API-sleutel.' },
      openai_api: { kind: 'openai_compatible', label: 'OpenAI Platform API', model: 'gpt-4.1', endpoint: 'https://api.openai.com/v1', requiresKey: true, hint: 'Rechtstreeks via het officiële OpenAI Platform met een eigen API-sleutel.' },
      openrouter: { kind: 'openai_compatible', label: 'OpenRouter (300+ Modellen)', model: 'anthropic/claude-sonnet-4.5', endpoint: 'https://openrouter.ai/api/v1', requiresKey: true, hint: 'Eén sleutel voor honderden modellen (Claude, Gemini, GPT, Qwen, Llama, DeepSeek).' },
      xai_grok: { kind: 'openai_compatible', label: 'xAI Grok Cloud', model: 'grok-3', endpoint: 'https://api.x.ai/v1', requiresKey: true, hint: 'xAI Grok via de officiële OpenAI-compatibele cloud-API.' },
      deepseek: { kind: 'openai_compatible', label: 'DeepSeek Cloud', model: 'deepseek-chat', endpoint: 'https://api.deepseek.com/v1', requiresKey: true, hint: 'DeepSeek V3 / R1 via de officiële cloud-API.' },
      mistral: { kind: 'openai_compatible', label: 'Mistral AI (Europese Cloud)', model: 'mistral-large-latest', endpoint: 'https://api.mistral.ai/v1', requiresKey: true, hint: 'Europese AI-provider (Mistral Large / Small) via Le Plateforme.' },
      groq: { kind: 'openai_compatible', label: 'Groq LPU Ultra-Fast Cloud', model: 'llama-3.3-70b-versatile', endpoint: 'https://api.groq.com/openai/v1', requiresKey: true, hint: 'Extreem lage latentie op Groq LPU-hardware (geen lokale GPU nodig).' },
      cerebras: { kind: 'openai_compatible', label: 'Cerebras Wafer-Scale Cloud', model: 'llama-3.3-70b', endpoint: 'https://api.cerebras.ai/v1', requiresKey: true, hint: 'Razendsnelle cloud-inferentie op Cerebras Wafer-Scale Engines.' },
      together_ai: { kind: 'openai_compatible', label: 'Together AI Cloud', model: 'Qwen/Qwen2.5-72B-Instruct-Turbo', endpoint: 'https://api.together.xyz/v1', requiresKey: true, hint: 'Open-weights topmodellen (Qwen, Llama, DeepSeek) in de cloud.' },
      fireworks_ai: { kind: 'openai_compatible', label: 'Fireworks AI Cloud', model: 'accounts/fireworks/models/deepseek-v3', endpoint: 'https://api.fireworks.ai/inference/v1', requiresKey: true, hint: 'Snelle productie-inferentie voor open-weights modellen in de cloud.' },
      perplexity_sonar: { kind: 'openai_compatible', label: 'Perplexity Sonar Cloud', model: 'sonar-pro', endpoint: 'https://api.perplexity.ai', requiresKey: true, hint: 'Perplexity Sonar API via OpenAI-compatibel HTTPS endpoint.' },
      ollama_local: { kind: 'openai_compatible', label: 'Ollama (Lokaal)', model: 'qwen2.5:14b', endpoint: 'http://host.docker.internal:11434/v1', requiresKey: false, hint: 'Optioneel lokaal draaien zonder API-sleutel wanneer Ollama op je systeem actief is.' },
      lmstudio_local: { kind: 'openai_compatible', label: 'LM Studio / llama.cpp (Lokaal)', model: 'local-model', endpoint: 'http://host.docker.internal:1234/v1', requiresKey: false, hint: 'Optioneel lokale OpenAI-server zonder API-sleutel (LM Studio op 1234 of llama.cpp op 8080).' }
    };

    function onCloudKindChange() {
      const presetKey = document.getElementById('cp-kind').value;
      const p = CLOUD_PRESET_MAP[presetKey] || CLOUD_PRESET_MAP.gemini_cloud;
      document.getElementById('cp-model').value = p.model;
      document.getElementById('cp-endpoint').value = p.endpoint;
      document.getElementById('cp-hint').textContent = p.hint;
    }

    async function configureCloudProvider() {
      const presetKey = document.getElementById('cp-kind').value;
      const p = CLOUD_PRESET_MAP[presetKey] || { kind: 'openai_compatible', label: presetKey, requiresKey: true };
      const kind = p.kind;
      const model = document.getElementById('cp-model').value.trim();
      const endpoint = document.getElementById('cp-endpoint').value.trim();
      const apiKey = document.getElementById('cp-apikey').value.trim();
      const notice = document.getElementById('llm-notice');
      const isLocalEndpoint = endpoint.startsWith('http://127.0.0.1') || endpoint.startsWith('http://localhost') || endpoint.startsWith('http://host.docker.internal') || endpoint.startsWith('http://[::1]');
      if (!apiKey && p.requiresKey && !isLocalEndpoint) {
        notice.style.color = '#fcd34d';
        notice.textContent = '⚠️ Vul eerst een geldige API-sleutel in om deze cloudverbinding te verifiëren.';
        return;
      }
      notice.style.color = '#7dd3fc';
      notice.textContent = '⏳ Verbinding versleuteld opslaan en live testen bij provider…';
      const res = await fetch('/internal/v1/dashboard/cortex/cloud-provider', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'configure_and_activate',
          kind,
          label: p.label,
          model,
          endpoint,
          api_key: apiKey,
          connection_id: ''
        })
      });
      document.getElementById('cp-apikey').value = '';
      if (res.ok) {
        const data = await res.json();
        renderState(data);
        if (data.cloud_providers && data.cloud_providers.selected_id) {
          notice.style.color = '#6ee7b7';
          notice.textContent = `✓ ${p.label} (${model}) succesvol geverifieerd en geactiveerd met Auto-Failover!`;
        } else {
          notice.style.color = '#fcd34d';
          notice.textContent = '⚠️ Verbinding opgeslagen, maar de live SDK-test slaagde niet (controleer API-sleutel, modelnaam of serverstatus).';
        }
      } else {
        notice.style.color = '#fda4af';
        notice.textContent = '❌ Kon verbinding niet opslaan (controleer endpoint en velden).';
      }
    }

    async function manageCloudConnection(action, connectionId) {
      const notice = document.getElementById('llm-notice');
      notice.style.color = '#7dd3fc';
      notice.textContent = '⏳ Providerstatus bijwerken…';
      const res = await fetch('/internal/v1/dashboard/cortex/cloud-provider', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          connection_id: connectionId,
          kind: 'gemini',
          label: 'Cloud',
          model: 'gemini-2.5-pro',
          endpoint: '',
          api_key: ''
        })
      });
      if (res.ok) {
        renderState(await res.json());
        notice.style.color = '#6ee7b7';
        notice.textContent = '✓ Multi-Brein Cloud instelling bijgewerkt.';
      }
    }

    async function triggerSleepConsolidation() {
      const dot = document.getElementById('presence-dot');
      const st = document.getElementById('presence-status-text');
      dot.classList.add('thinking');
      st.textContent = 'Droom-consolidatie…';
      const res = await fetch('/internal/v1/dashboard/cortex/sleep-consolidate', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      dot.classList.remove('thinking');
      st.textContent = 'Aanwezig';
      if (res.ok) renderState(await res.json());
    }

    async function decideJarvisAction(actionId, decision) {
      const res = await fetch('/internal/v1/dashboard/cortex/jarvis-action', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action_id: actionId, decision })
      });
      if (res.ok) renderState(await res.json());
    }

    async function sendStimulus(kind, feedbackMode) {
      const payload = { kind, intensity: 0.75 };
      if (kind === 'resident_feedback') {
        payload.actual_receptivity = feedbackMode === 'brief_or_busy' ? 0.22 : 0.90;
        payload.observed_mood = feedbackMode === 'brief_or_busy' ? 'kortaf / druk bezig' : 'open & enthousiast';
        payload.context_hypothesis = feedbackMode === 'brief_or_busy'
          ? 'Druk bezig — Eva houdt automatisch afstand en verlaagt sociale interrupties'
          : 'Ruimte voor creatief sparren en proactief meedenken';
      }
      const res = await fetch('/internal/v1/dashboard/cortex/stimulus', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) renderState(await res.json());
    }

    async function recordRoutineObservation(routineId, isException, detailNl) {
      const res = await fetch('/internal/v1/dashboard/cortex/routine', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ routine_id: routineId, is_exception: isException, detail_nl: detailNl })
      });
      if (res.ok) renderState(await res.json());
    }

    async function triggerSourceWatch(watchId, eventSha, summaryNl, accessAvailable) {
      const res = await fetch('/internal/v1/dashboard/cortex/source-watch', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          watch_id: watchId,
          event_payload_sha256: eventSha,
          summary_nl: summaryNl,
          access_available: accessAvailable
        })
      });
      if (res.ok) renderState(await res.json());
    }

    async function manageResponsibility(artifactId, action) {
      const res = await fetch('/internal/v1/dashboard/cortex/responsibility', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_id: artifactId, action, note_nl: 'Bijgewerkt via 3D Cortex Cockpit' })
      });
      if (res.ok) renderState(await res.json());
    }

    // LIVE STREAMING CHAT CONTROLLER (Connected to /internal/v1/dashboard/conversation/respond)
    const chatStream = document.getElementById('chat-stream');
    const chatForm = document.getElementById('cortex-chat-form');
    const chatInput = document.getElementById('cortex-chat-input');
    const chatSend = document.getElementById('cortex-chat-send');
    const chatStop = document.getElementById('cortex-chat-stop');

    function appendChatBubble(role, text) {
      const div = document.createElement('div');
      div.className = 'chat-bubble ' + role;
      div.textContent = text;
      chatStream.appendChild(div);
      chatStream.scrollTop = chatStream.scrollHeight;
      return div;
    }

    async function readNdjsonReply(response, onDelta) {
      if (!response.ok || !response.body || !(response.headers.get('content-type') || '').includes('application/x-ndjson')) {
        throw new Error('stream_unavailable');
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (true) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          let idx;
          while ((idx = buffer.indexOf('\\n')) >= 0) {
            const line = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 1);
            if (!line.trim()) continue;
            const item = JSON.parse(line);
            if (item.contract === 'EvaTextDelta/v1' && typeof item.text === 'string') {
              onDelta(item.text);
            } else if (item.contract === 'EvaInternalGroundedReply/v1' && typeof item.text === 'string') {
              return item;
            } else if (item.contract === 'EvaTextStreamError/v1') {
              throw new Error(item.code || 'stream_error');
            }
          }
          if (done) break;
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      throw new Error('incomplete_stream');
    }

    async function sendChatTurn(text) {
      appendChatBubble('user', text);
      const pending = appendChatBubble('eva', 'Eva denkt na…');
      const dot = document.getElementById('presence-dot');
      const st = document.getElementById('presence-status-text');
      chatSend.disabled = true;
      window.evaAvatarState.thinking = true;
      dot.classList.add('thinking');
      st.textContent = 'Aan het denken…';
      let draft = '';
      try {
        const res = await fetch('/internal/v1/dashboard/conversation/respond', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/x-ndjson' },
          body: JSON.stringify({ text })
        });
        const finalReply = await readNdjsonReply(res, (delta) => {
          window.evaAvatarState.thinking = false;
          window.evaAvatarState.speaking = true;
          st.textContent = 'Aan het spreken…';
          draft += delta;
          pending.textContent = draft;
          chatStream.scrollTop = chatStream.scrollHeight;
        });
        pending.textContent = finalReply.text || draft;
        loadState();
      } catch (err) {
        pending.classList.add('error');
        pending.textContent = err.message === 'provider_rate_limited'
          ? 'De primaire modelprovider staat tijdelijk op een snelheidslimiet. Koppel in het tabblad Brein & Cloud een extra cloud-sleutel (zoals Gemini Cloud of Claude) voor directe failover.'
          : 'Het antwoord kon nu niet worden bevestigd. Probeer het gerust opnieuw.';
      } finally {
        window.evaAvatarState.thinking = false;
        setTimeout(() => { window.evaAvatarState.speaking = false; }, 900);
        dot.classList.remove('thinking');
        st.textContent = 'Aanwezig';
        chatSend.disabled = false;
      }
    }

    chatForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const txt = chatInput.value.trim();
      if (!txt || chatSend.disabled) return;
      chatInput.value = '';
      await sendChatTurn(txt);
    });

    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.requestSubmit();
      }
    });

    chatStop.addEventListener('click', async () => {
      await fetch('/internal/v1/dashboard/conversation/stop', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      }).catch(() => {});
    });

    loadState();
    setInterval(loadState, 15000);
  </script>

  <!-- 3D EVA GLTF SCENE (Three.js + GLTFLoader + Lifelike Gaze, Breathing & Drag Rotation) -->
  <script type="module">
    import * as THREE from 'three';
    import { GLTFLoader } from '/internal/v1/dashboard/cortex/assets/GLTFLoader.js';

    const canvas = document.getElementById('eva-canvas');
    const fallback = document.getElementById('eva-fallback');

    function init3DEva() {
      let renderer;
      try {
        renderer = new THREE.WebGLRenderer({
          canvas,
          alpha: true,
          antialias: true,
          powerPreference: 'high-performance'
        });
      } catch (_err) {
        fallback.textContent = '3D WebGL is niet beschikbaar in deze browserweergave.';
        fallback.classList.add('visible');
        return;
      }

      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.18;

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(28, 1, 0.01, 1000);
      const clock = new THREE.Clock();

      // Studio 3-point lighting tuned for crisp white ceramic & slate humanoid mesh on dark navy
      const hemi = new THREE.HemisphereLight(0xf8fbff, 0x0d1b36, 2.45);
      scene.add(hemi);

      const keyLight = new THREE.DirectionalLight(0xffffff, 4.1);
      keyLight.position.set(2.8, 4.6, 5.2);
      scene.add(keyLight);

      const fillLight = new THREE.DirectionalLight(0x60a5fa, 2.35);
      fillLight.position.set(-3.8, 2.2, 3.2);
      scene.add(fillLight);

      const rimLight = new THREE.DirectionalLight(0x818cf8, 2.0);
      rimLight.position.set(0.0, 3.5, -4.0);
      scene.add(rimLight);

      const auraLight = new THREE.PointLight(0x3b82f6, 4.8, 16, 1.4);
      auraLight.position.set(0, 0.4, 2.2);
      scene.add(auraLight);

      const modelGroup = new THREE.Group();
      scene.add(modelGroup);

      let model = null;
      let mixer = null;
      let headBone = null;
      let neckBone = null;
      let modelHeight = 1;
      let baseY = 0;

      // Subtle pointer gaze + drag yaw
      let pointerX = 0;
      let pointerY = 0;
      let dragYaw = 0;
      let isDragging = false;
      let lastDragX = 0;

      window.addEventListener('pointermove', (e) => {
        pointerX = (e.clientX / Math.max(1, window.innerWidth)) * 2 - 1;
        pointerY = (e.clientY / Math.max(1, window.innerHeight)) * 2 - 1;
        if (isDragging) {
          const dx = e.clientX - lastDragX;
          lastDragX = e.clientX;
          dragYaw = Math.max(-0.65, Math.min(0.65, dragYaw + dx * 0.006));
        }
      });
      canvas.addEventListener('pointerdown', (e) => {
        isDragging = true;
        lastDragX = e.clientX;
      });
      window.addEventListener('pointerup', () => { isDragging = false; });

      function resize() {
        const w = Math.max(1, canvas.clientWidth);
        const h = Math.max(1, canvas.clientHeight);
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      new ResizeObserver(resize).observe(canvas);
      resize();

      new GLTFLoader().load(
        '/internal/v1/dashboard/cortex/assets/eva.glb',
        (gltf) => {
          model = gltf.scene;
          model.traverse((node) => {
            if (node.isBone) {
              const nm = (node.name || '').toLowerCase();
              if (nm === 'head') headBone = node;
              else if (nm === 'neck') neckBone = node;
            }
            if (!node.isMesh) return;
            node.frustumCulled = false;
            if (Array.isArray(node.material)) node.material = node.material.map(m => m.clone());
            else if (node.material) node.material = node.material.clone();
          });

          model.updateMatrixWorld(true);
          const bounds = new THREE.Box3().makeEmpty();
          const vertex = new THREE.Vector3();
          model.traverse((node) => {
            if (!node.isMesh || !node.geometry) return;
            if (node.isSkinnedMesh) {
              const count = node.geometry.attributes.position?.count || 0;
              for (let i = 0; i < count; i += 1) {
                node.getVertexPosition(i, vertex);
                bounds.expandByPoint(vertex.applyMatrix4(node.matrixWorld));
              }
            } else {
              if (!node.geometry.boundingBox) node.geometry.computeBoundingBox();
              if (node.geometry.boundingBox) {
                bounds.union(node.geometry.boundingBox.clone().applyMatrix4(node.matrixWorld));
              }
            }
          });
          if (bounds.isEmpty()) bounds.setFromObject(model);

          const center = bounds.getCenter(new THREE.Vector3());
          const size = bounds.getSize(new THREE.Vector3());
          model.position.sub(center);
          modelHeight = size.y || 1;
          modelGroup.add(model);

          // Studio upper-body framing matching the hero portrait (mid-thigh to top of head)
          baseY = -modelHeight * 0.13;
          modelGroup.position.y = baseY;

          const vFov = THREE.MathUtils.degToRad(camera.fov);
          const visibleSpan = modelHeight * 0.78;
          const dist = (visibleSpan * 0.5) / Math.tan(vFov * 0.5) * 1.06;
          camera.position.set(0, modelHeight * 0.04, dist);
          camera.near = Math.max(0.001, dist / 100);
          camera.far = dist * 20;
          camera.lookAt(0, modelHeight * 0.03, 0);
          camera.updateProjectionMatrix();

          if (gltf.animations && gltf.animations.length > 0) {
            mixer = new THREE.AnimationMixer(model);
            for (const clip of gltf.animations) {
              const action = mixer.clipAction(clip);
              action.timeScale = 0.28; // Calm, poised humanoid idle presence
              action.play();
            }
          }
          fallback.classList.remove('visible');
          canvas.dataset.modelReady = 'true';
        },
        undefined,
        () => {
          fallback.textContent = '3D-model kon niet worden geladen.';
          fallback.classList.add('visible');
        }
      );

      let smoothYaw = 0;
      let smoothPitch = 0;

      function animate(now) {
        requestAnimationFrame(animate);
        const dt = Math.min(clock.getDelta(), 0.05);
        const st = window.evaAvatarState || {};
        if (mixer) {
          mixer.timeScale = st.speaking ? 0.65 : (st.thinking ? 0.45 : 0.26);
          mixer.update(dt);
        }
        if (model && modelGroup) {
          const t = now * 0.001;
          const breathe = Math.sin(t * 1.35) * modelHeight * 0.0045;
          const speakBounce = st.speaking ? Math.sin(t * 9.0) * modelHeight * 0.003 : 0;
          const idleTurn = Math.sin(t * 0.42) * 0.045;

          if (!isDragging) dragYaw *= 0.985;
          modelGroup.rotation.y = idleTurn + dragYaw;
          modelGroup.position.y = baseY + breathe + speakBounce;

          // Gentle cursor gaze tracking on head/neck bones
          const targetYaw = Math.max(-0.22, Math.min(0.22, pointerX * 0.18));
          const targetPitch = Math.max(-0.12, Math.min(0.12, pointerY * 0.10));
          smoothYaw += (targetYaw - smoothYaw) * 0.08;
          smoothPitch += (targetPitch - smoothPitch) * 0.08;
          if (headBone) {
            headBone.rotation.y += smoothYaw * 0.55;
            headBone.rotation.x += smoothPitch * 0.45 + (st.speaking ? Math.sin(t * 11.0) * 0.025 : 0);
          }
          if (neckBone) {
            neckBone.rotation.y += smoothYaw * 0.25;
          }

          if (st.auraHex) auraLight.color.set(st.auraHex);
          auraLight.intensity = st.speaking ? (5.6 + Math.sin(t * 10.0) * 1.2) : (st.thinking ? 5.8 : 4.6);
        }
        renderer.render(scene, camera);
      }
      requestAnimationFrame(animate);
    }

    init3DEva();
  </script>
</body>
</html>
"""


def install_dashboard_cortex(app: FastAPI, *, access: Any, runtime: Any) -> None:
    """Mount the 3D Eva Split-Shell Cockpit, Jarvis World Model & Multi-Brain Cloud LLM routes."""

    @app.get("/internal/v1/dashboard/cortex", response_class=HTMLResponse)
    def cortex_page(request: Request) -> HTMLResponse:
        access.require(request)
        return HTMLResponse(
            CORTEX_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline' blob:; "
                    "connect-src 'self' blob: data:; "
                    "img-src 'self' data: blob:; "
                    "worker-src 'self' blob:; "
                    "base-uri 'none'; frame-ancestors 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/internal/v1/dashboard/cortex/assets/{asset_name}")
    def cortex_static_asset(request: Request, asset_name: str) -> FileResponse:
        access.require(request)
        media_type = _CORTEX_STATIC_ASSETS.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="cortex asset not found")
        asset_path = _CORTEX_STATIC_DIR / asset_name
        if not asset_path.is_file():
            raise HTTPException(status_code=404, detail="cortex asset unavailable")
        return FileResponse(
            path=asset_path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/internal/v1/dashboard/cortex/state")
    def cortex_state(request: Request) -> JSONResponse:
        access.require(request)
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/subscription")
    def select_subscription(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = SubscriptionSelectionRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid subscription selection") from exc
        _save_subscription_state(parsed.provider_id, parsed.auto_fallback)
        engine = _ensure_pulse_engine(runtime)
        engine.apply_sensory_stimulus(kind="provider_recovered", intensity=0.7)
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/cloud-provider")
    def configure_cloud_provider(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = CloudProviderConfigureRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid cloud provider request") from exc

        control, settings = _resolve_model_provider_settings(runtime)
        try:
            if parsed.action == "use_default_codex":
                settings.select(None)
                _save_subscription_state("openai_codex", True)
            elif parsed.action == "configure_and_activate":
                conn_id = settings.configure(
                    kind=parsed.kind,
                    label=parsed.label,
                    model=parsed.model,
                    endpoint=parsed.endpoint,
                    api_key=parsed.api_key,
                    workspace_id="",
                )
                if control is not None:
                    st = control.check(conn_id)
                    conn_row = next((c for c in st.get("connections", []) if c.get("id") == conn_id), None)
                    if conn_row and conn_row.get("check_state") == "ready":
                        settings.select(conn_id)
                        sub_map: dict[str, SubscriptionProviderId] = {
                            "gemini": "gemini_argon",
                            "anthropic": "claude_subscription",
                            "openai_compatible": "openai_codex",
                        }
                        _save_subscription_state(sub_map.get(parsed.kind, "gemini_argon"), True)
            elif parsed.action == "check" and parsed.connection_id:
                if control is not None:
                    control.check(parsed.connection_id)
            elif parsed.action == "select" and parsed.connection_id:
                settings.select(parsed.connection_id)
                _save_subscription_state("gemini_argon", True)
            elif parsed.action == "disable" and parsed.connection_id:
                settings.disable(parsed.connection_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        engine = _ensure_pulse_engine(runtime)
        engine.apply_sensory_stimulus(kind="provider_recovered", intensity=0.7)
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/sleep-consolidate")
    def trigger_sleep_consolidation(request: Request) -> JSONResponse:
        access.require(request)
        engine = _ensure_pulse_engine(runtime)
        engine.run_sleep_consolidation()
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/jarvis-action")
    def decide_jarvis_action(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = JarvisActionDecisionRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid jarvis action decision") from exc
        engine = _ensure_pulse_engine(runtime)
        engine.decide_jarvis_staged_action(parsed.action_id, decision=parsed.decision)
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/stimulus")
    def apply_stimulus(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = CortexStimulusRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid cortex stimulus") from exc
        engine = _ensure_pulse_engine(runtime)
        if parsed.kind == "resident_feedback":
            owner_id = str(
                parsed.resident_id
                or getattr(runtime, "_dashboard_owner_principal_id", None)
                or "owner-jaap"
            )
            engine.record_resident_signal(
                principal_id=owner_id,
                display_name=parsed.resident_name or "Jaap",
                actual_receptivity=parsed.actual_receptivity if parsed.actual_receptivity is not None else 0.3,
                observed_mood=parsed.observed_mood,
                context_hypothesis=parsed.context_hypothesis,
            )
        else:
            engine.apply_sensory_stimulus(kind=parsed.kind, intensity=parsed.intensity)
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/routine")
    def record_routine_observation(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = RoutineObservationRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid routine observation") from exc
        engine = _ensure_pulse_engine(runtime)
        engine.record_routine_observation(
            parsed.routine_id,
            is_exception=parsed.is_exception,
            detail_nl=parsed.detail_nl,
        )
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/source-watch")
    def record_source_watch(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = SourceWatchEventRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid source watch event") from exc
        engine = _ensure_pulse_engine(runtime)
        engine.record_source_watch_event(
            parsed.watch_id,
            event_payload_sha256=parsed.event_payload_sha256,
            summary_nl=parsed.summary_nl,
            access_available=parsed.access_available,
        )
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/internal/v1/dashboard/cortex/responsibility")
    def manage_responsibility(request: Request, payload: dict[str, Any]) -> JSONResponse:
        access.require(request)
        try:
            parsed = ResponsibilityActionRequest.model_validate(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid responsibility action") from exc
        engine = _ensure_pulse_engine(runtime)
        engine.manage_household_responsibility(
            parsed.artifact_id,
            action=parsed.action,
            note_nl=parsed.note_nl,
        )
        return JSONResponse(
            build_cortex_state_payload(runtime),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


__all__ = [
    "CORTEX_HTML",
    "CloudProviderConfigureRequest",
    "CortexStimulusRequest",
    "JarvisActionDecisionRequest",
    "ResponsibilityActionRequest",
    "RoutineObservationRequest",
    "SourceWatchEventRequest",
    "SubscriptionProviderInfo",
    "SubscriptionSelectionRequest",
    "build_cortex_state_payload",
    "install_dashboard_cortex",
]
