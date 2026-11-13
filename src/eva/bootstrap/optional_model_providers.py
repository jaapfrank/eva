"""Optional private-text model selection; reuse Eva's context and durable actions."""
from copy import copy
from dataclasses import replace
from pathlib import Path
from time import monotonic
import asyncio
import importlib.util
import os
import threading

from eva.operations.model_provider_settings import MODEL_PROVIDER_PRESETS, ModelProviderSettings
from eva.adapters.model.contracts import (
    ConversationModelDeadlineExceeded,
    ConversationModelError,
    ConversationModelInputBudgetExceeded,
    ConversationModelRateLimited,
    ConversationModelUnavailable,
    ConversationModelUsageError,
    ResearchCitationError,
    ResearchSourceCoverageError,
)
from eva.adapters.model.openai_codex_provider import CodexProviderError
from eva.application.cancellation import WorkInterrupted, check_cancellation
from eva.application.conversation import (
    ConversationClarificationRequired,
    ConversationConflict,
    ConversationExecutionLimitReached,
    ConversationStopped,
    published_conversation_text_count,
)

_NON_FAILOVER_ERRORS = (
    WorkInterrupted,
    ConversationStopped,
    ConversationConflict,
    ConversationExecutionLimitReached,
    ConversationClarificationRequired,
    ConversationModelInputBudgetExceeded,
    ConversationModelUsageError,
    ResearchSourceCoverageError,
    ResearchCitationError,
    asyncio.CancelledError,
    KeyboardInterrupt,
)

_BASELINE_COOLDOWN_KEY = "__baseline_codex__"
_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
_DEFAULT_UNAVAILABLE_COOLDOWN_SECONDS = 30.0


def _extract_retry_after_seconds(error: BaseException, *, default: float) -> float:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending and len(seen) < 6:
        cur = pending.pop(0)
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, CodexProviderError):
            diag = cur.diagnostic
            if isinstance(diag, dict):
                retry_val = diag.get("retry_after_seconds")
                if isinstance(retry_val, (int, float)) and not isinstance(retry_val, bool):
                    return max(5.0, min(300.0, float(retry_val)))
        cause = cur.__cause__ or cur.__context__
        if cause is not None:
            pending.append(cause)
    return default


_FAILOVER_CODEX_CATEGORIES = frozenset({
    "rate_limited",
    "unavailable",
    "deadline",
    "credentials_rejected",
})


def _is_failover_eligible_error(error: BaseException) -> bool:
    """Return True strictly for transient/credential provider failures, never for STOP or deterministic protocol errors."""
    check_cancellation()
    if isinstance(error, _NON_FAILOVER_ERRORS):
        return False
    if isinstance(
        error,
        (
            ConversationModelRateLimited,
            ConversationModelUnavailable,
            ConversationModelDeadlineExceeded,
        ),
    ):
        return True
    if isinstance(error, CodexProviderError):
        return error.category in _FAILOVER_CODEX_CATEGORIES
    if isinstance(error, ConversationModelError):
        cause = error.__cause__
        if isinstance(cause, CodexProviderError):
            return cause.category in _FAILOVER_CODEX_CATEGORIES
        msg = str(error).lower()
        if any(
            marker in msg
            for marker in (
                "pauze",
                "gepauzeerd",
                "attestation",
                "invalid response",
                "mixed an answer",
                "ended without a final answer",
                "streamed text phase",
                "deferred action",
                "archive binding",
                "event loop",
            )
        ):
            return False
        if any(
            marker in msg
            for marker in (
                "credentials were rejected",
                "429",
                "502",
                "503",
                "504",
                "usage_limit",
                "rate limit",
                "rate_limit",
                "unavailable",
                "overloaded",
                "timeout",
                "deadline",
            )
        ):
            return True
        return False
    return False


