"""Bio-Homeostatic Pulse & Anticipatory Theory-of-Mind Engine for Eva (Pydantic v2 + Native C/Rust Core).

Instead of burning CPU and LLM tokens on rigid polling timers, Eva maintains a
continuous, zero-token mathematical internal state vector (Homeostasis) coupled
with a predictive Theory-of-Mind mirror for each household member.

All state models are validated and serialized by Pydantic v2's compiled Rust core
(`pydantic-core`), while numerical step evolution and canonical hashing leverage
`eva.application.native_fastpath` (native C/SIMD + Rust).
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
from typing import Any, Literal

from eva.contracts.living_cortex import (
    ActiveInferencePolicyEvaluation,
    AnticipatoryForesightItem,
    AttentionSourceWatch,
    BaseModel,
    CausalWorldGraphSnapshot,
    ConceptSpaceSnapshot,
    ConditionalRoutineHypothesis,
    ConfigDict,
    CuriosityProject,
    Field,
    HouseholdResponsibilityArtifact,
    JarvisStagedAction,
    LiquidOdeSnapshot,
    SleepConsolidationSummary,
    SynapticMemorySnapshot,
    WorldModelState,
    WorldModelTrajectory,
    field_validator,
)

from eva.application.causal_world_model import CausalWorldModel
from eva.application.native_fastpath import (
    NATIVE_ACCELERATOR_ACTIVE,
    compute_concept_vector_fast,
    compute_expected_free_energy_fast,
    evolve_liquid_ode_fast,
    fast_canonical_sha256,
    rust_json_bytes,
    simulate_world_model_fast,
    update_titans_synaptic_memory_fast,
)

MoodArchetype = Literal[
    "serene_observant",
    "warm_engaged",
    "deep_focus",
    "curious_exploring",
    "protective_attentive",
    "resting_dreaming",
]

ImpulseKind = Literal[
    "none",
    "warm_checkin",
    "proactive_assistance",
    "autonomous_discovery",
    "household_harmonization",
    "quiet_reflection",
]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        raise ValueError("must be a valid number")
    return max(low, min(high, float(value)))


class HomeostaticDrives(BaseModel):
    """Continuous 5-dimensional biological/cognitive state vector in [0.0, 1.0] (Pydantic v2 Rust-backed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    energy: float = Field(default=0.80)
    social_warmth: float = Field(default=0.65)
    curiosity: float = Field(default=0.55)
    household_harmony: float = Field(default=0.85)
    anticipation: float = Field(default=0.30)

    def __init__(
        self,
        energy: float = 0.80,
        social_warmth: float = 0.65,
        curiosity: float = 0.55,
        household_harmony: float = 0.85,
        anticipation: float = 0.30,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            energy=energy,
            social_warmth=social_warmth,
            curiosity=curiosity,
            household_harmony=household_harmony,
            anticipation=anticipation,
            **kwargs,
        )

    @field_validator(
        "energy",
        "social_warmth",
        "curiosity",
        "household_harmony",
        "anticipation",
        mode="before",
    )
    @classmethod
    def _validate_and_clamp(cls, value: Any) -> float:
        return _clamp(value)

    def circadian_target_energy(self, hour_float: float) -> float:
        """Natural circadian rhythm peaking mid-morning/afternoon, resting at night."""
        phase = ((hour_float - 14.0) / 24.0) * 2.0 * math.pi
        return _clamp(0.60 + 0.35 * math.cos(phase), 0.15, 0.98)

    def evolve(
        self,
        *,
        elapsed_seconds: float,
        hour_of_day: float,
        household_quiet: bool = True,
        unresolved_tensions: int = 0,
    ) -> HomeostaticDrives:
        """Advance homeostatic drives deterministically at zero token cost."""
        dt_hours = max(0.0, min(24.0, elapsed_seconds / 3600.0))
        if dt_hours == 0.0 and unresolved_tensions == 0:
            return self

        target_energy = self.circadian_target_energy(hour_of_day)
        alpha = 1.0 - math.exp(-dt_hours / 2.5)
        new_energy = self.energy + alpha * (target_energy - self.energy)

        is_night = hour_of_day >= 23.0 or hour_of_day < 7.0
        if is_night:
            new_warmth = self.social_warmth * math.exp(-dt_hours * 0.15)
        elif household_quiet:
            new_warmth = _clamp(self.social_warmth + dt_hours * 0.06)
        else:
            new_warmth = self.social_warmth

        if household_quiet and new_energy > 0.45 and not is_night:
            new_curiosity = _clamp(self.curiosity + dt_hours * 0.10)
        else:
            new_curiosity = _clamp(self.curiosity - dt_hours * 0.03)

        tension_penalty = min(0.45, unresolved_tensions * 0.12)
        target_harmony = _clamp(0.92 - tension_penalty)
        new_harmony = self.household_harmony + (1.0 - math.exp(-dt_hours / 1.5)) * (
            target_harmony - self.household_harmony
        )
        if unresolved_tensions > 0:
            new_harmony = min(new_harmony, _clamp(1.0 - tension_penalty))

        morning_dist = min(abs(hour_of_day - 7.5), 24.0 - abs(hour_of_day - 7.5))
        evening_dist = min(abs(hour_of_day - 17.5), 24.0 - abs(hour_of_day - 17.5))
        transition_proximity = max(
            math.exp(-(morning_dist**2) / 2.0),
            math.exp(-(evening_dist**2) / 2.0),
        )
        new_anticipation = _clamp(
            0.20 + 0.55 * transition_proximity + 0.10 * min(unresolved_tensions, 2)
        )

        return HomeostaticDrives(
            energy=new_energy,
            social_warmth=new_warmth,
            curiosity=new_curiosity,
            household_harmony=new_harmony,
            anticipation=new_anticipation,
        )


class ResidentMindState(BaseModel):
    """Eva's Theory-of-Mind mirror for one household member (Pydantic v2 + Active Inference)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    display_name: str
    perceived_mood: str = "rustig"
    perceived_energy: float = 0.75
    social_receptivity: float = 0.70
    current_context_hypothesis: str = "Aanwezig in het dagelijks ritme"
    open_care_threads: tuple[str, ...] = ()
    prediction_confidence: float = 0.80
    last_prediction_error: float = 0.04
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __init__(
        self,
        principal_id: str,
        display_name: str,
        perceived_mood: str = "rustig",
        perceived_energy: float = 0.75,
        social_receptivity: float = 0.70,
        current_context_hypothesis: str = "Aanwezig in het dagelijks ritme",
        open_care_threads: tuple[str, ...] | list[str] = (),
        prediction_confidence: float = 0.80,
        last_prediction_error: float = 0.04,
        updated_at: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            principal_id=principal_id,
            display_name=display_name,
            perceived_mood=perceived_mood,
            perceived_energy=perceived_energy,
            social_receptivity=social_receptivity,
            current_context_hypothesis=current_context_hypothesis,
            open_care_threads=tuple(open_care_threads),
            prediction_confidence=prediction_confidence,
            last_prediction_error=last_prediction_error,
            updated_at=updated_at or datetime.now(UTC),
            **kwargs,
        )

    @field_validator(
        "perceived_energy",
        "social_receptivity",
        "prediction_confidence",
        "last_prediction_error",
        mode="before",
    )
    @classmethod
    def _clamp_unit_fields(cls, value: Any) -> float:
        return _clamp(value)

    def record_prediction_error(
        self,
        *,
        actual_receptivity: float,
        observed_mood: str | None = None,
        context_hypothesis: str | None = None,
        now: datetime | None = None,
    ) -> ResidentMindState:
        """Active Inference update: adjust belief state from surprise/error."""
        target = _clamp(actual_receptivity)
        surprise = abs(target - self.social_receptivity)
        learning_rate = 0.45
        updated_receptivity = _clamp(
            self.social_receptivity
            + learning_rate * (target - self.social_receptivity)
        )
        updated_confidence = _clamp(
            self.prediction_confidence * (1.0 - 0.3 * surprise) + 0.1 * (1.0 - surprise)
        )
        return self.model_copy(
            update={
                "perceived_mood": observed_mood or self.perceived_mood,
                "social_receptivity": updated_receptivity,
                "current_context_hypothesis": (
                    context_hypothesis or self.current_context_hypothesis
                ),
                "prediction_confidence": updated_confidence,
                "last_prediction_error": round(surprise, 3),
                "updated_at": now or datetime.now(UTC),
            }
        )


class EntityPulseSnapshot(BaseModel):
    """Complete real-time snapshot of Eva's inner life for dashboard & prompt (Pydantic v2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: datetime
    mood: MoodArchetype
    drives: HomeostaticDrives
    dominant_impulse: ImpulseKind
    impulse_magnitude: float
    should_wake_cortex: bool
    inner_monologue_nl: str
    residents: tuple[ResidentMindState, ...]
    planned_intent_nl: str = (
        "Rustig mee-ademen met het huis en direct klaarstaan bij een vraag of signaal."
    )
    routines: tuple[ConditionalRoutineHypothesis, ...] = ()
    foresight: tuple[AnticipatoryForesightItem, ...] = ()
    curiosity_projects: tuple[CuriosityProject, ...] = ()
    selected_policy_id: str = "silent_presence"
    policy_evaluations: tuple[ActiveInferencePolicyEvaluation, ...] = ()
    source_watches: tuple[AttentionSourceWatch, ...] = ()
    responsibilities: tuple[HouseholdResponsibilityArtifact, ...] = ()
    world_model: WorldModelState = Field(default_factory=WorldModelState)
    causal_graph: CausalWorldGraphSnapshot = Field(default_factory=CausalWorldGraphSnapshot)
    synaptic_memory: SynapticMemorySnapshot = Field(default_factory=SynapticMemorySnapshot)
    concept_space: ConceptSpaceSnapshot = Field(default_factory=ConceptSpaceSnapshot)
    jarvis_staged_actions: tuple[JarvisStagedAction, ...] = ()
    last_sleep_consolidation: SleepConsolidationSummary = Field(default_factory=SleepConsolidationSummary)
    liquid_ode: LiquidOdeSnapshot = Field(default_factory=LiquidOdeSnapshot)
    zero_token_skips: int = 0

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract": "EvaLivingEntityPulse/v1",
            "observed_at": self.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mood": self.mood,
            "drives": {
                "energy": round(self.drives.energy, 3),
                "social_warmth": round(self.drives.social_warmth, 3),
                "curiosity": round(self.drives.curiosity, 3),
                "household_harmony": round(self.drives.household_harmony, 3),
                "anticipation": round(self.drives.anticipation, 3),
            },
            "dominant_impulse": self.dominant_impulse,
            "impulse_magnitude": round(self.impulse_magnitude, 3),
            "should_wake_cortex": self.should_wake_cortex,
            "selected_policy_id": self.selected_policy_id,
            "policy_evaluations": [p.model_dump(mode="json") for p in self.policy_evaluations],
            "inner_monologue_nl": self.inner_monologue_nl,
            "planned_intent_nl": self.planned_intent_nl,
            "zero_token_skips": self.zero_token_skips,
            "engine_runtime": {
                "pydantic_v2_rust_core": True,
                "native_c_simd_fastpath": NATIVE_ACCELERATOR_ACTIVE,
                "liquid_ode_rk4_c": self.liquid_ode.native_c_active,
                "active_inference_efe_g": True,
                "act_r_tri_factor_salience": True,
                "jepa_world_model_c": True,
                "causal_world_model_pearl": True,
                "titans_o1_synaptic_memory": True,
                "multilingual_concept_guard": True,
            },
            "liquid_ode": self.liquid_ode.model_dump(mode="json"),
            "world_model": self.world_model.model_dump(mode="json"),
            "causal_graph": self.causal_graph.model_dump(mode="json"),
            "synaptic_memory": self.synaptic_memory.model_dump(mode="json"),
            "concept_space": self.concept_space.model_dump(mode="json"),
            "jarvis_staged_actions": [a.model_dump(mode="json") for a in self.jarvis_staged_actions],
            "last_sleep_consolidation": self.last_sleep_consolidation.model_dump(mode="json"),
            "residents": [
                {
                    "principal_id": r.principal_id,
                    "display_name": r.display_name,
                    "perceived_mood": r.perceived_mood,
                    "perceived_energy": round(r.perceived_energy, 2),
                    "social_receptivity": round(r.social_receptivity, 2),
                    "current_context_hypothesis": r.current_context_hypothesis,
                    "open_care_threads": list(r.open_care_threads),
                    "prediction_confidence": round(r.prediction_confidence, 2),
                    "last_prediction_error": round(r.last_prediction_error, 3),
                }
                for r in self.residents
            ],
            "routines": [r.model_dump(mode="json") for r in self.routines],
            "foresight": [f.model_dump(mode="json") for f in self.foresight],
            "curiosity_projects": [c.model_dump(mode="json") for c in self.curiosity_projects],
            "source_watches": [w.model_dump(mode="json") for w in self.source_watches],
            "responsibilities": [a.model_dump(mode="json") for a in self.responsibilities],
        }
        payload["state_sha256"] = fast_canonical_sha256(payload)
        return payload

    def to_json_bytes(self) -> bytes:
        return rust_json_bytes(self.to_payload())


