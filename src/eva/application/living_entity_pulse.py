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
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        }
        payload["state_sha256"] = fast_canonical_sha256(payload)
        return payload

    def to_json_bytes(self) -> bytes:
        return rust_json_bytes(self.to_payload())


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
        receptivity = _clamp(0.65 + min(0.30, words * 0.015), 0.25, 0.96)
        self.apply_sensory_stimulus(kind="conversation_turn", intensity=0.65, now=now)
        return self.record_resident_signal(
            principal_id=principal_id,
            display_name=display_name,
            actual_receptivity=receptivity,
            observed_mood="actief in gesprek" if words >= 4 else "kort & direct",
            context_hypothesis=f"Laatste beurt: \"{(user_text or '').strip()[:90]}\"",
            now=now,
        )

    def __init__(
        self,
        *,
        initial_drives: HomeostaticDrives | None = None,
        activation_threshold: float = 0.72,
        now: datetime | None = None,
    ) -> None:
        self._drives = initial_drives or HomeostaticDrives()
        self._activation_threshold = _clamp(activation_threshold, 0.2, 0.98)
        self._last_updated = now or datetime.now(UTC)
        self._residents: dict[str, ResidentMindState] = {}
        self._unresolved_tensions = 0

    @property
    def activation_threshold(self) -> float:
        return self._activation_threshold

    def upsert_resident(self, state: ResidentMindState) -> None:
        self._residents[state.principal_id] = state

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