def _record_provider_causal_event(event_key: str, *, intensity: float = 0.8) -> None:
    try:
        from eva.application.living_entity_pulse import LivingEntityPulseEngine

        engine = LivingEntityPulseEngine.get()
        if hasattr(engine, "record_causal_event"):
            engine.record_causal_event(event_key, intensity=intensity)
    except Exception:
        pass


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

    def _note_provider_failure(self, conn_key: str, error: BaseException) -> None:
        if isinstance(error, ConversationModelRateLimited) or (
            isinstance(error, CodexProviderError) and error.category == "rate_limited"
        ):
            cooldown_s = _extract_retry_after_seconds(
                error, default=_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
            )
            self.control.mark_cooldown(conn_key, seconds=cooldown_s, reason="rate_limited")
            _record_provider_causal_event("provider.http_429_rate_limit", intensity=0.82)
        elif isinstance(
            error, (ConversationModelUnavailable, ConversationModelDeadlineExceeded)
        ) or (
            isinstance(error, CodexProviderError)
            and error.category in {"unavailable", "deadline"}
        ):
            cooldown_s = _extract_retry_after_seconds(
                error, default=_DEFAULT_UNAVAILABLE_COOLDOWN_SECONDS
            )
            self.control.mark_cooldown(conn_key, seconds=cooldown_s, reason="unavailable")
            _record_provider_causal_event("provider.http_503_unavailable", intensity=0.86)

    def _note_failover_recovered(self, conn_key: str) -> None:
        self.control.clear_cooldown(conn_key)
        _record_provider_causal_event("provider.failover_recovered", intensity=0.90)
        try:
            from eva.application.living_entity_pulse import LivingEntityPulseEngine

            LivingEntityPulseEngine.get().apply_sensory_stimulus(
                kind="provider_recovered",
                intensity=0.55,
            )
        except Exception:
            pass

    def _run_configured_connection(self, identifier: str, turn, *args, selected: bool = False, **kwargs):
        from eva.adapters.model.optional_backends import OptionalBackend

        check_cancellation()
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
        self.control.clear_cooldown(identifier)
        return replace(result, provider=row["kind"] + ":" + row["model"])

    def _ready_cloud_failover_ids(
        self,
        state: dict,
        *,
        preferred_kind: str | None = None,
        exclude_id: str | None = None,
    ) -> list[str]:
        preferred_ready: list[str] = []
        others_ready: list[str] = []
        cooling_down_fallback: list[tuple[float, str]] = []
        for conn_id, row in (state.get("connections") or {}).items():
            if conn_id == exclude_id:
                continue
            if not row.get("enabled") or row.get("check_state") != "ready":
                continue
            rem = self.control.cooldown_remaining(conn_id)
            if rem > 0.0:
                cooling_down_fallback.append((rem, conn_id))
                continue
            if preferred_kind and row.get("kind") == preferred_kind:
                preferred_ready.append(conn_id)
            else:
                others_ready.append(conn_id)
        active = preferred_ready + others_ready
        if active:
            return active
        cooling_down_fallback.sort(key=lambda item: item[0])
        return [cid for _, cid in cooling_down_fallback]

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
        if ctx["speaker_role"] in ("gezinshoofd", "gezin"):
            try:
                from eva.application.living_entity_pulse import LivingEntityPulseEngine

                pulse_engine = LivingEntityPulseEngine.get()
                snap = pulse_engine.record_turn(
                    principal_id=turn.principal_id,
                    user_text=turn.text,
                    display_name=ctx["speaker_name"],
                )
                ode = snap.liquid_ode
                ode_note = (
                    f"Continue Liquid-tijdskern (dx/dt): alertheid={ode.state.get('arousal', 0.5):.2f}, "
                    f"sociale afstemming={ode.state.get('social_attunement', 0.6):.2f}, "
                    f"huisrust={ode.state.get('home_proprioception', 0.85):.2f} "
                    f"(τ_eff={ode.mean_tau_seconds}s)."
                )
            except Exception:
                pass

        proprioception_note = ""
        if ctx["speaker_role"] in ("gezinshoofd", "gezin"):
            try:
                from eva.application.home_proprioception import HomeProprioceptionManager

                proprioception_note = HomeProprioceptionManager(
                    self.control.settings.directory
                ).build_household_awareness_note()
            except Exception:
                proprioception_note = ""

        if ctx["speaker_role"] == "gast":
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

        extra_notes = [n for n in (ctx["self_context_note"], ode_note, proprioception_note) if n]
        if extra_notes:
            existing_sc = tuple(kwargs.get("self_context") or ())
            kwargs = {**kwargs, "self_context": (*existing_sc, *extra_notes)}
        return tuple(new_args), kwargs

    def _invoke_with_failover_fence(self, call_fn, kwargs: dict):
        """Run one provider attempt and report whether partial streaming or multi-step side effects occurred."""
        published_before = published_conversation_text_count()
        step_checkpoints = [0]
        orig_before_step = kwargs.get("before_step")
        call_kwargs = kwargs
        if callable(orig_before_step):
            def _tracked_before_step(*b_args, **b_kwargs):
                step_checkpoints[0] += 1
                return orig_before_step(*b_args, **b_kwargs)

            call_kwargs = {**kwargs, "before_step": _tracked_before_step}
        try:
            return call_fn(call_kwargs)
        except BaseException as exc:
            already_streamed = published_conversation_text_count() > published_before
            multi_step_progressed = step_checkpoints[0] > 1
            if already_streamed or multi_step_progressed or not _is_failover_eligible_error(exc):
                raise
            raise _FailoverEligibleAttemptError(exc) from None

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

        auto_fallback = False
        if sub_candidate.exists():
            try:
                sub_data = json.loads(sub_candidate.read_text(encoding="utf-8"))
                auto_fallback = bool(sub_data.get("auto_fallback", False))
            except Exception:
                auto_fallback = False

        # Skip actively cooling-down primary during 429/503 window when a healthy failover target exists
        if auto_fallback and self.control.cooldown_remaining(identifier) > 0.0:
            for backup_id in self._ready_cloud_failover_ids(state, exclude_id=identifier):
                if self.control.cooldown_remaining(backup_id) > 0.0:
                    continue
                try:
                    return self._invoke_with_failover_fence(
                        lambda kw, bid=backup_id: self._run_configured_connection(
                            bid, turn, *args, selected=False, **kw
                        ),
                        kwargs,
                    )
                except _FailoverEligibleAttemptError as wrapped_err:
                    self._note_provider_failure(backup_id, wrapped_err.original)
                    continue
            if (
                self.control.cooldown_remaining(_BASELINE_COOLDOWN_KEY) <= 0.0
                and bool(self.baseline.credential_status().ready)
            ):
                try:
                    return self._invoke_with_failover_fence(
                        lambda kw: self.baseline.generate(turn, *args, **kw),
                        kwargs,
                    )
                except _FailoverEligibleAttemptError as wrapped_base:
                    self._note_provider_failure(_BASELINE_COOLDOWN_KEY, wrapped_base.original)

        try:
            return self._invoke_with_failover_fence(
                lambda kw: self._run_configured_connection(
                    identifier, turn, *args, selected=True, **kw
                ),
                kwargs,
            )
        except _FailoverEligibleAttemptError as wrapped_primary:
            error = wrapped_primary.original
            self._note_provider_failure(identifier, error)
            if not auto_fallback:
                if isinstance(error, ConversationModelError):
                    raise error
                raise ConversationModelError("optional model request failed") from None

            for backup_id in self._ready_cloud_failover_ids(state, exclude_id=identifier):
                try:
                    res = self._invoke_with_failover_fence(
                        lambda kw, bid=backup_id: self._run_configured_connection(
                            bid, turn, *args, selected=False, **kw
                        ),
                        kwargs,
                    )
                    self._note_failover_recovered(backup_id)
                    return res
                except _FailoverEligibleAttemptError as wrapped_backup:
                    self._note_provider_failure(backup_id, wrapped_backup.original)
                    continue
            if bool(self.baseline.credential_status().ready):
                try:
                    res = self._invoke_with_failover_fence(
                        lambda kw: self.baseline.generate(turn, *args, **kw),
                        kwargs,
                    )
                    self._note_failover_recovered(_BASELINE_COOLDOWN_KEY)
                    return res
                except _FailoverEligibleAttemptError as wrapped_base:
                    self._note_provider_failure(_BASELINE_COOLDOWN_KEY, wrapped_base.original)
            if isinstance(error, ConversationModelError):
                raise error
            raise ConversationModelError("all configured model providers failed") from None

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
        last_error: BaseException | None = None
        attempted_ids: set[str] = set()

        if preferred_kind is not None:
            for conn_id in self._ready_cloud_failover_ids(settings_state, preferred_kind=preferred_kind):
                attempted_ids.add(conn_id)
                try:
                    res = self._invoke_with_failover_fence(
                        lambda kw, cid=conn_id: self._run_configured_connection(
                            cid, turn, *args, selected=False, **kw
                        ),
                        kwargs,
                    )
                    if last_error is not None:
                        self._note_failover_recovered(conn_id)
                    return res
                except _FailoverEligibleAttemptError as wrapped_exc:
                    exc = wrapped_exc.original
                    self._note_provider_failure(conn_id, exc)
                    last_error = exc
                    if not auto_fallback:
                        if isinstance(exc, ConversationModelError):
                            raise exc
                        raise ConversationModelError("preferred cloud provider failed") from None
                    continue

        if not (auto_fallback and self.control.cooldown_remaining(_BASELINE_COOLDOWN_KEY) > 0.0 and self._ready_cloud_failover_ids(settings_state)):
            try:
                res = self._invoke_with_failover_fence(
                    lambda kw: self.baseline.generate(turn, *args, **kw),
                    kwargs,
                )
                self.control.clear_cooldown(_BASELINE_COOLDOWN_KEY)
                if last_error is not None:
                    self._note_failover_recovered(_BASELINE_COOLDOWN_KEY)
                return res
            except _FailoverEligibleAttemptError as wrapped_exc:
                exc = wrapped_exc.original
                self._note_provider_failure(_BASELINE_COOLDOWN_KEY, exc)
                last_error = exc
                if not auto_fallback:
                    if isinstance(exc, ConversationModelError):
                        raise exc
                    raise ConversationModelError("primary conversation model failed") from None

        for conn_id in self._ready_cloud_failover_ids(settings_state, preferred_kind=preferred_kind):
            if conn_id in attempted_ids:
                continue
            try:
                res = self._invoke_with_failover_fence(
                    lambda kw, cid=conn_id: self._run_configured_connection(
                        cid, turn, *args, selected=False, **kw
                    ),
                    kwargs,
                )
                if last_error is not None:
                    self._note_failover_recovered(conn_id)
                return res
            except _FailoverEligibleAttemptError as wrapped_exc:
                exc = wrapped_exc.original
                self._note_provider_failure(conn_id, exc)
                last_error = exc
                continue

        if isinstance(last_error, ConversationModelError):
            raise last_error
        raise ConversationModelError("no verified conversation model provider succeeded") from None