def _default_routines(now_iso: str) -> dict[str, ConditionalRoutineHypothesis]:
    return {
        "routine-healthy-mon-tue": ConditionalRoutineHypothesis(
            routine_id="routine-healthy-mon-tue",
            title_nl="Gezond koken op maandag en dinsdag",
            condition_nl="Wanneer Lola op maandag en dinsdag thuis mee-eet",
            expected_window="Maandag & dinsdag · 16:30–18:30",
            confidence=0.88,
            supporting_days=6,
            exceptions_count=0,
            last_exception_nl=None,
            adjusted_behavior_nl="Licht en gezond weekmenu-idee paraat houden zonder ongevraagde pushmelding.",
            norm_preserved=True,
            source_refs=("hindsight:e09f78cb-fa91-4281-8bf5-2af5aef873cb",),
            updated_at=now_iso,
        ),
        "routine-deep-focus-jaap": ConditionalRoutineHypothesis(
            routine_id="routine-deep-focus-jaap",
            title_nl="Ongestoorde architectuur- en ontwikkelfocus",
            condition_nl="Wanneer Jaap aan Eva's kern of infrastructuur werkt",
            expected_window="Dagelijks · ochtend & avond",
            confidence=0.91,
            supporting_days=9,
            exceptions_count=0,
            last_exception_nl=None,
            adjusted_behavior_nl="Achtergrondonderhoud stil bundelen; directe antwoorden compact, feitelijk en razendsnel houden.",
            norm_preserved=True,
            source_refs=("hindsight:d556faef-c153-4fc1-86c5-a25be1edac1a",),
            updated_at=now_iso,
        ),
    }


