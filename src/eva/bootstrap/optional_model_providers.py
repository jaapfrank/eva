"""Optional private-text model selection; reuse Eva's context and durable actions."""
from copy import copy
from dataclasses import replace
from pathlib import Path
import asyncio
import importlib.util
import os

from eva.operations.model_provider_settings import MODEL_PROVIDER_PRESETS, ModelProviderSettings
from eva.adapters.model.contracts import ConversationModelError


class OptionalConversationModel:
    def __init__(self, baseline, control):
        self.baseline, self.control = baseline, control

    def __getattr__(self, name):
        # Research, initiative, development and status retain the configured
        # subscription route. Only the explicitly offered private text scope is
        # selectable here; this is not a second agent or tool executor.
        return getattr(self.baseline, name)

    def with_context_archive(self, archive):
        return OptionalConversationModel(self.baseline.with_context_archive(archive), self.control)

    def _run_configured_connection(self, identifier: str, turn, *args, selected: bool = False, **kwargs):
        from eva.adapters.model.optional_backends import OptionalBackend

        row, _ = self.control.settings.connection(identifier, selected=selected)
        configured_model = copy(self.baseline)
        configured_model._model = row["model"]
        configured_model._model_backend = OptionalBackend(
            self.control.settings,
            identifier,
            selected=selected,
            max_tokens=(
                configured_model._resource_budget.max_output_tokens
                if configured_model._resource_budget
                else 2048
            ),
            transport=self.control.transport,
        )
        result = configured_model.generate(turn, *args, **kwargs)
        return replace(result, provider=row["kind"] + ":" + row["model"])

    def _ready_cloud_failover_ids(
        self,
        state: dict,
        *,
        preferred_kind: str | None = None,
        exclude_id: str | None = None,
    ) -> list[str]:
        preferred: list[str] = []
        others: list[str] = []
        for conn_id, row in (state.get("connections") or {}).items():
            if conn_id == exclude_id:
                continue
            if not row.get("enabled") or row.get("check_state") != "ready":
                continue
            if preferred_kind and row.get("kind") == preferred_kind:
                preferred.append(conn_id)
            else:
                others.append(conn_id)
        return preferred + others

    def _prepare_household_and_pulse(self, turn, args, kwargs):
        from eva.application.household_head_settings import HouseholdHeadSettings

        hh = HouseholdHeadSettings(self.control.settings.directory)
        ctx = hh.build_turn_context(
            principal_id=turn.principal_id,
            conversation_id=turn.conversation_id,
        )
        if not ctx["allowed"]:
            raise ConversationModelError(
                "Deze koppeling is momenteel gepauzeerd door het gezinshoofd."
            )

        ode_note = ""
        try:
            from eva.application.living_entity_pulse import LivingEntityPulseEngine

            pulse_engine = LivingEntityPulseEngine.get()
            if ctx["speaker_role"] in ("gezinshoofd", "gezin"):
                snap = pulse_engine.record_turn(
                    principal_id=turn.principal_id,
                    user_text=turn.text,
                    display_name=ctx["speaker_name"],
                )
            else:
                snap = pulse_engine.snapshot()
            ode = snap.liquid_ode
            ode_note = (
                f"Continue Liquid-tijdskern (dx/dt): alertheid={ode.state.get('arousal', 0.5):.2f}, "
                f"sociale afstemming={ode.state.get('social_attunement', 0.6):.2f}, "
                f"huisrust={ode.state.get('home_proprioception', 0.85):.2f} "
                f"(τ_eff={ode.mean_tau_seconds}s)."
            )
        except Exception:
            pass

        if hh.state_file.is_file() and ctx["speaker_role"] == "gast":
            avail = kwargs.get("available_actions")
            if avail is not None:
                safe_guest_actions = tuple(a for a in avail if a == "memory_search")
                kwargs = {**kwargs, "available_actions": safe_guest_actions}

        if not args:
            return args, kwargs

        new_args = list(args)
        ident = new_args[0]
        if hasattr(ident, "name") and hasattr(ident, "description") and hh.state_file.is_file():
            try:
                new_args[0] = replace(
                    ident,
                    name=ctx["eva_display_name"],
                    description=ctx["eva_persona_summary"],
                )
            except Exception:
                pass

        extra_notes = [n for n in (ctx["self_context_note"], ode_note) if n]
        if extra_notes:
            existing_sc = tuple(kwargs.get("self_context") or ())
            kwargs = {**kwargs, "self_context": (*existing_sc, *extra_notes)}
        return tuple(new_args), kwargs

    def generate(self, turn, *args, **kwargs):
        import json
        from eva.application.voice_context import current_voice_context

        if (
            current_voice_context() is not None
            or turn.audience.kind != "private_person"
            or not turn.conversation_id.startswith(("dashboard:", "telegram:", "whatsapp:"))
        ):
            return self.baseline.generate(turn, *args, **kwargs)

        args, kwargs = self._prepare_household_and_pulse(turn, args, kwargs)
        state = self.control.settings.read()
        identifier = state["selected_id"]
        sub_candidate = self.control.settings.directory / "subscription-llm-state.json"

        if identifier is None:
            if sub_candidate.exists():
                return self._generate_with_subscription_state(sub_candidate, turn, *args, state=state, **kwargs)
            return self.baseline.generate(turn, *args, **kwargs)

        try:
            return self._run_configured_connection(identifier, turn, *args, selected=True, **kwargs)
        except Exception as error:
            auto_fallback = False
            if sub_candidate.exists():
                try:
                    sub_data = json.loads(sub_candidate.read_text(encoding="utf-8"))
                    auto_fallback = bool(sub_data.get("auto_fallback", False))
                except Exception:
                    auto_fallback = False
            if not auto_fallback:
                if isinstance(error, ConversationModelError):
                    raise
                raise ConversationModelError("optional model request failed") from error

            for backup_id in self._ready_cloud_failover_ids(state, exclude_id=identifier):
                try:
                    return self._run_configured_connection(backup_id, turn, *args, selected=False, **kwargs)
                except Exception:
                    continue
            try:
                if bool(self.baseline.credential_status().ready):
                    return self.baseline.generate(turn, *args, **kwargs)
            except Exception:
                pass
            if isinstance(error, ConversationModelError):
                raise
            raise ConversationModelError("all configured model providers failed") from error

    def _generate_with_subscription_state(self, sub_file: Path, turn, *args, state: dict | None = None, **kwargs):
        import json

        try:
            sub_state = json.loads(sub_file.read_text(encoding="utf-8"))
        except Exception:
            return self.baseline.generate(turn, *args, **kwargs)
        selected_provider = sub_state.get("selected_provider", "openai_codex")
        auto_fallback = bool(sub_state.get("auto_fallback", True))
        settings_state = state if state is not None else self.control.settings.read()

        preferred_kind = (
            "gemini"
            if selected_provider == "gemini_argon"
            else ("anthropic" if selected_provider == "claude_subscription" else None)
        )
        last_error: Exception | None = None

        if preferred_kind is not None:
            for conn_id in self._ready_cloud_failover_ids(settings_state, preferred_kind=preferred_kind):
                try:
                    return self._run_configured_connection(conn_id, turn, *args, selected=False, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if not auto_fallback:
                        if isinstance(exc, ConversationModelError):
                            raise
                        raise ConversationModelError("preferred cloud provider failed") from exc
                    continue

        try:
            return self.baseline.generate(turn, *args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not auto_fallback:
                if isinstance(exc, ConversationModelError):
                    raise
                raise ConversationModelError("primary conversation model failed") from exc

        for conn_id in self._ready_cloud_failover_ids(settings_state, preferred_kind=preferred_kind):
            try:
                return self._run_configured_connection(conn_id, turn, *args, selected=False, **kwargs)
            except Exception as exc:
                last_error = exc
                continue

        if isinstance(last_error, ConversationModelError):
            raise last_error
        raise ConversationModelError("no verified conversation model provider succeeded") from last_error


class OptionalModelProviders:
    def __init__(self, baseline, directory, *, transport=None):
        self.baseline = baseline
        self.settings = ModelProviderSettings(Path(directory))
        self.transport = transport

    def status(self):
        state = self.settings.read()
        default = self.baseline.credential_status()
        return {
            "contract": "EvaModelProviders/v1",
            "scope": "private_text_conversations",
            "selected_id": state["selected_id"],
            "default": {
                "label": "Bestaande ChatGPT-verbinding",
                "ready": default.ready,
                "state": default.state,
            },
            "connections": [
                {k: v for k, v in row.items() if k != "workspace_id"}
                | {"selected": identifier == state["selected_id"]}
                for identifier, row in state["connections"].items()
            ],
            "adapters": [
                {
                    "kind": kind,
                    "label": label,
                    "installed": importlib.util.find_spec(module) is not None,
                }
                for kind, label, module in [
                    ("gemini", "Google Gemini (Cloud API)", "openai"),
                    ("anthropic", "Claude / Anthropic", "anthropic"),
                    ("openai_compatible", "OpenAI-compatibele API (12+ Cloud & Lokaal)", "openai"),
                ]
            ],
            "presets": [dict(p) for p in MODEL_PROVIDER_PRESETS],
            "meaning": (
                "Kies uit 15 LLM-providers (ChatGPT OAuth, Gemini, Claude, OpenAI API, OpenRouter, Grok, "
                "DeepSeek, Mistral, Groq, Cerebras, Together, Fireworks, Perplexity, Ollama of LM Studio). "
                "Geen lokale GPU vereist; lokale servers zijn optioneel zonder API-sleutel."
            ),
        }

    def check(self, identifier, *, admission_check=None):
        from typing import Literal
        from pydantic import BaseModel
        from pydantic_ai import Agent, ToolOutput
        from eva.adapters.model.optional_backends import OptionalBackend

        class Confirmation(BaseModel):
            echoed: Literal["eva-provider-check"]

        usage = None
        try:
            backend = OptionalBackend(
                self.settings,
                identifier,
                transport=self.transport,
                max_tokens=256,
                admission_check=admission_check,
            )

            async def run():
                agent = Agent(backend, output_type=ToolOutput(Confirmation), retries=0)
                return await agent.run(
                    "Return echoed=eva-provider-check using the provided output tool. No other work."
                )

            result = asyncio.run(run())
            reported = result.usage
            usage = {
                "model_attempts": reported.requests,
                "input_tokens": reported.input_tokens,
                "output_tokens": reported.output_tokens,
            }
            ready = isinstance(result.output, Confirmation) and reported.requests == 1
        except Exception:
            ready = False  # Provider exception bodies may contain credentials or prompts.
        if admission_check is not None:
            admission_check()
        self.settings.record_check(identifier, ready=ready, usage=usage)
        return self.status()


def attach_optional_models(runtime):
    existing = getattr(runtime, "_model_provider_control", None)
    if existing is not None:
        return existing
    conversation = getattr(runtime, "_conversation", None)
    if conversation is None or getattr(runtime, "_dashboard_owner_principal_id", None) is None:
        return None
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel

    baseline = getattr(conversation, "_model", None)
    if not isinstance(baseline, PydanticConversationModel):
        return None
    control = OptionalModelProviders(
        baseline,
        os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers"),
    )
    conversation._model = OptionalConversationModel(baseline, control)
    if getattr(conversation, "_contact_model", None) is not None:
        conversation._contact_model = OptionalConversationModel(conversation._contact_model, control)
    runtime._model_provider_control = control
    return control
