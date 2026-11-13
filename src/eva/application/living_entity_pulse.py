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
    AnticipatoryForesightItem,
    BaseModel,
    ConditionalRoutineHypothesis,
    ConfigDict,
    CuriosityProject,
    Field,
    field_validator,
)

from eva.application.native_fastpath import (
    NATIVE_ACCELERATOR_ACTIVE,
    fast_canonical_sha256,
    rust_json_bytes,
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
            "inner_monologue_nl": self.inner_monologue_nl,
            "planned_intent_nl": self.planned_intent_nl,
            "zero_token_skips": self.zero_token_skips,
            "engine_runtime": {
                "pydantic_v2_rust_core": True,
                "native_c_simd_fastpath": NATIVE_ACCELERATOR_ACTIVE,
            },
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
        """Update homeostatic drives and Theory-of-Mind from a live user conversation turn."""
        words = len((user_text or "").split())
        text_lower = (user_text or "").lower()
        if any(w in text_lower for w in ("druk", "kort", "haast", "weinig tijd", "later")):
            receptivity = 0.35
            mood_label = "druk & kortgebonden"
        else:
            receptivity = _clamp(0.65 + min(0.30, words * 0.015), 0.25, 0.96)
            mood_label = "actief in gesprek" if words >= 4 else "kort & direct"
        self.apply_sensory_stimulus(kind="conversation_turn", intensity=0.65, now=now)
        return self.record_resident_signal(
            principal_id=principal_id,
            display_name=display_name,
            actual_receptivity=receptivity,
            observed_mood=mood_label,
            context_hypothesis=f"Laatste beurt: \"{(user_text or '').strip()[:90]}\"",
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
        self._unresolved_tensions = 0
        self._zero_token_skips = 1
        self._persist_path = persist_path
        if self._persist_path is not None:
            self._load_persisted_state()

    @property
    def activation_threshold(self) -> float:
        return self._activation_threshold

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
        self._advance_to(current_time)
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
        elif kind == "household_anomaly":
            self._unresolved_tensions += 1
            self._drives = HomeostaticDrives(
                energy=_clamp(d.energy + 0.10 * s),
                social_warmth=d.social_warmth,
                curiosity=d.curiosity,
                household_harmony=_clamp(d.household_harmony - 0.22 * s),
                anticipation=_clamp(d.anticipation + 0.25 * s),
            )
        elif kind in ("task_completed", "provider_recovered"):
            self._unresolved_tensions = max(0, self._unresolved_tensions - 1)
            self._drives = HomeostaticDrives(
                energy=d.energy,
                social_warmth=d.social_warmth,
                curiosity=_clamp(d.curiosity - 0.12 * s),
                household_harmony=_clamp(d.household_harmony + 0.18 * s),
                anticipation=_clamp(d.anticipation - 0.12 * s),
            )
        return self.snapshot(now=current_time)

    def _advance_to(self, now: datetime) -> None:
        elapsed = max(0.0, (now - self._last_updated).total_seconds())
        hour_float = now.hour + now.minute / 60.0
        self._drives = self._drives.evolve(
            elapsed_seconds=elapsed,
            hour_of_day=hour_float,
            household_quiet=True,
            unresolved_tensions=self._unresolved_tensions,
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
        return EntityPulseSnapshot(
            observed_at=current_time,
            mood=mood,
            drives=d,
            dominant_impulse=dominant_impulse if magnitude >= 0.35 else "none",
            impulse_magnitude=magnitude,
            should_wake_cortex=should_wake,
            inner_monologue_nl=monologue,
            planned_intent_nl=planned,
            residents=tuple(self._residents.values()),
            routines=tuple(self._routines.values()),
            foresight=self._foresight,
            curiosity_projects=tuple(self._curiosity_projects.values()),
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