class _FailoverEligibleAttemptError(Exception):
    def __init__(self, original: BaseException) -> None:
        self.original = original
        super().__init__(type(original).__name__)


class OptionalModelProviders:
    def __init__(self, baseline, directory, *, transport=None):
        self.baseline = baseline
        self.settings = ModelProviderSettings(Path(directory))
        self.transport = transport
        self._cooldown_lock = threading.Lock()
        self._cooldowns: dict[str, tuple[float, str]] = {}

    def mark_cooldown(self, identifier: str, *, seconds: float, reason: str = "unavailable") -> None:
        duration = max(1.0, min(300.0, float(seconds)))
        now_m = monotonic()
        until = now_m + duration
        with self._cooldown_lock:
            if len(self._cooldowns) >= 64 and identifier not in self._cooldowns:
                expired = [k for k, (exp, _) in self._cooldowns.items() if exp <= now_m]
                for k in expired:
                    self._cooldowns.pop(k, None)
                if len(self._cooldowns) >= 64:
                    oldest_key = min(self._cooldowns.items(), key=lambda kv: kv[1][0])[0]
                    self._cooldowns.pop(oldest_key, None)
            existing = self._cooldowns.get(identifier)
            if existing is None or until > existing[0]:
                self._cooldowns[identifier] = (until, reason)

    def clear_cooldown(self, identifier: str) -> None:
        with self._cooldown_lock:
            self._cooldowns.pop(identifier, None)

    def cooldown_remaining(self, identifier: str) -> float:
        now_m = monotonic()
        with self._cooldown_lock:
            entry = self._cooldowns.get(identifier)
            if entry is None:
                return 0.0
            rem = entry[0] - now_m
            if rem <= 0.0:
                self._cooldowns.pop(identifier, None)
                return 0.0
            return round(rem, 2)

    def status(self):
        state = self.settings.read()
        default = self.baseline.credential_status()
        base_cd = self.cooldown_remaining(_BASELINE_COOLDOWN_KEY)
        return {
            "contract": "EvaModelProviders/v1",
            "scope": "private_text_conversations",
            "selected_id": state["selected_id"],
            "default": {
                "label": "Bestaande ChatGPT-verbinding",
                "ready": default.ready,
                "state": default.state,
                "cooldown_remaining_seconds": base_cd,
            },
            "connections": [
                {k: v for k, v in row.items() if k != "workspace_id"}
                | {
                    "selected": identifier == state["selected_id"],
                    "cooldown_remaining_seconds": self.cooldown_remaining(identifier),
                }
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
        if ready:
            self.clear_cooldown(identifier)
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
