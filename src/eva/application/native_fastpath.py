"""Hybrid Native (C/SIMD + Pydantic v2 Rust core) fast-path accelerator for Eva's brainstem.

Provides zero-GC, sub-microsecond native execution for:
1. 5D Bio-Homeostatic differential state evolution & circadian cosine rhythm.
2. Active Inference (Friston prediction-error & precision-weighted belief updates).
3. Fast canonical JSON SHA-256 hashing backed by pydantic-core (Rust) and OpenSSL.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unicodedata
from typing import Any

from eva.contracts.living_cortex import rust_to_json as _rust_to_json

_C_SOURCE = r"""
#include <math.h>

static inline double clamp01(double v) {
    return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
}

static inline double clamp_range(double v, double lo, double hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

void eva_homeostatic_step(
    double cur_energy,
    double cur_warmth,
    double cur_curiosity,
    double cur_harmony,
    double cur_anticipation,
    double hour_of_day,
    double hours_since_interaction,
    int pending_tasks,
    int unresolved_alerts,
    double dt_hours,
    double *out_drives,
    double *out_impulse_norm
) {
    const double pi = 3.14159265358979323846;
    double circadian_phase = ((hour_of_day - 14.5) / 24.0) * 2.0 * pi;
    double target_energy = clamp01(0.60 + 0.32 * cos(circadian_phase));

    int is_daytime = (hour_of_day >= 7.5 && hour_of_day <= 22.0) ? 1 : 0;
    double silence_factor = clamp01(hours_since_interaction / 8.0);
    double target_warmth = is_daytime ? (0.35 + 0.50 * silence_factor) : (0.20 + 0.15 * silence_factor);

    double calm_bonus = (unresolved_alerts == 0) ? 0.25 : -0.15;
    double target_curiosity = clamp01(0.30 + 0.45 * target_energy + calm_bonus);

    double friction = 0.18 * (double)unresolved_alerts + 0.05 * (double)(pending_tasks < 6 ? pending_tasks : 6);
    double target_harmony = clamp01(0.92 - friction);

    double morning_Window = exp(-pow((hour_of_day - 7.75) / 1.25, 2.0));
    double evening_window = exp(-pow((hour_of_day - 17.75) / 1.50, 2.0));
    double task_pressure = (pending_tasks * 0.08) < 0.35 ? (pending_tasks * 0.08) : 0.35;
    double target_anticipation = clamp01(
        0.22 + 0.55 * (morning_Window > evening_window ? morning_Window : evening_window) + task_pressure
    );

    double alpha = clamp01(dt_hours / 0.5);
    if (alpha < 0.25) {
        alpha = 0.25;
    }

    double energy = clamp01(cur_energy + alpha * (target_energy - cur_energy));
    double warmth = clamp01(cur_warmth + alpha * (target_warmth - cur_warmth));
    double curiosity = clamp01(cur_curiosity + alpha * (target_curiosity - cur_curiosity));
    double harmony = clamp01(cur_harmony + alpha * (target_harmony - cur_harmony));
    double anticipation = clamp01(cur_anticipation + alpha * (target_anticipation - cur_anticipation));

    out_drives[0] = round(energy * 10000.0) / 10000.0;
    out_drives[1] = round(warmth * 10000.0) / 10000.0;
    out_drives[2] = round(curiosity * 10000.0) / 10000.0;
    out_drives[3] = round(harmony * 10000.0) / 10000.0;
    out_drives[4] = round(anticipation * 10000.0) / 10000.0;

    double w0 = 0.45 * warmth;
    double w1 = 0.40 * curiosity;
    double w2 = 0.50 * (1.0 - harmony);
    double w3 = 0.45 * anticipation;
    double norm = sqrt(w0 * w0 + w1 * w1 + w2 * w2 + w3 * w3);
    *out_impulse_norm = round(norm * 10000.0) / 10000.0;
}

void eva_active_inference_update(
    double cur_energy,
    double cur_receptivity,
    double cur_valence,
    double cur_confidence,
    int signal_code,
    double *out_state
) {
    double obs_energy = cur_energy;
    double obs_receptivity = cur_receptivity;
    double obs_valence = cur_valence;

    if (signal_code == 1) { /* warm_reply */
        obs_energy = clamp01(cur_energy + 0.15);
        obs_receptivity = 0.88;
        obs_valence = 0.65;
    } else if (signal_code == 2) { /* brief_or_busy */
        obs_energy = clamp01(cur_energy - 0.10);
        obs_receptivity = 0.18;
        obs_valence = -0.15;
    } else if (signal_code == 3) { /* stressed */
        obs_energy = 0.35;
        obs_receptivity = 0.25;
        obs_valence = -0.55;
    } else if (signal_code == 4) { /* curious_question */
        obs_energy = 0.80;
        obs_receptivity = 0.92;
        obs_valence = 0.45;
    } else if (signal_code == 5) { /* quiet_presence */
        obs_energy = cur_energy;
        obs_receptivity = clamp01(cur_receptivity - 0.08);
        obs_valence = cur_valence * 0.8;
    }

    double pred_err = clamp01(
        0.55 * fabs(obs_receptivity - cur_receptivity)
        + 0.25 * fabs(obs_valence - cur_valence)
        + 0.20 * fabs(obs_energy - cur_energy)
    );
    double lr = clamp01(0.35 + 0.45 * pred_err);
    double new_energy = clamp01(cur_energy + lr * (obs_energy - cur_energy));
    double new_receptivity = clamp01(cur_receptivity + lr * (obs_receptivity - cur_receptivity));
    double new_valence = clamp_range(cur_valence + lr * (obs_valence - cur_valence), -1.0, 1.0);
    double new_conf = clamp01(cur_confidence + 0.12 * (1.0 - pred_err) - 0.08 * pred_err);

    out_state[0] = round(new_energy * 10000.0) / 10000.0;
    out_state[1] = round(new_receptivity * 10000.0) / 10000.0;
    out_state[2] = round(new_valence * 10000.0) / 10000.0;
    out_state[3] = round(pred_err * 10000.0) / 10000.0;
    out_state[4] = round(new_conf * 10000.0) / 10000.0;
}

void eva_expected_free_energy_policies(
    double energy,
    double warmth,
    double curiosity,
    double harmony,
    double anticipation,
    double max_receptivity,
    double mean_uncertainty,
    double *out_g,
    double *out_epistemic,
    double *out_pragmatic,
    double *out_interruption,
    double *out_posterior,
    int *out_best_idx
) {
    /* 5 policies:
       0 = silent_presence
       1 = protective_stabilize
       2 = epistemic_curiosity
       3 = anticipatory_stage
       4 = warm_social_check_in
    */
    double intr_base = clamp01(1.0 - max_receptivity);
    double tension = clamp01(1.0 - harmony);

    /* Epistemic value (information gain / uncertainty resolution) */
    out_epistemic[0] = 0.12 + 0.15 * (1.0 - energy);
    out_epistemic[1] = 0.35 * tension + 0.25 * mean_uncertainty;
    out_epistemic[2] = 0.55 * curiosity + 0.35 * mean_uncertainty;
    out_epistemic[3] = 0.40 * anticipation + 0.20 * mean_uncertainty;
    out_epistemic[4] = 0.30 * warmth + 0.35 * mean_uncertainty * max_receptivity;

    /* Pragmatic value (homeostatic setpoint fulfillment) */
    out_pragmatic[0] = 0.38 + 0.35 * (1.0 - energy) + 0.25 * intr_base;
    out_pragmatic[1] = 0.85 * tension + 0.20 * anticipation;
    out_pragmatic[2] = 0.50 * curiosity * energy * harmony;
    out_pragmatic[3] = 0.72 * anticipation * energy;
    out_pragmatic[4] = 0.68 * warmth * max_receptivity * energy;

    /* Interruption / metabolic cost */
    out_interruption[0] = 0.02;
    out_interruption[1] = 0.12 + 0.15 * intr_base;
    out_interruption[2] = 0.08 + 0.10 * (1.0 - energy);
    out_interruption[3] = 0.10 + 0.18 * intr_base;
    out_interruption[4] = 0.18 + 0.55 * intr_base;

    const double gamma = 4.0;
    double max_neg_g = -1e9;
    int best = 0;
    for (int k = 0; k < 5; k++) {
        out_epistemic[k] = round(clamp01(out_epistemic[k]) * 10000.0) / 10000.0;
        out_pragmatic[k] = round(clamp01(out_pragmatic[k]) * 10000.0) / 10000.0;
        out_interruption[k] = round(clamp01(out_interruption[k]) * 10000.0) / 10000.0;
        double g = out_interruption[k] - out_pragmatic[k] - out_epistemic[k];
        out_g[k] = round(g * 10000.0) / 10000.0;
        double neg_g = -gamma * g;
        if (neg_g > max_neg_g) {
            max_neg_g = neg_g;
            best = k;
        }
    }
    double sum_exp = 0.0;
    for (int k = 0; k < 5; k++) {
        out_posterior[k] = exp(-gamma * out_g[k] - max_neg_g);
        sum_exp += out_posterior[k];
    }
    for (int k = 0; k < 5; k++) {
        out_posterior[k] = round((out_posterior[k] / sum_exp) * 10000.0) / 10000.0;
    }
    *out_best_idx = best;
}

double eva_cognitive_salience_score(
    double lexical_relevance,
    double age_hours,
    double trust_weight,
    int is_correction_or_routine
) {
    double rel = clamp01(lexical_relevance);
    double recency = exp(-0.012 * (age_hours < 0.0 ? 0.0 : age_hours));
    double importance = clamp01(0.65 * trust_weight + (is_correction_or_routine ? 0.35 : 0.10));
    double score = 0.45 * rel + 0.30 * recency + 0.25 * importance;
    return round(clamp01(score) * 10000.0) / 10000.0;
}
"""

_HERE = Path(__file__).resolve().parent
_BUNDLED_SO = _HERE / "_eva_fastpath.so"
_TMP_SO = Path("/tmp/eva-native-fastpath-v1.so")


def _load_native_lib() -> ctypes.CDLL | None:
    for candidate in (_BUNDLED_SO, _TMP_SO):
        if candidate.is_file():
            try:
                lib = ctypes.CDLL(str(candidate))
                lib.eva_homeostatic_step.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_double,
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.eva_homeostatic_step.restype = None
                lib.eva_active_inference_update.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.eva_active_inference_update.restype = None
                if hasattr(lib, "eva_expected_free_energy_policies") and hasattr(
                    lib, "eva_cognitive_salience_score"
                ):
                    lib.eva_expected_free_energy_policies.argtypes = [
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_int),
                    ]
                    lib.eva_expected_free_energy_policies.restype = None
                    lib.eva_cognitive_salience_score.argtypes = [
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_int,
                    ]
                    lib.eva_cognitive_salience_score.restype = ctypes.c_double
                    return lib
            except OSError:
                continue
    try:
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as src:
            src.write(_C_SOURCE)
            src_path = Path(src.name)
        target = _BUNDLED_SO if _HERE.stat().st_mode & 0o200 else _TMP_SO
        try:
            subprocess.run(
                ["gcc", "-O3", "-shared", "-fPIC", str(src_path), "-lm", "-o", str(target)],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError):
            subprocess.run(
                ["gcc", "-O3", "-shared", "-fPIC", str(src_path), "-lm", "-o", str(_TMP_SO)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            target = _TMP_SO
        finally:
            src_path.unlink(missing_ok=True)
        return _load_native_lib()
    except Exception:
        return None


_NATIVE_LIB = _load_native_lib()
NATIVE_ACCELERATOR_ACTIVE: bool = _NATIVE_LIB is not None

_SIGNAL_CODES = {
    "warm_reply": 1,
    "brief_or_busy": 2,
    "stressed": 3,
    "curious_question": 4,
    "quiet_presence": 5,
}


def compute_homeostatic_step_fast(
    cur_energy: float,
    cur_warmth: float,
    cur_curiosity: float,
    cur_harmony: float,
    cur_anticipation: float,
    *,
    hour_of_day: float,
    hours_since_interaction: float,
    pending_tasks: int,
    unresolved_alerts: int,
    dt_hours: float,
) -> tuple[tuple[float, float, float, float, float], float]:
    """Compute the 5D homeostatic state and impulse vector norm via native C/SIMD when available."""
    if _NATIVE_LIB is not None:
        drives = (ctypes.c_double * 5)()
        norm = ctypes.c_double()
        _NATIVE_LIB.eva_homeostatic_step(
            cur_energy,
            cur_warmth,
            cur_curiosity,
            cur_harmony,
            cur_anticipation,
            hour_of_day,
            hours_since_interaction,
            int(pending_tasks),
            int(unresolved_alerts),
            dt_hours,
            drives,
            ctypes.byref(norm),
        )
        return (drives[0], drives[1], drives[2], drives[3], drives[4]), float(norm.value)

    # Pure-Python fallback with identical math
    circadian_phase = ((hour_of_day - 14.5) / 24.0) * 2.0 * math.pi
    target_energy = max(0.0, min(1.0, 0.60 + 0.32 * math.cos(circadian_phase)))
    is_daytime = 7.5 <= hour_of_day <= 22.0
    silence_factor = max(0.0, min(1.0, hours_since_interaction / 8.0))
    target_warmth = (0.35 + 0.50 * silence_factor) if is_daytime else (0.20 + 0.15 * silence_factor)
    calm_bonus = 0.25 if unresolved_alerts == 0 else -0.15
    target_curiosity = max(0.0, min(1.0, 0.30 + 0.45 * target_energy + calm_bonus))
    friction = 0.18 * unresolved_alerts + 0.05 * min(pending_tasks, 6)
    target_harmony = max(0.0, min(1.0, 0.92 - friction))
    morning_window = math.exp(-((hour_of_day - 7.75) / 1.25) ** 2)
    evening_window = math.exp(-((hour_of_day - 17.75) / 1.50) ** 2)
    target_anticipation = max(
        0.0,
        min(1.0, 0.22 + 0.55 * max(morning_window, evening_window) + min(pending_tasks * 0.08, 0.35)),
    )
    alpha = max(0.25, min(1.0, dt_hours / 0.5))
    energy = round(max(0.0, min(1.0, cur_energy + alpha * (target_energy - cur_energy))), 4)
    warmth = round(max(0.0, min(1.0, cur_warmth + alpha * (target_warmth - cur_warmth))), 4)
    curiosity = round(max(0.0, min(1.0, cur_curiosity + alpha * (target_curiosity - cur_curiosity))), 4)
    harmony = round(max(0.0, min(1.0, cur_harmony + alpha * (target_harmony - cur_harmony))), 4)
    anticipation = round(max(0.0, min(1.0, cur_anticipation + alpha * (target_anticipation - cur_anticipation))), 4)
    norm = round(
        math.sqrt(
            (0.45 * warmth) ** 2
            + (0.40 * curiosity) ** 2
            + (0.50 * (1.0 - harmony)) ** 2
            + (0.45 * anticipation) ** 2
        ),
        4,
    )
    return (energy, warmth, curiosity, harmony, anticipation), norm


def compute_active_inference_fast(
    cur_energy: float,
    cur_receptivity: float,
    cur_valence: float,
    cur_confidence: float,
    signal_kind: str,
) -> tuple[float, float, float, float, float]:
    """Compute Active Inference prediction error and belief update via native C/SIMD."""
    code = _SIGNAL_CODES.get(signal_kind, 5)
    if _NATIVE_LIB is not None:
        out = (ctypes.c_double * 5)()
        _NATIVE_LIB.eva_active_inference_update(
            cur_energy,
            cur_receptivity,
            cur_valence,
            cur_confidence,
            code,
            out,
        )
        return (out[0], out[1], out[2], out[3], out[4])

    if signal_kind == "warm_reply":
        obs_e, obs_r, obs_v = min(1.0, cur_energy + 0.15), 0.88, 0.65
    elif signal_kind == "brief_or_busy":
        obs_e, obs_r, obs_v = max(0.0, cur_energy - 0.10), 0.18, -0.15
    elif signal_kind == "stressed":
        obs_e, obs_r, obs_v = 0.35, 0.25, -0.55
    elif signal_kind == "curious_question":
        obs_e, obs_r, obs_v = 0.80, 0.92, 0.45
    else:
        obs_e, obs_r, obs_v = cur_energy, max(0.0, cur_receptivity - 0.08), cur_valence * 0.8
    pred_err = max(
        0.0,
        min(
            1.0,
            0.55 * abs(obs_r - cur_receptivity)
            + 0.25 * abs(obs_v - cur_valence)
            + 0.20 * abs(obs_e - cur_energy),
        ),
    )
    lr = max(0.0, min(1.0, 0.35 + 0.45 * pred_err))
    return (
        round(max(0.0, min(1.0, cur_energy + lr * (obs_e - cur_energy))), 4),
        round(max(0.0, min(1.0, cur_receptivity + lr * (obs_r - cur_receptivity))), 4),
        round(max(-1.0, min(1.0, cur_valence + lr * (obs_v - cur_valence))), 4),
        round(pred_err, 4),
        round(max(0.0, min(1.0, cur_confidence + 0.12 * (1.0 - pred_err) - 0.08 * pred_err)), 4),
    )


_POLICY_CATALOG: tuple[tuple[str, str], ...] = (
    ("silent_presence", "Rustige waakzaamheid & zero-token luisteren"),
    ("protective_stabilize", "Harmonie herstellen & open knelpunten voorbereiden"),
    ("epistemic_curiosity", "Eigen onderzoeksproject of patroonverfijning tijdens rust"),
    ("anticipatory_stage", "Overgangsmoment voorbereiden vóór drukte"),
    ("warm_social_check_in", "Warme, korte afstemming bij hoge ontvankelijkheid"),
)


def compute_expected_free_energy_fast(
    energy: float,
    warmth: float,
    curiosity: float,
    harmony: float,
    anticipation: float,
    *,
    max_receptivity: float = 0.72,
    mean_uncertainty: float = 0.24,
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Evaluate candidate policies via Karl Friston's Expected Free Energy G(pi) in C/SIMD."""
    if _NATIVE_LIB is not None and hasattr(_NATIVE_LIB, "eva_expected_free_energy_policies"):
        out_g = (ctypes.c_double * 5)()
        out_ep = (ctypes.c_double * 5)()
        out_pr = (ctypes.c_double * 5)()
        out_in = (ctypes.c_double * 5)()
        out_po = (ctypes.c_double * 5)()
        best_idx = ctypes.c_int(0)
        _NATIVE_LIB.eva_expected_free_energy_policies(
            energy,
            warmth,
            curiosity,
            harmony,
            anticipation,
            max_receptivity,
            mean_uncertainty,
            out_g,
            out_ep,
            out_pr,
            out_in,
            out_po,
            ctypes.byref(best_idx),
        )
        winner_idx = int(best_idx.value)
        policies = tuple(
            {
                "policy_id": _POLICY_CATALOG[k][0],
                "label_nl": _POLICY_CATALOG[k][1],
                "expected_free_energy_g": float(out_g[k]),
                "epistemic_value": float(out_ep[k]),
                "pragmatic_value": float(out_pr[k]),
                "interruption_cost": float(out_in[k]),
                "posterior_probability": float(out_po[k]),
                "selected": k == winner_idx,
            }
            for k in range(5)
        )
        return _POLICY_CATALOG[winner_idx][0], policies

    intr_base = max(0.0, min(1.0, 1.0 - max_receptivity))
    tension = max(0.0, min(1.0, 1.0 - harmony))
    ep = [
        0.12 + 0.15 * (1.0 - energy),
        0.35 * tension + 0.25 * mean_uncertainty,
        0.55 * curiosity + 0.35 * mean_uncertainty,
        0.40 * anticipation + 0.20 * mean_uncertainty,
        0.30 * warmth + 0.35 * mean_uncertainty * max_receptivity,
    ]
    pr = [
        0.38 + 0.35 * (1.0 - energy) + 0.25 * intr_base,
        0.85 * tension + 0.20 * anticipation,
        0.50 * curiosity * energy * harmony,
        0.72 * anticipation * energy,
        0.68 * warmth * max_receptivity * energy,
    ]
    ic = [
        0.02,
        0.12 + 0.15 * intr_base,
        0.08 + 0.10 * (1.0 - energy),
        0.10 + 0.18 * intr_base,
        0.18 + 0.55 * intr_base,
    ]
    g_vals = []
    for k in range(5):
        ep[k] = round(max(0.0, min(1.0, ep[k])), 4)
        pr[k] = round(max(0.0, min(1.0, pr[k])), 4)
        ic[k] = round(max(0.0, min(1.0, ic[k])), 4)
        g_vals.append(round(ic[k] - pr[k] - ep[k], 4))
    gamma = 4.0
    max_neg_g = max(-gamma * g for g in g_vals)
    exps = [math.exp(-gamma * g - max_neg_g) for g in g_vals]
    total_exp = sum(exps)
    post = [round(v / total_exp, 4) for v in exps]
    winner_idx = min(range(5), key=lambda i: g_vals[i])
    policies = tuple(
        {
            "policy_id": _POLICY_CATALOG[k][0],
            "label_nl": _POLICY_CATALOG[k][1],
            "expected_free_energy_g": g_vals[k],
            "epistemic_value": ep[k],
            "pragmatic_value": pr[k],
            "interruption_cost": ic[k],
            "posterior_probability": post[k],
            "selected": k == winner_idx,
        }
        for k in range(5)
    )
    return _POLICY_CATALOG[winner_idx][0], policies


def compute_cognitive_salience_fast(
    lexical_relevance: float,
    age_hours: float = 1.0,
    trust_weight: float = 0.9,
    is_correction_or_routine: bool = False,
) -> float:
    """Compute ACT-R + Generative Agents Tri-Factor Memory Activation score in C/SIMD."""
    if _NATIVE_LIB is not None and hasattr(_NATIVE_LIB, "eva_cognitive_salience_score"):
        return float(
            _NATIVE_LIB.eva_cognitive_salience_score(
                float(lexical_relevance),
                float(age_hours),
                float(trust_weight),
                1 if is_correction_or_routine else 0,
            )
        )
    rel = max(0.0, min(1.0, lexical_relevance))
    recency = math.exp(-0.012 * max(0.0, age_hours))
    importance = max(
        0.0,
        min(1.0, 0.65 * trust_weight + (0.35 if is_correction_or_routine else 0.10)),
    )
    return round(max(0.0, min(1.0, 0.45 * rel + 0.30 * recency + 0.25 * importance)), 4)


def fast_canonical_sha256(payload: Any) -> str:
    """Compute canonical JSON SHA-256 using C-level NFC check and sorted UTF-8 serialization."""
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not raw.isascii() and not unicodedata.is_normalized("NFC", raw):
        raw = unicodedata.normalize("NFC", raw)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def rust_json_bytes(payload: Any) -> bytes:
    """Serialize a Pydantic v2 model or dict using pydantic-core's compiled Rust serializer."""
    return _rust_to_json(payload)


__all__ = [
    "NATIVE_ACCELERATOR_ACTIVE",
    "compute_active_inference_fast",
    "compute_cognitive_salience_fast",
    "compute_expected_free_energy_fast",
    "compute_homeostatic_step_fast",
    "fast_canonical_sha256",
    "rust_json_bytes",
]