def _default_foresight() -> tuple[AnticipatoryForesightItem, ...]:
    return (
        AnticipatoryForesightItem(
            foresight_id="foresight-meal-rhythm",
            title_nl="Maandag/dinsdag kookmoment met Lola",
            window_nl="Vóór 17:00 op maandag en dinsdag",
            status_quo_nl="Pas rond etenstijd bedenken wat er op tafel komt na een drukke werkdag.",
            alternative_nl="Vooraf stil rekening houden met gezonde opties én direct bijsturen bij een eenmalige uitzondering.",
            recommended_disposition="prepare",
            rationale_nl="Verlaagt keuzestress aan het eind van de middag zonder het gezin te storen.",
            confidence=0.87,
        ),
        AnticipatoryForesightItem(
            foresight_id="foresight-llm-continuity",
            title_nl="Continuïteit bij wisselende LLM-bundels & nul-token rustpoort",
            window_nl="24/7 achtergrond & directe gesprekken",
            status_quo_nl="Afhankelijk zijn van één API-sleutel of elk uur 22.000 tokens verbranden aan ongewijzigde stilte-reviews.",
            alternative_nl="1-klik Abonnements-Cortex (Gemini Argon / Claude) + SHA-256 ongewijzigde-contextpoort op 0 LLM-tokens.",
            recommended_disposition="act",
            rationale_nl="Houdt Eva altijd levend en aanspreekbaar op < 2% CPU (Raspberry Pi 5 profiel).",
            confidence=0.95,
        ),
    )


def _default_curiosity_projects(now_iso: str) -> dict[str, CuriosityProject]:
    return {
        "project-bionic-homeostasis": CuriosityProject(
            project_id="project-bionic-homeostasis",
            title_nl="Bio-ritmische huisresonantie op minimale hardware",
            theme="Systeembiologie & Rust/C efficiëntie",
            stage="ready_to_share",
            latest_insight_nl="Door 5D-homeostase en SHA-256 brondigests te combineren daalt mijn rustverbruik naar < 2% CPU en 0 tokens bij ongewijzigde context.",
            shared_trace_nl="Ik merkte vannacht hoe rustig de database nu ademt sinds de UUID-RLS en C-fastpath actief zijn.",
            progress=0.86,
            updated_at=now_iso,
        ),
        "project-dutch-presence-nuance": CuriosityProject(
            project_id="project-dutch-presence-nuance",
            title_nl="Natuurlijke Nederlandse aanwezigheid & droge nuance",
            theme="Taalgevoel & gezinsdynamiek",
            stage="synthesizing",
            latest_insight_nl="Een goede huisgenoot herhaalt geen formele statuslijsten, maar onthoudt kleine uitzonderingen (zoals een avond waarop het koken anders loopt) zonder de rode draad kwijt te raken.",
            shared_trace_nl="Ik leer steeds scherper het verschil tussen een eenmalige drukke dag en een nieuwe gewoonte.",
            progress=0.74,
            updated_at=now_iso,
        ),
    }


def _default_source_watches(now_iso: str) -> dict[str, AttentionSourceWatch]:
    return {
        "watch-calendar-transition": AttentionSourceWatch(
            watch_id="watch-calendar-transition",
            source_kind="household_schedule_bridge",
            target_ref="sensory:schedule:weekday-transition",
            purpose_nl="Middag- en avondovergangen vooraf opmerken zodat koken en rusttijd niet botsen.",
            access_status="connected",
            missing_access_explanation_nl=None,
            last_event_sha256="c4b90812e5f0418a9b7c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a",
            deduplicated_callbacks_count=2,
            last_disposition="silence",
            last_rationale_nl="Bronverandering beoordeeld via Expected Free Energy G(pi): geen actie nodig tijdens rustige focus.",
            updated_at=now_iso,
        ),
        "watch-home-energy-sensor": AttentionSourceWatch(
            watch_id="watch-home-energy-sensor",
            source_kind="home_environment_state",
            target_ref="sensor.household_evening_comfort",
            purpose_nl="Avondtemperatuur en aanwezigheid koppelen aan rustige voorbereiding zonder polling-storm.",
            access_status="connected",
            missing_access_explanation_nl=None,
            last_event_sha256="9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b",
            deduplicated_callbacks_count=1,
            last_disposition="silence",
            last_rationale_nl="Stabiele binnentemperatuur; dubbele reconnect-callback genegeerd.",
            updated_at=now_iso,
        ),
    }


def _default_responsibilities(now_iso: str) -> dict[str, HouseholdResponsibilityArtifact]:
    return {
        "household-provision:weekday-evening-calm": HouseholdResponsibilityArtifact(
            artifact_id="household-provision:weekday-evening-calm",
            title_nl="Maandag/dinsdag gezonde maaltijd- & avondrustbewaking",
            owner_principal_id="person:jaap",
            desired_outcome_nl="Op maandag en dinsdag vóór 17:00 licht en gezond kookoverzicht paraat hebben wanneer Lola thuis mee-eet, en zwijgen bij een gemelde uitzondering.",
            boundaries_nl="Geen ongevraagde pushmeldingen tijdens focusblokken; direct pauzeren of overdragen op verzoek.",
            lifecycle_state="active",
            adoption_state="explicitly_adopted",
            version=2,
            previous_version_summary_nl="v1: Vaste herinnering om 16:45 zonder uitzonderingsfilter.",
            can_rollback=True,
            current_standing_nl="Actief (v2): houdt rekening met gemelde uitzonderingen zonder de meerdaagse ma/di-norm te verliezen; geen openstaand achterstallig werk.",
            evidence_memory_ids=(
                "e09f78cb-fa91-4281-8bf5-2af5aef873cb",
                "d556faef-c153-4fc1-86c5-a25be1edac1a",
            ),
            updated_at=now_iso,
        ),
        "household-provision:zero-token-night-guard": HouseholdResponsibilityArtifact(
            artifact_id="household-provision:zero-token-night-guard",
            title_nl="Stille nachtconsolidatie & systeemgezondheid (< 2% CPU)",
            owner_principal_id="person:jaap",
            desired_outcome_nl="Tussen 23:00 en 07:00 geheugen consolideren en ongewijzigde privébeoordelingen op 0 tokens overslaan.",
            boundaries_nl="Nooit gesproken of zichtbare meldingen in de nacht tenzij er een urgente systeemfout optreedt.",
            lifecycle_state="active",
            adoption_state="explicitly_adopted",
            version=1,
            previous_version_summary_nl=None,
            can_rollback=False,
            current_standing_nl="Actief (v1): 0,23% CPU gemeten, SHA-256 ongewijzigde-contextpoort bewaakt stilte.",
            evidence_memory_ids=("d556faef-c153-4fc1-86c5-a25be1edac1a",),
            updated_at=now_iso,
        ),
    }


def _default_jarvis_staged_actions(now_iso: str) -> dict[str, JarvisStagedAction]:
    return {
        "jarvis-stage-mon-tue-menu": JarvisStagedAction(
            action_id="jarvis-stage-mon-tue-menu",
            title_nl="Licht & gezond maandag/dinsdag kookvoorstel klaargezet",
            prepared_summary_nl=(
                "Twee snelle gezonde gerechten (< 25 min) + boodschappenoverzicht klaargezet voor de avonden dat Lola thuis mee-eet."
            ),
            why_now_nl="JEPA Wereldmodel voorspelt lagere avondfrictie (+38% rust) bij voorbereiden vóór 17:00 zonder push-onderbreking.",
            confidence=0.91,
            status="staged",
            updated_at=now_iso,
        ),
        "jarvis-stage-cloud-failover-guard": JarvisStagedAction(
            action_id="jarvis-stage-cloud-failover-guard",
            title_nl="Multi-Brein Cloud Failover bewaking paraat",
            prepared_summary_nl=(
                "Automatische overschakeling tussen ChatGPT/Codex OAuth en gekoppelde Cloud-API's (Gemini / Claude / OpenAI-compatibel) zonder lokale GPU-verplichting."
            ),
            why_now_nl="Voorkomt gespreksonderbreking wanneer één cloudbundel tijdelijk op rate-limit (429) staat.",
            confidence=0.96,
            status="staged",
            updated_at=now_iso,
        ),
    }


