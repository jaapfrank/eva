"""Structural Causal Model (oorzaak -> gevolg) & Counterfactual Dream Engine (0 LLM tokens).

Implements genuine Judea Pearl backdoor-adjusted interventional estimation
P(Y=1 | do(X=x)) = sum_z P(Y=1 | X=x, Z=z) P(Z=z) over circadian confounder strata Z
with strict temporal precedence (t_X < t_Y), symmetric exposure windows, and monotonic
interventional Tian-Pearl bounds (Y_{1}(u) >= Y_{0}(u)) for Probability of Necessity (PN)
and Probability of Sufficiency (PS).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import threading
from typing import Any, Literal
import uuid

from eva.contracts.living_cortex import (
    CausalStructuralEdge,
    CausalWorldGraphSnapshot,
    CounterfactualDreamInsight,
)

CircadianStratum = Literal["ochtend", "middag", "avond", "nacht"]
_STRATA: tuple[CircadianStratum, ...] = ("ochtend", "middag", "avond", "nacht")
_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9_.-]+")
MAX_CAUSAL_EVENTS = 512


def _circadian_stratum(ts: datetime) -> CircadianStratum:
    hour = ts.astimezone(UTC).hour
    if 6 <= hour < 12:
        return "ochtend"
    if 12 <= hour < 18:
        return "middag"
    if 18 <= hour < 23:
        return "avond"
    return "nacht"


def _sanitize_var_part(raw: str, fallback: str) -> str:
    cleaned = _SAFE_TOKEN_RE.sub("_", str(raw or "").strip().lower()).strip("._-")
    return (cleaned[:48] if cleaned else fallback)


def _clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    if math.isnan(val) or math.isinf(val):
        return low
    return max(low, min(high, val))


@dataclass(frozen=True, slots=True)
class CausalEventRecord:
    event_id: str
    variable_id: str
    domain: str
    active: bool
    magnitude: float
    occurred_at: datetime
    stratum: CircadianStratum

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "variable_id": self.variable_id,
            "domain": self.domain,
            "active": self.active,
            "magnitude": round(self.magnitude, 4),
            "occurred_at": self.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stratum": self.stratum,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CausalEventRecord | None:
        try:
            ts_str = str(raw["occurred_at"])
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(UTC)
            var_id = str(raw["variable_id"])
            domain = _sanitize_var_part(str(raw.get("domain") or var_id.split(".")[0]), "system")
            stratum_raw = str(raw.get("stratum") or "")
            stratum: CircadianStratum = (
                stratum_raw if stratum_raw in _STRATA else _circadian_stratum(ts)  # type: ignore[assignment]
            )
            return cls(
                event_id=str(raw.get("event_id") or uuid.uuid4().hex[:16]),
                variable_id=var_id[:96],
                domain=domain,
                active=bool(raw.get("active", True)),
                magnitude=_clamp(float(raw.get("magnitude", 1.0))),
                occurred_at=ts,
                stratum=stratum,
            )
        except Exception:
            return None


_HUMAN_LABELS_NL: dict[str, str] = {
    "network.wan_latency_spike": "piek in lokale netwerk-/WAN-latentie",
    "network.wifi_rf_burst": "plotselinge Wi-Fi/Zigbee RF-signaalrimpel",
    "network.device_unreachable": "onbereikbaar lokaal netwerkapparaat",
    "provider.http_429_rate_limit": "model-provider HTTP 429 quotumlimiet",
    "provider.http_503_unavailable": "model-provider HTTP 503 storing",
    "provider.failover_recovered": "automatische overschakeling naar gezonde backup-provider",
    "presence.room_transition": "kamerwissel in het Spatial Presence Field",
    "presence.evening_wind_down": "avondrust in de woonkamer",
    "energy.power_draw_spike": "stroompiek in huis",
    "household.unresolved_tension": "oplopende openstaande taakspanning",
    "household.task_delay": "vertraging in achtergrondtaak",
    "routine.morning_rush": "ochtendspits in het gezin",
}


def _label_nl(variable_id: str) -> str:
    if variable_id in _HUMAN_LABELS_NL:
        return _HUMAN_LABELS_NL[variable_id]
    return variable_id.replace(".", " · ").replace("_", " ")


def _preventative_recommendation_nl(cause_id: str, effect_id: str, median_lag_s: float) -> str:
    lag_txt = f"{int(round(median_lag_s))}s" if median_lag_s < 120 else f"{max(1, int(round(median_lag_s / 60.0)))} min"
    if "429" in cause_id or "503" in cause_id or cause_id.startswith("provider."):
        return (
            f"Schakel bij eerste signaal van {_label_nl(cause_id)} direct een tijdelijke provider-cooldown in "
            f"zodat {_label_nl(effect_id)} (~{lag_txt} later) wordt voorkomen."
        )
    if cause_id.startswith("network."):
        return (
            f"Buffer lokale opdrachten en controleer LAN-route zodra {_label_nl(cause_id)} optreedt, "
            f"vóórdat dit na ~{lag_txt} leidt tot {_label_nl(effect_id)}."
        )
    return (
        f"Bereid proactief ontlasting klaar zodra {_label_nl(cause_id)} wordt waargenomen, "
        f"zodat {_label_nl(effect_id)} (~{lag_txt} later) tegenfeitelijk uitblijft."
    )


class CausalWorldModel:
    """Deterministic Structural Causal Model & Counterfactual Dream Replay Engine."""

    def __init__(
        self,
        persist_path: Path | None = None,
        *,
        horizon_seconds: float = 900.0,
    ) -> None:
        self._persist_path = persist_path
        self._horizon_seconds = max(10.0, float(horizon_seconds))
        self._lock = threading.Lock()
        self._events: list[CausalEventRecord] = []
        self._last_insights: tuple[CounterfactualDreamInsight, ...] = ()
        self._load()

    def record_event(
        self,
        domain: str,
        variable: str,
        *,
        active: bool = True,
        magnitude: float = 1.0,
        occurred_at: datetime | None = None,
    ) -> CausalEventRecord:
        ts = (occurred_at or datetime.now(UTC)).astimezone(UTC)
        dom = _sanitize_var_part(domain, "system")
        var = _sanitize_var_part(variable, "event")
        var_id = f"{dom}.{var}"
        rec = CausalEventRecord(
            event_id=uuid.uuid4().hex[:16],
            variable_id=var_id,
            domain=dom,
            active=bool(active),
            magnitude=_clamp(float(magnitude)),
            occurred_at=ts,
            stratum=_circadian_stratum(ts),
        )
        with self._lock:
            self._events.append(rec)
            self._events.sort(key=lambda e: e.occurred_at)
            if len(self._events) > MAX_CAUSAL_EVENTS:
                self._events = self._events[-MAX_CAUSAL_EVENTS:]
            self._save_locked()
        return rec

    def _estimate_edges_locked(self) -> tuple[list[CausalStructuralEdge], int]:
        events = [e for e in self._events if e.active]
        if len(events) < 4:
            return [], 0

        variables = sorted({e.variable_id for e in events})
        if len(variables) < 2:
            return [], 0

        horizon = self._horizon_seconds
        observed_days = sorted({e.occurred_at.date() for e in events})

        # Build observational units (events_in_unit, lookahead_events, confounder_stratum_key, unit_start_ts, unit_width_s):
        # - Multi-day histories stratify by exact hour-of-day across all observed days (Z = hour:00..23)
        #   so daily circadian coincidences (e.g. 08:00 coffee & 08:03 rush) are deconfounded without
        #   idle-hour dilution, preserving cross-midnight 23:xx -> 00:xx lookahead.
        # - Single-day histories stratify contiguous horizon slots (including quiet X=0 control slots)
        #   by circadian block so single-day histories satisfy positivity without background noise.
        units: list[tuple[list[CausalEventRecord], list[CausalEventRecord], str, float, float]] = []
        if len(observed_days) >= 2:
            active_hours = sorted({e.occurred_at.hour for e in events})
            by_day_hour: dict[tuple[Any, int], list[CausalEventRecord]] = {}
            for ev in events:
                by_day_hour.setdefault((ev.occurred_at.date(), ev.occurred_at.hour), []).append(ev)
            for d in observed_days:
                for h in active_hours:
                    cur_evs = by_day_hour.get((d, h), [])
                    nxt_evs = (
                        by_day_hour.get((d, h + 1), [])
                        if h < 23
                        else by_day_hour.get((d + timedelta(days=1), 0), [])
                    )
                    unit_start_ts = datetime(d.year, d.month, d.day, h, 0, 0, tzinfo=UTC).timestamp()
                    units.append((cur_evs, nxt_evs, f"hour:{h:02d}", unit_start_ts, 3600.0))
        else:
            t_start = events[0].occurred_at.timestamp()
            t_end = events[-1].occurred_at.timestamp()
            total_span = max(horizon * 6.0, (t_end - t_start) + horizon * 2.0)
            slot_width = max(horizon, total_span / 256.0)
            num_slots = max(6, min(256, int(math.ceil(total_span / slot_width))))
            slot_events: list[list[CausalEventRecord]] = [[] for _ in range(num_slots)]
            for ev in events:
                idx = max(0, min(num_slots - 1, int((ev.occurred_at.timestamp() - t_start) // slot_width)))
                slot_events[idx].append(ev)
            for k in range(num_slots):
                slot_start_ts = t_start + k * slot_width
                mid_ts = datetime.fromtimestamp(slot_start_ts + 0.5 * slot_width, tz=UTC)
                nxt = slot_events[k + 1] if k + 1 < num_slots else []
                units.append((slot_events[k], nxt, _circadian_stratum(mid_ts), slot_start_ts, slot_width))

        if len(units) < 3:
            return [], 0

        stratum_counts: dict[str, int] = {}
        for _, _, z, _, _ in units:
            stratum_counts[z] = stratum_counts.get(z, 0) + 1
        strata_keys = sorted(stratum_counts.keys())

        verified_edges: list[CausalStructuralEdge] = []
        rejected_spurious = 0

        for cause_id in variables:
            for effect_id in variables:
                if cause_id == effect_id:
                    continue

                # Per-stratum contingency counts: (n_x1, n_x1_y1, n_x0, n_x0_y1)
                counts: dict[str, list[int]] = {z: [0, 0, 0, 0] for z in strata_keys}
                lags: list[float] = []

                for evs, next_evs, z, unit_start_ts, unit_width_s in units:
                    eff_horizon = min(horizon, unit_width_s)
                    x_times = sorted(
                        e.occurred_at.timestamp() for e in evs if e.variable_id == cause_id
                    )
                    all_y_times = [
                        e.occurred_at.timestamp()
                        for e in (*evs, *next_evs)
                        if e.variable_id == effect_id
                    ]
                    if x_times:
                        matched_lags = [
                            t_y - t_x
                            for t_x in x_times
                            for t_y in all_y_times
                            if 0.0 < t_y - t_x <= eff_horizon
                        ]
                        counts[z][0] += 1
                        if matched_lags:
                            counts[z][1] += 1
                            lags.append(min(matched_lags))
                    else:
                        # Symmetric exposure window of exact width eff_horizon under X=0
                        y_in_control_window = any(
                            unit_start_ts <= t_y <= unit_start_ts + eff_horizon
                            for t_y in all_y_times
                        )
                        counts[z][2] += 1
                        if y_in_control_window:
                            counts[z][3] += 1

                total_x1 = sum(c[0] for c in counts.values())
                total_x1_y1 = sum(c[1] for c in counts.values())
                total_x0 = sum(c[2] for c in counts.values())
                total_x0_y1 = sum(c[3] for c in counts.values())

                if total_x1 < 2 or total_x1_y1 < 2 or total_x0 < 1:
                    continue

                raw_p1 = (total_x1_y1 + 0.5) / (total_x1 + 1.0)
                raw_p0 = (total_x0_y1 + 0.5) / (total_x0 + 1.0)
                raw_lift = _clamp(raw_p1 - raw_p0, -1.0, 1.0)

                # Pearl backdoor adjustment over observed strata Z where positivity holds (n1 > 0 and n0 > 0)
                comparable_strata = [
                    z for z in strata_keys if counts[z][0] > 0 and counts[z][2] > 0
                ]
                if comparable_strata:
                    comp_total = sum(stratum_counts[z] for z in comparable_strata)
                    adj_p1 = 0.0
                    adj_p0 = 0.0
                    for z in comparable_strata:
                        wz = stratum_counts[z] / comp_total
                        n1, y1, n0, y0 = counts[z]
                        p1_z = (y1 + 0.25) / (n1 + 0.5)
                        p0_z = (y0 + 0.25) / (n0 + 0.5)
                        adj_p1 += wz * p1_z
                        adj_p0 += wz * p0_z
                    backdoor_ace = _clamp(adj_p1 - adj_p0, -1.0, 1.0)
                else:
                    # Positivity violation: X=1 and X=0 were never both observed in any same stratum Z,
                    # so X cannot be separated from circadian confounder Z.
                    adj_p1 = raw_p1
                    adj_p0 = raw_p0
                    backdoor_ace = 0.0

                if raw_lift >= 0.25 and backdoor_ace < 0.14:
                    rejected_spurious += 1
                    continue

                if backdoor_ace < 0.18:
                    continue

                # Monotonic interventional Tian-Pearl bounds (Y_{1}(u) >= Y_{0}(u) + backdoor sufficiency)
                pn = _clamp((adj_p1 - adj_p0) / max(adj_p1, 1e-6), 0.0, 1.0)
                ps = _clamp((adj_p1 - adj_p0) / max(1.0 - adj_p0, 1e-6), 0.0, 1.0)
                med_lag = round(statistics.median(lags), 2) if lags else 0.0
                support_factor = 1.0 - math.exp(-total_x1_y1 / 3.0)
                conf = _clamp(round(0.35 * support_factor + 0.65 * backdoor_ace, 4), 0.0, 0.99)

                verified_edges.append(
                    CausalStructuralEdge(
                        cause_id=cause_id,
                        effect_id=effect_id,
                        backdoor_ace=round(backdoor_ace, 4),
                        raw_association_lift=round(raw_lift, 4),
                        probability_of_necessity=round(pn, 4),
                        probability_of_sufficiency=round(ps, 4),
                        median_lag_seconds=med_lag,
                        support_count=total_x1_y1,
                        confidence=conf,
                        spurious_confounded=False,
                    )
                )

        verified_edges.sort(
            key=lambda edge: (edge.backdoor_ace * edge.confidence, edge.support_count),
            reverse=True,
        )
        return verified_edges[:16], rejected_spurious

    def run_counterfactual_dream_replay(self) -> CausalWorldGraphSnapshot:
        """Replay historical timelines counterfactually Y_{do(X=0)}(u) during sleep consolidation."""
        with self._lock:
            edges, rejected_spurious = self._estimate_edges_locked()
            insights: list[CounterfactualDreamInsight] = []
            for edge in edges[:6]:
                if edge.backdoor_ace < 0.20 or edge.probability_of_necessity < 0.25:
                    continue
                digest = hashlib.sha256(f"{edge.cause_id}->{edge.effect_id}".encode("utf-8")).hexdigest()[:12]
                lag_s = edge.median_lag_seconds
                question_nl = (
                    f"Wat als {_label_nl(edge.cause_id)} tegenfeitelijk was voorkomen (do(X=0))? "
                    f"Dan daalt de kans op {_label_nl(edge.effect_id)} met {int(round(edge.backdoor_ace * 100))}% "
                    f"(noodzakelijkheid PN={int(round(edge.probability_of_necessity * 100))}%, mediaan Δt={lag_s:.0f}s)."
                )
                insights.append(
                    CounterfactualDreamInsight(
                        insight_id=f"cf-{digest}",
                        root_cause_id=edge.cause_id,
                        target_effect_id=edge.effect_id,
                        backdoor_ace=edge.backdoor_ace,
                        probability_of_necessity=edge.probability_of_necessity,
                        median_lag_seconds=lag_s,
                        counterfactual_question_nl=question_nl,
                        preventative_action_nl=_preventative_recommendation_nl(
                            edge.cause_id, edge.effect_id, lag_s
                        ),
                        confidence=edge.confidence,
                    )
                )
            self._last_insights = tuple(insights)
            self._save_locked()
            return self._build_snapshot_locked(edges, rejected_spurious)

    def snapshot(self) -> CausalWorldGraphSnapshot:
        with self._lock:
            edges, rejected_spurious = self._estimate_edges_locked()
            return self._build_snapshot_locked(edges, rejected_spurious)

    def _build_snapshot_locked(
        self,
        edges: list[CausalStructuralEdge],
        rejected_spurious: int,
    ) -> CausalWorldGraphSnapshot:
        status = (
            f"Structureel Causaal Model actief · {len(self._events)} gebeurtenissen · "
            f"{len(edges)} bewezen oorzaak→gevolg relaties · "
            f"{rejected_spurious} schijncorrelaties uitgefilterd (0 LLM-tokens)"
        )
        return CausalWorldGraphSnapshot(
            total_events_recorded=len(self._events),
            verified_causal_edges=tuple(edges),
            rejected_spurious_edges=rejected_spurious,
            counterfactual_insights=self._last_insights,
            status_nl=status,
        )

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.is_file():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            loaded_events: list[CausalEventRecord] = []
            for item in data.get("events", []):
                if isinstance(item, dict):
                    parsed = CausalEventRecord.from_dict(item)
                    if parsed is not None:
                        loaded_events.append(parsed)
            loaded_events.sort(key=lambda e: e.occurred_at)
            self._events = loaded_events[-MAX_CAUSAL_EVENTS:]
            loaded_insights: list[CounterfactualDreamInsight] = []
            for item in data.get("last_insights", []):
                if isinstance(item, dict):
                    loaded_insights.append(CounterfactualDreamInsight(**item))
            self._last_insights = tuple(loaded_insights[:8])
        except Exception:
            pass

    def _save_locked(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            payload = {
                "contract": "EvaCausalWorldModelState/v1",
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "events": [e.to_dict() for e in self._events[-MAX_CAUSAL_EVENTS:]],
                "last_insights": [i.model_dump(mode="json") for i in self._last_insights],
            }
            raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            tmp = self._persist_path.with_name(f".{self._persist_path.name}.{uuid.uuid4().hex[:12]}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self._persist_path)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
        except Exception:
            pass


__all__ = [
    "CausalEventRecord",
    "CausalWorldModel",
    "CircadianStratum",
]