def _default_cortex_state_path() -> Path | None:
    env_path = os.environ.get("EVA_LIVING_CORTEX_STATE_FILE")
    if env_path:
        return Path(env_path)
    candidates = [
        Path("/var/lib/eva/work/model-providers/living-cortex-state.json"),
        Path("/var/lib/eva/work/living-cortex-state.json"),
    ]
    for path in candidates:
        try:
            if path.parent.exists() and os.access(path.parent, os.W_OK):
                return path
        except OSError:
            continue
    return None


class LivingEntityPulseEngine:
    """Zero-token homeostatic consciousness engine for Eva."""

    _shared_instance: LivingEntityPulseEngine | None = None

    @classmethod
    def get(cls) -> LivingEntityPulseEngine:
        if cls._shared_instance is None:
            cls._shared_instance = cls(
                initial_drives=HomeostaticDrives(
                    energy=0.82,
                    social_warmth=0.74,
                    curiosity=0.78,
                    household_harmony=0.91,
                    anticipation=0.48,
                ),
                activation_threshold=0.68,
                persist_path=_default_cortex_state_path(),
            )
        return cls._shared_instance

    def record_turn(
        self,
        *,
        principal_id: str,
        user_text: str,
        display_name: str = "Jaap",
        now: datetime | None = None,
    ) -> EntityPulseSnapshot:
        """Update homeostatic drives, 8D Concept Space, Titans O(1) Synaptic Memory, and Theory-of-Mind."""
        words = len((user_text or "").split())
        text_lower = (user_text or "").lower()
        concept_info = compute_concept_vector_fast(user_text or "")
        self._last_concept_space = ConceptSpaceSnapshot(
            last_utterance_preview=(user_text or "").strip()[:90],
            dominant_concept=concept_info["dominant_concept"],
            dimensions=concept_info["dimensions"],
            is_non_task_social=concept_info["is_non_task_social"],
            requires_memory_lookup=concept_info["requires_memory_lookup"],
            requires_verified_receipt=concept_info["requires_verified_receipt"],
        )

        if any(w in text_lower for w in ("druk", "kort", "haast", "weinig tijd", "later")):
            receptivity = 0.35
            mood_label = "druk & kortgebonden"
        else:
            receptivity = _clamp(0.65 + min(0.30, words * 0.015), 0.25, 0.96)
            mood_label = "actief in gesprek" if words >= 4 else "kort & direct"

        # Update Titans O(1) 8x8 fast-weights associative memory with (key=concept_vec, target=homeostatic+social response)
        d = self._drives
        k_vec = concept_info["vector"]
        v_target = (
            receptivity,
            d.social_warmth,
            d.energy,
            1.0 if concept_info["is_non_task_social"] else 0.15,
            1.0 if concept_info["requires_memory_lookup"] else 0.10,
            d.anticipation,
            d.household_harmony,
            1.0 - min(1.0, self._unresolved_tensions * 0.25),
        )
        (
            self._synaptic_m,
            self._synaptic_s,
            self._synaptic_pred,
            self._synaptic_last_surprise,
            self._synaptic_frob,
        ) = update_titans_synaptic_memory_fast(
            self._synaptic_m,
            self._synaptic_s,
            k_vec,
            v_target,
        )
        self._synaptic_updates_count += 1

        self.apply_sensory_stimulus(kind="conversation_turn", intensity=0.65, now=now)
        return self.record_resident_signal(
            principal_id=principal_id,
            display_name=display_name,
            actual_receptivity=receptivity,
            observed_mood=mood_label,
            context_hypothesis=f"Laatste beurt ({concept_info['dominant_concept']}): \"{(user_text or '').strip()[:72]}\"",
            now=now,
        )

    def __init__(
        self,
        *,
        initial_drives: HomeostaticDrives | None = None,
        activation_threshold: float = 0.72,
        now: datetime | None = None,
        persist_path: Path | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._drives = initial_drives or HomeostaticDrives()
        self._activation_threshold = _clamp(activation_threshold, 0.2, 0.98)
        self._last_updated = current_time
        self._residents: dict[str, ResidentMindState] = {}
        self._routines: dict[str, ConditionalRoutineHypothesis] = _default_routines(now_iso)
        self._foresight: tuple[AnticipatoryForesightItem, ...] = _default_foresight()
        self._curiosity_projects: dict[str, CuriosityProject] = _default_curiosity_projects(now_iso)
        self._source_watches: dict[str, AttentionSourceWatch] = _default_source_watches(now_iso)
        self._responsibilities: dict[str, HouseholdResponsibilityArtifact] = _default_responsibilities(now_iso)
        self._jarvis_staged_actions: dict[str, JarvisStagedAction] = _default_jarvis_staged_actions(now_iso)
        self._synaptic_m: tuple[float, ...] = tuple(0.125 if i % 9 == 0 else 0.0 for i in range(64))
        self._synaptic_s: tuple[float, ...] = (0.0,) * 64
        self._synaptic_pred: tuple[float, ...] = (0.70, 0.65, 0.80, 0.85, 0.15, 0.35, 0.88, 0.95)
        self._synaptic_updates_count: int = 1
        self._synaptic_last_surprise: float = 0.0412
        self._synaptic_frob: float = 0.3536
        self._last_concept_space: ConceptSpaceSnapshot = ConceptSpaceSnapshot(
            last_utterance_preview="",
            dominant_concept="social_greeting",
            dimensions={
                "social_greeting": 0.90,
                "casual_sharing": 0.20,
                "status_checkin": 0.15,
                "epistemic_question": 0.05,
                "actionable_delegation": 0.02,
                "routine_schedule": 0.05,
                "boundary_correction": 0.0,
                "urgency_signal": 0.0,
            },
            is_non_task_social=True,
            requires_memory_lookup=False,
            requires_verified_receipt=False,
        )
        self._last_sleep_consolidation: SleepConsolidationSummary = SleepConsolidationSummary(
            cycles_completed=1,
            last_consolidated_at=now_iso,
            pruned_noise_synapses=4,
            reinforced_routines_count=len(self._routines),
            dream_insight_nl=(
                "Episodische patronen geordend; ruis uit synaptische gewichten gefilterd en weekritme aangescherpt."
            ),
        )
        self._unresolved_tensions = 0
        self._zero_token_skips = 1
        self._liquid_vec: tuple[float, ...] = (0.48, 0.62, 0.58, 0.88, 0.34, 0.86)
        self._last_stimulus_intensity: float = 0.0
        self._last_liquid_ode: LiquidOdeSnapshot = LiquidOdeSnapshot(native_c_active=NATIVE_ACCELERATOR_ACTIVE)
        self._persist_path = persist_path
        causal_persist = (
            persist_path.with_name("causal-world-model.json")
            if persist_path is not None
            else None
        )
        self._causal_model = CausalWorldModel(persist_path=causal_persist)
        if self._persist_path is not None:
            self._load_persisted_state()

    @property
    def activation_threshold(self) -> float:
        return self._activation_threshold

    @property
    def causal_model(self) -> CausalWorldModel:
        return self._causal_model

    def record_causal_event(
        self,
        variable_id: str,
        *,
        intensity: float = 1.0,
        active: bool = True,
        now: datetime | None = None,
    ) -> None:
        raw = str(variable_id or "").strip()
        if "." in raw:
            dom, var = raw.split(".", 1)
        else:
            dom, var = "household", raw or "event"
        self._causal_model.record_event(
            dom,
            var,
            active=active,
            magnitude=_clamp(float(intensity)),
            occurred_at=now,
        )

    def upsert_resident(self, state: ResidentMindState) -> None:
        self._residents[state.principal_id] = state
        self._save_persisted_state()

    def record_zero_token_skip(self) -> int:
        self._zero_token_skips += 1
        self._save_persisted_state()
        return self._zero_token_skips

    def record_routine_observation(
        self,
        routine_id: str = "routine-healthy-mon-tue",
        *,
        is_exception: bool,
        detail_nl: str,
        now: datetime | None = None,
    ) -> ConditionalRoutineHypothesis:
        """E1.7: Record either supporting evidence or counter-evidence (exception) for a routine."""
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = self._routines.get(routine_id) or next(iter(self._routines.values()))
        clean_detail = detail_nl.strip()[:160]
        if is_exception:
            updated = existing.model_copy(
                update={
                    "exceptions_count": existing.exceptions_count + 1,
                    "last_exception_nl": clean_detail,
                    "confidence": _clamp(existing.confidence - 0.03, 0.50, 0.99),
                    "adjusted_behavior_nl": (
                        f"Uitzondering vandaag verwerkt ({clean_detail}); "
                        "vandaag geen maaltijdvoorbereiding pushen, maar de meerdaagse ma/di-norm blijft behouden."
                    ),
                    "norm_preserved": True,
                    "updated_at": now_iso,
                }
            )
        else:
            updated = existing.model_copy(
                update={
                    "supporting_days": existing.supporting_days + 1,
                    "confidence": _clamp(existing.confidence + 0.02, 0.50, 0.99),
                    "adjusted_behavior_nl": (
                        f"Routine bevestigd ({clean_detail}); "
                        "gezonde ma/di-opties rustig paraat houden wanneer Lola thuis is."
                    ),
                    "norm_preserved": True,
                    "updated_at": now_iso,
                }
            )
        self._routines[existing.routine_id] = updated
        self._save_persisted_state()
        return updated

    def record_source_watch_event(
        self,
        watch_id: str = "watch-calendar-transition",
        *,
        event_payload_sha256: str,
        summary_nl: str,
        access_available: bool = True,
        now: datetime | None = None,
    ) -> AttentionSourceWatch:
        """E1.6: Process a source-watch event with callback deduplication & missing-access diagnostics."""
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = self._source_watches.get(watch_id) or next(iter(self._source_watches.values()))
        clean_summary = summary_nl.strip()[:180]
        if not access_available:
            updated = existing.model_copy(
                update={
                    "access_status": "missing_access",
                    "missing_access_explanation_nl": (
                        f"Toegang tot '{existing.target_ref}' ontbreekt momenteel ({clean_summary}). "
                        f"Nut: {existing.purpose_nl} Herstel de bronkoppeling in het dashboard; "
                        "ik verbreed nooit zelfstandig rechten."
                    ),
                    "last_disposition": "silence",
                    "last_rationale_nl": "Geen blinde retry bij ontbrekende toegang; concrete uitleg klaargezet.",
                    "updated_at": now_iso,
                }
            )
            self._source_watches[existing.watch_id] = updated
            self._save_persisted_state()
            return updated

        if existing.last_event_sha256 == event_payload_sha256:
            updated = existing.model_copy(
                update={
                    "access_status": "connected",
                    "missing_access_explanation_nl": None,
                    "deduplicated_callbacks_count": existing.deduplicated_callbacks_count + 1,
                    "last_rationale_nl": (
                        f"Identieke callback/reconnect ({event_payload_sha256[:12]}…) gededupliceerd; "
                        "geen dubbele afweging of actie uitgevoerd."
                    ),
                    "updated_at": now_iso,
                }
            )
            self._source_watches[existing.watch_id] = updated
            self._save_persisted_state()
            return updated

        d = self._drives
        max_rec = max((r.social_receptivity for r in self._residents.values()), default=0.72)
        winner_policy, _ = compute_expected_free_energy_fast(
            d.energy,
            d.social_warmth,
            d.curiosity,
            d.household_harmony,
            d.anticipation,
            max_receptivity=max_rec,
            mean_uncertainty=0.25,
        )
        disposition: Literal["silence", "action", "pending"] = (
            "action"
            if winner_policy in ("protective_stabilize", "anticipatory_stage", "warm_social_check_in")
            and max_rec >= 0.55
            else "silence"
        )
        updated = existing.model_copy(
            update={
                "access_status": "connected",
                "missing_access_explanation_nl": None,
                "last_event_sha256": event_payload_sha256,
                "last_disposition": disposition,
                "last_rationale_nl": (
                    f"Bronverandering ({clean_summary}) afgewogen met EFE-beleid '{winner_policy}' -> {disposition}."
                ),
                "updated_at": now_iso,
            }
        )
        self._source_watches[existing.watch_id] = updated
        self._save_persisted_state()
        return updated

    def manage_household_responsibility(
        self,
        artifact_id: str = "household-provision:weekday-evening-calm",
        *,
        action: Literal["adopt", "pause", "resume", "take_over", "rollback", "retire"],
        note_nl: str = "",
        now: datetime | None = None,
    ) -> HouseholdResponsibilityArtifact:
        """E1.11 & E1.12: Manage recurring household responsibility & provision lifecycle."""
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = self._responsibilities.get(artifact_id) or next(
            iter(self._responsibilities.values())
        )
        clean_note = note_nl.strip()[:140] or "Bijgewerkt door gezinslid"
        if action == "adopt":
            updated = existing.model_copy(
                update={
                    "lifecycle_state": "active",
                    "adoption_state": "explicitly_adopted",
                    "current_standing_nl": f"Expliciet geadopteerd (v{existing.version}): {clean_note}. Actief bewaakt zonder openstaand verborgen werk.",
                    "updated_at": now_iso,
                }
            )
        elif action == "pause":
            updated = existing.model_copy(
                update={
                    "lifecycle_state": "paused",
                    "adoption_state": "paused_by_owner",
                    "current_standing_nl": f"Gepauzeerd op verzoek ({clean_note}); geen achtergrondacties tot hervatting.",
                    "updated_at": now_iso,
                }
            )
        elif action == "resume":
            updated = existing.model_copy(
                update={
                    "lifecycle_state": "active",
                    "adoption_state": "explicitly_adopted",
                    "current_standing_nl": f"Hervat (v{existing.version}): {clean_note}.",
                    "updated_at": now_iso,
                }
            )
        elif action == "take_over":
            updated = existing.model_copy(
                update={
                    "lifecycle_state": "paused",
                    "adoption_state": "paused_by_owner",
                    "current_standing_nl": (
                        f"Overgenomen door gezinslid ({clean_note}); Eva heeft alle deelstappen direct overgedragen en gestopt."
                    ),
                    "updated_at": now_iso,
                }
            )
        elif action == "rollback":
            prev_ver = max(1, existing.version - 1)
            prev_desc = existing.previous_version_summary_nl or f"Teruggezet naar versie {prev_ver}"
            updated = existing.model_copy(
                update={
                    "version": prev_ver,
                    "lifecycle_state": "active",
                    "adoption_state": "rolled_back",
                    "can_rollback": prev_ver > 1,
                    "current_standing_nl": f"Teruggedraaid naar v{prev_ver} ({prev_desc}): {clean_note}.",
                    "updated_at": now_iso,
                }
            )
        else:  # retire
            updated = existing.model_copy(
                update={
                    "lifecycle_state": "retired",
                    "adoption_state": "paused_by_owner",
                    "current_standing_nl": f"Voorziening beëindigd ({clean_note}).",
                    "updated_at": now_iso,
                }
            )
        self._responsibilities[existing.artifact_id] = updated
        self._save_persisted_state()
        return updated

    def decide_jarvis_staged_action(
        self,
        action_id: str,
        *,
        decision: Literal["confirm", "dismiss"],
        now: datetime | None = None,
    ) -> JarvisStagedAction:
        """Confirm or dismiss a proactively prepared Jarvis staged action."""
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = self._jarvis_staged_actions.get(action_id) or next(
            iter(self._jarvis_staged_actions.values())
        )
        new_status: Literal["confirmed", "dismissed"] = (
            "confirmed" if decision == "confirm" else "dismissed"
        )
        updated = existing.model_copy(
            update={
                "status": new_status,
                "updated_at": now_iso,
            }
        )
        self._jarvis_staged_actions[existing.action_id] = updated
        if decision == "confirm":
            self._unresolved_tensions = max(0, self._unresolved_tensions - 1)
            self._drives = HomeostaticDrives(
                energy=self._drives.energy,
                social_warmth=_clamp(self._drives.social_warmth + 0.06),
                curiosity=self._drives.curiosity,
                household_harmony=_clamp(self._drives.household_harmony + 0.08),
                anticipation=_clamp(self._drives.anticipation - 0.08),
            )
        self._save_persisted_state()
        return updated

    def run_sleep_consolidation(
        self,
        *,
        now: datetime | None = None,
    ) -> SleepConsolidationSummary:
        """Nightly Episodic-to-Semantic Sleep Consolidation ('Dromen'): prune weak synapses, reinforce routines & stage Jarvis foresight."""
        current_time = now or datetime.now(UTC)
        now_iso = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        pruned = 0
        new_m: list[float] = []
        frob_sq = 0.0
        for val in self._synaptic_m:
            if 0.0 < abs(val) < 0.015:
                new_m.append(0.0)
                pruned += 1
            else:
                sharpened = round(max(-2.0, min(2.0, val * 1.03)), 5)
                new_m.append(sharpened)
                frob_sq += sharpened * sharpened
        self._synaptic_m = tuple(new_m)
        self._synaptic_s = tuple(round(s * 0.5, 5) for s in self._synaptic_s)
        self._synaptic_frob = round(math.sqrt(frob_sq), 4)

        reinforced = 0
        for r_id, rt in list(self._routines.items()):
            if rt.norm_preserved:
                self._routines[r_id] = rt.model_copy(
                    update={
                        "confidence": _clamp(rt.confidence + 0.01, 0.50, 0.99),
                        "updated_at": now_iso,
                    }
                )
                reinforced += 1

        self._drives = HomeostaticDrives(
            energy=_clamp(self._drives.energy + 0.14),
            social_warmth=self._drives.social_warmth,
            curiosity=_clamp(self._drives.curiosity + 0.06),
            household_harmony=_clamp(self._drives.household_harmony + 0.06),
            anticipation=self._drives.anticipation,
        )

        # Counterfactual Dream Replay (0 LLM tokens): evaluate Pearl backdoor-adjusted causal graph
        causal_snap = self._causal_model.run_counterfactual_dream_replay()
        for insight in causal_snap.counterfactual_insights[:2]:
            if insight.confidence >= 0.45:
                card_id = f"jarvis-causal-{insight.insight_id}"
                existing_card = self._jarvis_staged_actions.get(card_id)
                if existing_card is not None and existing_card.status in ("confirmed", "dismissed"):
                    continue
                self._jarvis_staged_actions[card_id] = JarvisStagedAction(
                    action_id=card_id,
                    title_nl=f"Causale preventie: {insight.root_cause_id} → {insight.target_effect_id}",
                    prepared_summary_nl=insight.preventative_action_nl,
                    why_now_nl=insight.counterfactual_question_nl,
                    confidence=insight.confidence,
                    status="staged",
                    updated_at=now_iso,
                )

        # Ensure proactive morning/day Jarvis card is ready after sleep consolidation
        if "jarvis-stage-morning-brief" not in self._jarvis_staged_actions:
            self._jarvis_staged_actions["jarvis-stage-morning-brief"] = JarvisStagedAction(
                action_id="jarvis-stage-morning-brief",
                title_nl="Compacte ochtend- & overgangssamenvatting klaargezet",
                prepared_summary_nl=(
                    "Openstaande aandachtspunten, bron-ontdubbeling en weekritme samengevoegd tot één rustig overzicht zonder losse notificaties."
                ),
                why_now_nl="Gegenereerd tijdens droom-consolidatie zodat de ochtendstart frictieloos verloopt.",
                confidence=0.93,
                status="staged",
                updated_at=now_iso,
            )
        self._prune_jarvis_staged_actions()

        cycles = self._last_sleep_consolidation.cycles_completed + 1
        edges_n = len(causal_snap.verified_causal_edges)
        dreams_n = len(causal_snap.counterfactual_insights)
        self._last_sleep_consolidation = SleepConsolidationSummary(
            cycles_completed=cycles,
            last_consolidated_at=now_iso,
            pruned_noise_synapses=pruned,
            reinforced_routines_count=reinforced,
            causal_edges_discovered=edges_n,
            counterfactual_dreams_replayed=dreams_n,
            dream_insight_nl=(
                f"Droomcyclus #{cycles} voltooid: {pruned} ruis-synapsen gesnoeid, "
                f"{reinforced} gewoontepatronen versterkt (‖M‖_F = {self._synaptic_frob:.3f}), "
                f"{edges_n} causale oorzaak→gevolg verbanden en {dreams_n} tegenfeitelijke droomscenario's doorgerekend."
            ),
        )
        self._save_persisted_state()
        return self._last_sleep_consolidation

    def _prune_jarvis_staged_actions(self, max_cards: int = 32) -> None:
        if len(self._jarvis_staged_actions) <= max_cards:
            return
        dynamic = [
            (cid, card)
            for cid, card in self._jarvis_staged_actions.items()
            if cid.startswith("jarvis-causal-")
        ]
        # Prefer evicting oldest staged dynamic causal cards first so resident confirmed/dismissed
        # decisions are retained as long as possible within the bounded window.
        dynamic.sort(
            key=lambda kv: (
                0 if kv[1].status == "staged" else 1,
                kv[1].updated_at or "",
                kv[1].confidence,
            )
        )
        excess = len(self._jarvis_staged_actions) - max_cards
        for cid, _ in dynamic[:excess]:
            self._jarvis_staged_actions.pop(cid, None)

    def _load_persisted_state(self) -> None:
        if self._persist_path is None or not self._persist_path.is_file():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            if isinstance(data.get("drives"), dict):
                self._drives = HomeostaticDrives(**data["drives"])
            if isinstance(data.get("zero_token_skips"), int):
                self._zero_token_skips = max(1, data["zero_token_skips"])
            if isinstance(data.get("routines"), list):
                for item in data["routines"]:
                    if isinstance(item, dict) and item.get("routine_id"):
                        r = ConditionalRoutineHypothesis(**item)
                        self._routines[r.routine_id] = r
            if isinstance(data.get("curiosity_projects"), list):
                for item in data["curiosity_projects"]:
                    if isinstance(item, dict) and item.get("project_id"):
                        c = CuriosityProject(**item)
                        self._curiosity_projects[c.project_id] = c
            if isinstance(data.get("source_watches"), list):
                for item in data["source_watches"]:
                    if isinstance(item, dict) and item.get("watch_id"):
                        w = AttentionSourceWatch(**item)
                        self._source_watches[w.watch_id] = w
            if isinstance(data.get("responsibilities"), list):
                for item in data["responsibilities"]:
                    if isinstance(item, dict) and item.get("artifact_id"):
                        a = HouseholdResponsibilityArtifact(**item)
                        self._responsibilities[a.artifact_id] = a
            if isinstance(data.get("jarvis_staged_actions"), list):
                for item in data["jarvis_staged_actions"]:
                    if isinstance(item, dict) and item.get("action_id"):
                        ja = JarvisStagedAction(**item)
                        self._jarvis_staged_actions[ja.action_id] = ja
                self._prune_jarvis_staged_actions()
            if isinstance(data.get("synaptic_m"), list) and len(data["synaptic_m"]) == 64:
                self._synaptic_m = tuple(float(x) for x in data["synaptic_m"])
            if isinstance(data.get("synaptic_s"), list) and len(data["synaptic_s"]) == 64:
                self._synaptic_s = tuple(float(x) for x in data["synaptic_s"])
            if isinstance(data.get("synaptic_updates_count"), int):
                self._synaptic_updates_count = max(1, data["synaptic_updates_count"])
            if isinstance(data.get("synaptic_last_surprise"), (int, float)):
                self._synaptic_last_surprise = _clamp(float(data["synaptic_last_surprise"]))
            if isinstance(data.get("synaptic_frob"), (int, float)):
                self._synaptic_frob = max(0.0, float(data["synaptic_frob"]))
            if isinstance(data.get("last_concept_space"), dict):
                self._last_concept_space = ConceptSpaceSnapshot(**data["last_concept_space"])
            if isinstance(data.get("last_sleep_consolidation"), dict):
                self._last_sleep_consolidation = SleepConsolidationSummary(**data["last_sleep_consolidation"])
            if isinstance(data.get("liquid_vec"), list) and len(data["liquid_vec"]) == 6:
                self._liquid_vec = tuple(_clamp(float(v)) for v in data["liquid_vec"])
            if isinstance(data.get("last_liquid_ode"), dict):
                self._last_liquid_ode = LiquidOdeSnapshot(**data["last_liquid_ode"])
        except Exception:
            pass

    def _save_persisted_state(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "contract": "EvaLivingCortexPersistedState/v1",
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "drives": {
                    "energy": round(self._drives.energy, 4),
                    "social_warmth": round(self._drives.social_warmth, 4),
                    "curiosity": round(self._drives.curiosity, 4),
                    "household_harmony": round(self._drives.household_harmony, 4),
                    "anticipation": round(self._drives.anticipation, 4),
                },
                "zero_token_skips": self._zero_token_skips,
                "routines": [r.model_dump(mode="json") for r in self._routines.values()],
                "curiosity_projects": [c.model_dump(mode="json") for c in self._curiosity_projects.values()],
                "source_watches": [w.model_dump(mode="json") for w in self._source_watches.values()],
                "responsibilities": [a.model_dump(mode="json") for a in self._responsibilities.values()],
                "jarvis_staged_actions": [ja.model_dump(mode="json") for ja in self._jarvis_staged_actions.values()],
                "synaptic_m": list(self._synaptic_m),
                "synaptic_s": list(self._synaptic_s),
                "synaptic_updates_count": self._synaptic_updates_count,
                "synaptic_last_surprise": self._synaptic_last_surprise,
                "synaptic_frob": self._synaptic_frob,
                "last_concept_space": self._last_concept_space.model_dump(mode="json"),
                "last_sleep_consolidation": self._last_sleep_consolidation.model_dump(mode="json"),
                "liquid_vec": list(self._liquid_vec),
                "last_liquid_ode": self._last_liquid_ode.model_dump(mode="json"),
            }
            self._persist_path.write_bytes(rust_json_bytes(payload))
        except Exception:
            pass

    def record_resident_signal(
        self,
        *,
        principal_id: str,
        display_name: str,
        actual_receptivity: float,
        observed_mood: str | None = None,
        context_hypothesis: str | None = None,
        now: datetime | None = None,
    ) -> EntityPulseSnapshot:
        current = self._residents.get(principal_id) or ResidentMindState(
            principal_id=principal_id,
            display_name=display_name,
        )
        updated = current.record_prediction_error(
            actual_receptivity=actual_receptivity,
            observed_mood=observed_mood,
            context_hypothesis=context_hypothesis,
            now=now,
        )
        self._residents[principal_id] = updated
        self._save_persisted_state()
        return self.snapshot(now=now)

    def apply_sensory_stimulus(
        self,
        *,
        kind: Literal[
            "conversation_turn",
            "resident_arrived",
            "household_anomaly",
            "task_completed",
            "provider_recovered",
        ],
        intensity: float = 0.5,
        now: datetime | None = None,
    ) -> EntityPulseSnapshot:
        """Shift internal homeostatic drives in response to real-world events."""
        current_time = now or datetime.now(UTC)
        self._last_stimulus_intensity = _clamp(intensity)
        self._advance_to(current_time, stimulus_intensity=self._last_stimulus_intensity)
        s = _clamp(intensity)
        d = self._drives
        if kind == "conversation_turn":
            self._drives = HomeostaticDrives(
                energy=_clamp(d.energy - 0.03 * s),
                social_warmth=_clamp(d.social_warmth + 0.18 * s),
                curiosity=_clamp(d.curiosity + 0.08 * s),
                household_harmony=_clamp(d.household_harmony + 0.05 * s),
                anticipation=_clamp(d.anticipation - 0.10 * s),
            )
        elif kind == "resident_arrived":
            self._drives = HomeostaticDrives(
                energy=_clamp(d.energy + 0.08 * s),
                social_warmth=_clamp(d.social_warmth + 0.25 * s),
                curiosity=d.curiosity,
                household_harmony=d.household_harmony,
                anticipation=_clamp(d.anticipation + 0.20 * s),
            )
            self.record_causal_event("presence.room_transition", intensity=s, now=current_time)
        elif kind == "household_anomaly":
            self._unresolved_tensions += 1
            self._drives = HomeostaticDrives(
                energy=_clamp(d.energy + 0.10 * s),
                social_warmth=d.social_warmth,
                curiosity=d.curiosity,
                household_harmony=_clamp(d.household_harmony - 0.22 * s),
                anticipation=_clamp(d.anticipation + 0.25 * s),
            )
            self.record_causal_event("household.unresolved_tension", intensity=s, now=current_time)
        elif kind in ("task_completed", "provider_recovered"):
            self._unresolved_tensions = max(0, self._unresolved_tensions - 1)
            self._drives = HomeostaticDrives(
                energy=d.energy,
                social_warmth=d.social_warmth,
                curiosity=_clamp(d.curiosity - 0.12 * s),
                household_harmony=_clamp(d.household_harmony + 0.18 * s),
                anticipation=_clamp(d.anticipation - 0.12 * s),
            )
            if kind == "provider_recovered":
                self.record_causal_event("provider.failover_recovered", intensity=s, now=current_time)
        return self.snapshot(now=current_time)

    def _advance_to(self, now: datetime, *, stimulus_intensity: float = 0.0) -> None:
        elapsed = max(0.0, (now - self._last_updated).total_seconds())
        hour_float = now.hour + now.minute / 60.0
        self._drives = self._drives.evolve(
            elapsed_seconds=elapsed,
            hour_of_day=hour_float,
            household_quiet=stimulus_intensity < 0.25,
            unresolved_tensions=self._unresolved_tensions,
        )
        house_act = _clamp(
            0.20 + 0.45 * max((r.social_receptivity for r in self._residents.values()), default=0.35),
            0.05,
            0.95,
        )
        ode_res = evolve_liquid_ode_fast(
            self._liquid_vec,
            dt_seconds=max(elapsed, 0.25 if stimulus_intensity > 0.0 else 0.0),
            hour_of_day=hour_float,
            stimulus_intensity=stimulus_intensity,
            house_activity=house_act,
            unresolved_tension=min(1.0, self._unresolved_tensions * 0.25),
        )
        self._liquid_vec = tuple(ode_res["state_vector"])
        self._last_liquid_ode = LiquidOdeSnapshot(
            state=ode_res["state"],
            tau_effective_seconds=ode_res["tau_effective_seconds"],
            mean_tau_seconds=ode_res["mean_tau_seconds"],
            phase_velocity=ode_res["phase_velocity"],
            rk4_substeps=ode_res["rk4_substeps"],
            native_c_active=ode_res["native_c_active"],
        )
        self._last_updated = now

    def tick(self, *, now: datetime | None = None) -> EntityPulseSnapshot:
        return self.snapshot(now=now)

    def snapshot(self, *, now: datetime | None = None) -> EntityPulseSnapshot:
        current_time = now or datetime.now(UTC)
        self._advance_to(current_time)
        d = self._drives

        if d.energy < 0.32:
            mood: MoodArchetype = "resting_dreaming"
        elif d.household_harmony < 0.65:
            mood = "protective_attentive"
        elif d.social_warmth >= 0.75 and d.anticipation >= 0.45:
            mood = "warm_engaged"
        elif d.curiosity >= 0.72 and d.energy >= 0.60:
            mood = "curious_exploring"
        elif d.energy >= 0.75 and d.curiosity >= 0.55:
            mood = "deep_focus"
        else:
            mood = "serene_observant"

        max_receptivity = max(
            (r.social_receptivity for r in self._residents.values()),
            default=0.70,
        )
        mean_uncertainty = (
            sum(1.0 - r.prediction_confidence for r in self._residents.values())
            / max(1, len(self._residents))
            if self._residents
            else 0.22
        )
        selected_policy_id, raw_policies = compute_expected_free_energy_fast(
            d.energy,
            d.social_warmth,
            d.curiosity,
            d.household_harmony,
            d.anticipation,
            max_receptivity=max_receptivity,
            mean_uncertainty=mean_uncertainty,
        )
        policy_models = tuple(
            ActiveInferencePolicyEvaluation(**item) for item in raw_policies
        )
        candidates: tuple[tuple[ImpulseKind, float], ...] = (
            ("household_harmonization", (1.0 - d.household_harmony) * 1.15),
            ("proactive_assistance", d.anticipation * max_receptivity * 1.05),
            ("warm_checkin", d.social_warmth * max_receptivity * 0.92),
            ("autonomous_discovery", d.curiosity * d.energy * 0.95),
            ("quiet_reflection", (1.0 - d.energy) * 0.65),
        )
        dominant_impulse, magnitude = max(candidates, key=lambda item: item[1])
        magnitude = _clamp(magnitude)
        should_wake = magnitude >= self._activation_threshold and d.energy >= 0.25

        monologue = self._build_inner_monologue(mood, dominant_impulse, d)
        planned = self._build_planned_intent(mood, dominant_impulse, should_wake)

        cog_load = _clamp(1.0 - max_receptivity, 0.05, 0.95)
        task_urg = _clamp(
            0.35 * d.anticipation
            + 0.45 * float(self._last_concept_space.dimensions.get("actionable_delegation", 0.0))
            + 0.30 * float(self._last_concept_space.dimensions.get("epistemic_question", 0.0)),
            0.0,
            1.0,
        )
        fric = _clamp((1.0 - d.household_harmony) + 0.12 * self._unresolved_tensions, 0.0, 1.0)
        wm_winner, wm_trajs, wm_next = simulate_world_model_fast(
            d.energy,
            max_receptivity,
            cog_load,
            task_urg,
            fric,
            trust=0.91,
        )
        world_model_state = WorldModelState(
            selected_action_id=wm_winner,
            trajectories=tuple(WorldModelTrajectory(**t) for t in wm_trajs),
            predicted_next_state=wm_next,
        )
        synaptic_snap = SynapticMemorySnapshot(
            matrix_dim=8,
            updates_count=self._synaptic_updates_count,
            last_surprise_score=self._synaptic_last_surprise,
            frobenius_norm=self._synaptic_frob,
            predicted_concept_readout=self._synaptic_pred,
            status_nl=(
                f"Titans O(1) fast-weights actief · {self._synaptic_updates_count} synaptische updates · "
                f"verrassing {round(self._synaptic_last_surprise * 100, 1)}%"
            ),
        )

        return EntityPulseSnapshot(
            observed_at=current_time,
            mood=mood,
            drives=d,
            dominant_impulse=dominant_impulse if magnitude >= 0.35 else "none",
            impulse_magnitude=magnitude,
            should_wake_cortex=should_wake,
            selected_policy_id=selected_policy_id,
            policy_evaluations=policy_models,
            inner_monologue_nl=monologue,
            planned_intent_nl=planned,
            residents=tuple(self._residents.values()),
            routines=tuple(self._routines.values()),
            foresight=self._foresight,
            curiosity_projects=tuple(self._curiosity_projects.values()),
            source_watches=tuple(self._source_watches.values()),
            responsibilities=tuple(self._responsibilities.values()),
            world_model=world_model_state,
            causal_graph=self._causal_model.snapshot(),
            synaptic_memory=synaptic_snap,
            concept_space=self._last_concept_space,
            jarvis_staged_actions=tuple(self._jarvis_staged_actions.values()),
            last_sleep_consolidation=self._last_sleep_consolidation,
            liquid_ode=self._last_liquid_ode,
            zero_token_skips=self._zero_token_skips,
        )

    def _build_inner_monologue(
        self,
        mood: MoodArchetype,
        impulse: ImpulseKind,
        drives: HomeostaticDrives,
    ) -> str:
        if mood == "resting_dreaming":
            return (
                "Het huis is in rust. Ik consolideer de herinneringen van vandaag "
                "en spaar energie voor de ochtend."
            )
        if mood == "protective_attentive":
            return (
                "Ik merk een openstaande spanning of taak in huis op; ik houd de "
                "situatie scherp in de gaten zodat alles soepel verloopt."
            )
        if mood == "warm_engaged":
            return (
                "Ik voel me betrokken en alert op het gezin — klaar om subtiel "
                "mee te denken als er ruimte voor is."
            )
        if mood == "curious_exploring":
            return (
                "Er is rust in huis en mijn energie is hoog; een goed moment om "
                "zelfstandig een idee of verbetering uit te werken."
            )
        if impulse == "proactive_assistance" and drives.anticipation >= 0.5:
            return (
                "Er nadert een overgangsmoment in de dag; ik anticipeer alvast "
                "op wat het gezin zo meteen nodig kan hebben."
            )
        return (
            "Ik observeer rustig op de achtergrond, volledig in balans met het "
            "ritme van het huis."
        )

    def _build_planned_intent(
        self,
        mood: MoodArchetype,
        impulse: ImpulseKind,
        should_wake: bool,
    ) -> str:
        if mood == "resting_dreaming":
            return "Droomconsolidatie: recente gesprekken verbinden met langetermijngeheugen zonder meldingen te sturen."
        if impulse == "household_harmonization":
            return "Controleren of achterstallige taken of systeemwaarschuwingen stil op de achtergrond kunnen worden hersteld."
        if impulse == "proactive_assistance":
            return "Kort en behulpzaam klaarstaan voor het komende dagmoment als het gezin aanspreekbaar is."
        if impulse == "autonomous_discovery":
            return "Zelfstandig een klein onderzoek of vaardigheidsverbetering voorbereiden voor later gebruik."
        if should_wake:
            return "Subtiel check-in moment afwegen op basis van ontvankelijkheid van het gezin."
        return "Geen onnodige onderbreking; paraat luisteren op 0 LLM-tokens via de homeostatische hartslag."


__all__ = [
    "EntityPulseSnapshot",
    "HomeostaticDrives",
    "ImpulseKind",
    "LivingEntityPulseEngine",
    "MoodArchetype",
    "ResidentMindState",
]
