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
    if (!isfinite(v)) return 0.0;
    return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
}

static inline double clamp_range(double v, double lo, double hi) {
    if (!isfinite(v)) return lo;
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

/* JEPA-inspired Latent World Model: simulates 2-step causal state transitions
   z_t = [energy, receptivity, cognitive_load, task_urgency, friction, trust]
   across 5 candidate actions:
   0 = observe_silently
   1 = warm_human_presence
   2 = stage_action_jarvis
   3 = direct_epistemic_answer
   4 = sleep_dream_consolidate
*/
void eva_world_model_simulate(
    const double *z_in,
    double *out_utilities,
    double *out_next_z,
    int *out_best_action
) {
    double e = clamp01(z_in[0]);
    double r = clamp01(z_in[1]);
    double load = clamp01(z_in[2]);
    double urg = clamp01(z_in[3]);
    double fric = clamp01(z_in[4]);
    double tr = clamp01(z_in[5]);

    double d_harm[5], d_relief[5], shock[5];
    /* 0: observe_silently */
    d_harm[0] = 0.22 * load + 0.18 * (1.0 - r);
    d_relief[0] = 0.04;
    shock[0] = 0.0;

    /* 1: warm_human_presence */
    d_harm[1] = 0.48 * r * tr + 0.15 * (1.0 - urg);
    d_relief[1] = 0.14 * (1.0 - load);
    shock[1] = 0.12 * load + 0.15 * (1.0 - r);

    /* 2: stage_action_jarvis (proactively prepare in background, minimal interruption) */
    d_harm[2] = 0.35 * tr + 0.30 * fric;
    d_relief[2] = 0.78 * urg + 0.42 * fric;
    shock[2] = 0.08 + 0.14 * (1.0 - r);

    /* 3: direct_epistemic_answer */
    d_harm[3] = 0.32 * r + 0.20 * tr;
    d_relief[3] = 0.62 * urg + 0.25 * (1.0 - fric);
    shock[3] = 0.10 + 0.16 * load * (1.0 - urg);

    /* 4: sleep_dream_consolidate */
    d_harm[4] = 0.42 * (1.0 - e) + 0.28 * fric;
    d_relief[4] = 0.36 * fric * (1.0 - urg);
    shock[4] = 0.01 + 0.35 * urg;

    int best = 0;
    double best_u = -1e9;
    for (int k = 0; k < 5; k++) {
        double u = (0.45 * d_harm[k] + 0.55 * d_relief[k]) - 0.65 * shock[k];
        out_utilities[k] = round(u * 10000.0) / 10000.0;
        if (u > best_u) {
            best_u = u;
            best = k;
        }
    }
    *out_best_action = best;

    /* Roll forward 2 causal steps under winning action */
    double next_e = clamp01(e - (best == 2 ? 0.04 : 0.01) + (best == 4 ? 0.12 : 0.0));
    double next_r = clamp01(r + 0.18 * d_harm[best] - 0.25 * shock[best]);
    double next_load = clamp01(load - 0.30 * d_relief[best] + 0.15 * shock[best]);
    double next_urg = clamp01(urg - 0.55 * d_relief[best]);
    double next_fric = clamp01(fric - 0.40 * d_relief[best] - 0.20 * d_harm[best]);
    double next_tr = clamp01(tr + 0.06 * (d_harm[best] + d_relief[best]) - 0.10 * shock[best]);

    out_next_z[0] = round(next_e * 10000.0) / 10000.0;
    out_next_z[1] = round(next_r * 10000.0) / 10000.0;
    out_next_z[2] = round(next_load * 10000.0) / 10000.0;
    out_next_z[3] = round(next_urg * 10000.0) / 10000.0;
    out_next_z[4] = round(next_fric * 10000.0) / 10000.0;
    out_next_z[5] = round(next_tr * 10000.0) / 10000.0;
}

/* Titans / Liquid Test-Time Synaptic Plasticity (O(1) 8x8 fast-weights):
   M_t = (1 - alpha) * M_{t-1} + S_t
   S_t = eta * S_{t-1} - theta_t * grad_M(||M_{t-1} k_t - v_t||^2)
*/
void eva_titans_synaptic_step(
    double *M,
    double *S,
    const double *k_vec,
    const double *v_vec,
    double alpha,
    double eta,
    double base_lr,
    double *out_pred_v,
    double *out_surprise,
    double *out_frobenius_norm
) {
    double err[8];
    double mse = 0.0;
    for (int i = 0; i < 8; i++) {
        double pred = 0.0;
        for (int j = 0; j < 8; j++) {
            pred += M[i * 8 + j] * k_vec[j];
        }
        out_pred_v[i] = round(clamp01(pred) * 10000.0) / 10000.0;
        err[i] = pred - v_vec[i];
        mse += err[i] * err[i];
    }
    double surprise = mse / 8.0;
    if (surprise > 1.0) surprise = 1.0;
    *out_surprise = round(surprise * 10000.0) / 10000.0;

    /* Surprise-gated plasticity rate */
    double theta_t = base_lr * (0.25 + 1.75 * surprise);
    double frob_sq = 0.0;
    for (int i = 0; i < 8; i++) {
        for (int j = 0; j < 8; j++) {
            int idx = i * 8 + j;
            double grad = 2.0 * err[i] * k_vec[j];
            S[idx] = eta * S[idx] - theta_t * grad;
            double m_new = (1.0 - alpha) * M[idx] + S[idx];
            if (m_new > 2.0) m_new = 2.0;
            if (m_new < -2.0) m_new = -2.0;
            M[idx] = round(m_new * 100000.0) / 100000.0;
            frob_sq += M[idx] * M[idx];
        }
    }
    *out_frobenius_norm = round(sqrt(frob_sq) * 10000.0) / 10000.0;
}

/* Liquid Time-Constant (LTC) 6D Continuous Neural ODE with RK4 integration:
   x = [arousal, social_attunement, epistemic_drive, home_proprioception, temporal_anticipation, affective_coherence]
   dx_i/dt = -(1/tau0_i) * (x_i - base_i) + g_i(x, I) * (A_i - x_i)
   tau_eff_i(x, I) = 1.0 / ((1.0 / tau0_i) + g_i(x, I))
*/
static inline double ltc_sigmoid(double z) {
    if (z > 16.0) return 1.0;
    if (z < -16.0) return 0.0;
    return 1.0 / (1.0 + exp(-z));
}

static void ltc_rhs(
    const double *x,
    const double *base,
    const double *reversal_a,
    const double *tau0,
    const double *gain_w,
    double stimulus,
    double tension,
    double *dxdt,
    double *tau_eff_out
) {
    /* Lateral liquid coupling across the 6 neurons */
    const double lateral[6][6] = {
        { 0.00,  0.35,  0.25, -0.20,  0.30,  0.10},
        { 0.30,  0.00,  0.20,  0.25,  0.15,  0.35},
        { 0.25,  0.15,  0.00,  0.30,  0.20,  0.20},
        {-0.15,  0.20,  0.10,  0.00, -0.10,  0.45},
        { 0.35,  0.20,  0.25, -0.10,  0.00,  0.15},
        {-0.10,  0.30,  0.15,  0.40,  0.10,  0.00}
    };
    const double stim_drive[6] = {3.2, 2.8, 2.1, 2.4, 2.6, 1.6};
    const double bias[6] = {-1.35, -1.20, -1.40, -1.50, -1.30, -1.60};

    for (int i = 0; i < 6; i++) {
        double recurrent = bias[i] + stim_drive[i] * stimulus + (i == 3 || i == 0 ? 1.8 * tension : 0.4 * tension);
        for (int j = 0; j < 6; j++) {
            recurrent += lateral[i][j] * x[j];
        }
        double g = gain_w[i] * ltc_sigmoid(recurrent);
        double leak = 1.0 / tau0[i];
        dxdt[i] = -leak * (x[i] - base[i]) + g * (reversal_a[i] - x[i]);
        if (tau_eff_out != 0) {
            tau_eff_out[i] = 1.0 / (leak + g);
        }
    }
}

void eva_liquid_ode_step(
    const double *x_in,
    double dt_seconds,
    double hour_of_day,
    double stimulus_intensity,
    double house_activity,
    double unresolved_tension,
    double *x_out,
    double *tau_eff_out,
    double *out_phase_velocity,
    int *out_substeps
) {
    const double pi = 3.14159265358979323846;
    const double tau0[6] = {240.0, 900.0, 1800.0, 1200.0, 1500.0, 3600.0};
    const double gain_w[6] = {0.14, 0.08, 0.045, 0.06, 0.07, 0.03};

    double clean_stim_in = clamp01(stimulus_intensity);
    double clean_act = clamp01(house_activity);
    double stim = clamp01(0.75 * clean_stim_in + 0.35 * clean_act);
    double tens = clamp01(unresolved_tension);
    double dt = clamp_range(dt_seconds, 0.0, 21600.0);
    double hod = isfinite(hour_of_day) ? fmod(fmod(hour_of_day, 24.0) + 24.0, 24.0) : 12.0;

    double circ = cos(((hod - 14.5) / 24.0) * 2.0 * pi);
    int daytime = (hod >= 7.0 && hod <= 22.5) ? 1 : 0;
    double morning_peak = exp(-pow((hod - 7.75) / 1.4, 2.0));
    double evening_peak = exp(-pow((hod - 17.75) / 1.6, 2.0));
    double trans = morning_peak > evening_peak ? morning_peak : evening_peak;

    double base[6];
    base[0] = clamp01(0.42 + 0.28 * circ + 0.15 * clean_act);
    base[1] = daytime ? 0.58 : 0.28;
    base[2] = clamp01(0.45 + 0.22 * circ * (1.0 - tens));
    base[3] = clamp01(0.88 - 0.42 * tens);
    base[4] = clamp01(0.24 + 0.52 * trans + 0.15 * tens);
    base[5] = clamp01(0.84 - 0.25 * tens);

    double reversal_a[6];
    reversal_a[0] = clamp01(0.92 * stim + 0.35 * tens + 0.25);
    reversal_a[1] = clamp01(0.90 * stim + (daytime ? 0.45 : 0.25));
    reversal_a[2] = clamp01(0.82 - 0.30 * tens + 0.15 * stim);
    reversal_a[3] = clamp01(0.94 - 0.65 * tens);
    reversal_a[4] = clamp01(0.45 + 0.45 * trans + 0.30 * stim);
    reversal_a[5] = clamp01(0.90 - 0.35 * tens + 0.10 * stim);

    double x[6];
    for (int i = 0; i < 6; i++) {
        x[i] = isfinite(x_in[i]) ? clamp01(x_in[i]) : 0.60;
    }

    int steps = (int)ceil(dt / 12.0);
    if (steps < 1) steps = 1;
    if (steps > 1800) steps = 1800;
    double h = dt / (double)steps;

    double k1[6], k2[6], k3[6], k4[6], tmp[6];
    if (dt > 1e-6) {
        for (int s = 0; s < steps; s++) {
            ltc_rhs(x, base, reversal_a, tau0, gain_w, stim, tens, k1, 0);
            for (int i = 0; i < 6; i++) tmp[i] = clamp01(x[i] + 0.5 * h * k1[i]);
            ltc_rhs(tmp, base, reversal_a, tau0, gain_w, stim, tens, k2, 0);
            for (int i = 0; i < 6; i++) tmp[i] = clamp01(x[i] + 0.5 * h * k2[i]);
            ltc_rhs(tmp, base, reversal_a, tau0, gain_w, stim, tens, k3, 0);
            for (int i = 0; i < 6; i++) tmp[i] = clamp01(x[i] + h * k3[i]);
            ltc_rhs(tmp, base, reversal_a, tau0, gain_w, stim, tens, k4, 0);
            for (int i = 0; i < 6; i++) {
                x[i] = clamp01(x[i] + (h / 6.0) * (k1[i] + 2.0 * k2[i] + 2.0 * k3[i] + k4[i]));
            }
            /* Exponentially relax transient stimulus impulse over longer horizons */
            stim *= exp(-h / 45.0);
        }
    }

    double inst_dx[6];
    ltc_rhs(x, base, reversal_a, tau0, gain_w, clamp01(0.75 * clean_stim_in + 0.35 * clean_act), tens, inst_dx, tau_eff_out);

    double vel_sq = 0.0;
    for (int i = 0; i < 6; i++) {
        x_out[i] = round(clamp01(x[i]) * 10000.0) / 10000.0;
        tau_eff_out[i] = round(clamp_range(tau_eff_out[i], 1.0, 7200.0) * 100.0) / 100.0;
        vel_sq += inst_dx[i] * inst_dx[i];
    }
    *out_phase_velocity = round(sqrt(vel_sq) * 1000000.0) / 1000000.0;
    *out_substeps = steps;
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
                if (
                    hasattr(lib, "eva_expected_free_energy_policies")
                    and hasattr(lib, "eva_cognitive_salience_score")
                    and hasattr(lib, "eva_world_model_simulate")
                    and hasattr(lib, "eva_titans_synaptic_step")
                    and hasattr(lib, "eva_liquid_ode_step")
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
                    lib.eva_world_model_simulate.argtypes = [
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_int),
                    ]
                    lib.eva_world_model_simulate.restype = None
                    lib.eva_titans_synaptic_step.argtypes = [
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                    ]
                    lib.eva_titans_synaptic_step.restype = None
                    lib.eva_liquid_ode_step.argtypes = [
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.c_double,
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_double),
                        ctypes.POINTER(ctypes.c_int),
                    ]
                    lib.eva_liquid_ode_step.restype = None
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


_WORLD_MODEL_ACTIONS: tuple[tuple[str, str], ...] = (
    ("observe_silently", "Stil observeren & ruimte laten"),
    ("warm_human_presence", "Natuurlijk menselijk contact (geen taak-script)"),
    ("stage_action_jarvis", "Jarvis-actie stil klaarzetten voor 1-klik bevestiging"),
    ("direct_epistemic_answer", "Direct feitelijk antwoord uit geheugen & bronnen"),
    ("sleep_dream_consolidate", "Nachtelijke droom-consolidatie & synaptische ordening"),
)


def simulate_world_model_fast(
    energy: float,
    receptivity: float,
    cognitive_load: float,
    task_urgency: float,
    friction: float,
    trust: float = 0.88,
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, float]]:
    """Simulate 2-step JEPA-inspired causal latent trajectories across 5 candidate actions in C/SIMD."""
    z_vec = [
        max(0.0, min(1.0, float(energy))),
        max(0.0, min(1.0, float(receptivity))),
        max(0.0, min(1.0, float(cognitive_load))),
        max(0.0, min(1.0, float(task_urgency))),
        max(0.0, min(1.0, float(friction))),
        max(0.0, min(1.0, float(trust))),
    ]
    if _NATIVE_LIB is not None and hasattr(_NATIVE_LIB, "eva_world_model_simulate"):
        c_z = (ctypes.c_double * 6)(*z_vec)
        c_u = (ctypes.c_double * 5)()
        c_next = (ctypes.c_double * 6)()
        c_best = ctypes.c_int(0)
        _NATIVE_LIB.eva_world_model_simulate(c_z, c_u, c_next, ctypes.byref(c_best))
        best_idx = int(c_best.value)
        trajectories = tuple(
            {
                "action_id": _WORLD_MODEL_ACTIONS[k][0],
                "label_nl": _WORLD_MODEL_ACTIONS[k][1],
                "counterfactual_utility": float(c_u[k]),
                "selected": k == best_idx,
            }
            for k in range(5)
        )
        predicted_next = {
            "energy": float(c_next[0]),
            "receptivity": float(c_next[1]),
            "cognitive_load": float(c_next[2]),
            "task_urgency": float(c_next[3]),
            "friction": float(c_next[4]),
            "trust": float(c_next[5]),
        }
        return _WORLD_MODEL_ACTIONS[best_idx][0], trajectories, predicted_next

    e, r, load, urg, fric, tr = z_vec
    d_harm = [
        0.22 * load + 0.18 * (1.0 - r),
        0.48 * r * tr + 0.15 * (1.0 - urg),
        0.35 * tr + 0.30 * fric,
        0.32 * r + 0.20 * tr,
        0.42 * (1.0 - e) + 0.28 * fric,
    ]
    d_relief = [
        0.04,
        0.14 * (1.0 - load),
        0.78 * urg + 0.42 * fric,
        0.62 * urg + 0.25 * (1.0 - fric),
        0.36 * fric * (1.0 - urg),
    ]
    shock = [
        0.0,
        0.12 * load + 0.15 * (1.0 - r),
        0.08 + 0.14 * (1.0 - r),
        0.10 + 0.16 * load * (1.0 - urg),
        0.01 + 0.35 * urg,
    ]
    utilities = [
        round((0.45 * d_harm[k] + 0.55 * d_relief[k]) - 0.65 * shock[k], 4)
        for k in range(5)
    ]
    best_idx = max(range(5), key=lambda k: utilities[k])
    trajectories = tuple(
        {
            "action_id": _WORLD_MODEL_ACTIONS[k][0],
            "label_nl": _WORLD_MODEL_ACTIONS[k][1],
            "counterfactual_utility": utilities[k],
            "selected": k == best_idx,
        }
        for k in range(5)
    )
    predicted_next = {
        "energy": round(max(0.0, min(1.0, e - (0.04 if best_idx == 2 else 0.01) + (0.12 if best_idx == 4 else 0.0))), 4),
        "receptivity": round(max(0.0, min(1.0, r + 0.18 * d_harm[best_idx] - 0.25 * shock[best_idx])), 4),
        "cognitive_load": round(max(0.0, min(1.0, load - 0.30 * d_relief[best_idx] + 0.15 * shock[best_idx])), 4),
        "task_urgency": round(max(0.0, min(1.0, urg - 0.55 * d_relief[best_idx])), 4),
        "friction": round(max(0.0, min(1.0, fric - 0.40 * d_relief[best_idx] - 0.20 * d_harm[best_idx])), 4),
        "trust": round(max(0.0, min(1.0, tr + 0.06 * (d_harm[best_idx] + d_relief[best_idx]) - 0.10 * shock[best_idx])), 4),
    }
    return _WORLD_MODEL_ACTIONS[best_idx][0], trajectories, predicted_next


def update_titans_synaptic_memory_fast(
    weights_64: tuple[float, ...] | list[float],
    momentum_64: tuple[float, ...] | list[float],
    key_vec_8: tuple[float, ...] | list[float],
    value_vec_8: tuple[float, ...] | list[float],
    *,
    alpha: float = 0.04,
    eta: float = 0.85,
    base_lr: float = 0.12,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], float, float]:
    """Perform O(1) Titans surprise-gated fast-weight synaptic update on an 8x8 matrix in C/SIMD."""
    m_list = list(weights_64) if len(weights_64) == 64 else [0.125 if i % 9 == 0 else 0.0 for i in range(64)]
    s_list = list(momentum_64) if len(momentum_64) == 64 else [0.0] * 64
    k_list = [max(0.0, min(1.0, float(x))) for x in (list(key_vec_8) + [0.0] * 8)[:8]]
    v_list = [max(0.0, min(1.0, float(x))) for x in (list(value_vec_8) + [0.0] * 8)[:8]]

    if _NATIVE_LIB is not None and hasattr(_NATIVE_LIB, "eva_titans_synaptic_step"):
        c_m = (ctypes.c_double * 64)(*m_list)
        c_s = (ctypes.c_double * 64)(*s_list)
        c_k = (ctypes.c_double * 8)(*k_list)
        c_v = (ctypes.c_double * 8)(*v_list)
        c_pred = (ctypes.c_double * 8)()
        c_surp = ctypes.c_double(0.0)
        c_frob = ctypes.c_double(0.0)
        _NATIVE_LIB.eva_titans_synaptic_step(
            c_m,
            c_s,
            c_k,
            c_v,
            float(alpha),
            float(eta),
            float(base_lr),
            c_pred,
            ctypes.byref(c_surp),
            ctypes.byref(c_frob),
        )
        return (
            tuple(float(c_m[i]) for i in range(64)),
            tuple(float(c_s[i]) for i in range(64)),
            tuple(float(c_pred[i]) for i in range(8)),
            float(c_surp.value),
            float(c_frob.value),
        )

    pred = []
    err = []
    for i in range(8):
        p = sum(m_list[i * 8 + j] * k_list[j] for j in range(8))
        pred.append(round(max(0.0, min(1.0, p)), 4))
        err.append(p - v_list[i])
    surprise = round(min(1.0, sum(e * e for e in err) / 8.0), 4)
    theta_t = base_lr * (0.25 + 1.75 * surprise)
    frob_sq = 0.0
    for i in range(8):
        for j in range(8):
            idx = i * 8 + j
            grad = 2.0 * err[i] * k_list[j]
            s_list[idx] = eta * s_list[idx] - theta_t * grad
            m_new = max(-2.0, min(2.0, (1.0 - alpha) * m_list[idx] + s_list[idx]))
            m_list[idx] = round(m_new, 5)
            frob_sq += m_list[idx] * m_list[idx]
    return (
        tuple(m_list),
        tuple(s_list),
        tuple(pred),
        surprise,
        round(math.sqrt(frob_sq), 4),
    )


_CONCEPT_DIMENSIONS: tuple[str, ...] = (
    "social_greeting",
    "casual_sharing",
    "status_checkin",
    "epistemic_question",
    "actionable_delegation",
    "routine_schedule",
    "boundary_correction",
    "urgency_signal",
)


def compute_concept_vector_fast(text: str) -> dict[str, Any]:
    """Map an utterance in ANY language into an 8D semantic Concept Space + Neuro-Symbolic Guard."""
    clean = (text or "").strip()
    lower = clean.lower()
    # Extract Unicode word tokens across any script/language
    tokens = [
        tok.strip(".,!?;:\"'()[]{}¡¿")
        for tok in lower.split()
        if tok.strip(".,!?;:\"'()[]{}¡¿")
    ]
    n_tok = len(tokens)
    has_qmark = "?" in clean or "¿" in clean or "？" in clean
    has_digits = any(ch.isdigit() for ch in clean)

    # Relational wellbeing check-in patterns (multilingual structural + semantic)
    relational_checkin = (
        any(
            phrase in lower
            for phrase in (
                "hoe is het",
                "hoe gaat het",
                "alles goed",
                "how are you",
                "how's it going",
                "ca va",
                "ça va",
                "como estas",
                "cómo estás",
                "wie geht",
                "tutto bene",
            )
        )
        and n_tok <= 7
    )

    action_stems = (
        "plan", "zet", "maak", "stuur", "regel", "zoek", "check", "controleer",
        "adopteer", "pauzeer", "hervat", "rollback", "neem over", "schedule",
        "create", "send", "prepare", "book", "configure", "activeer", "klaarzetten",
    )
    epistemic_stems = (
        "wat", "waar", "wanneer", "waarom", "welke", "hoeveel", "wie", "weet",
        "herinner", "geheugen", "staat", "staan", "welk", "what", "where", "when",
        "why", "which", "who", "remember", "memory", "initiatie", "voorziening",
        "verantwoordelijk", "vooruitblik", "knelpunt", "project", "nieuwsgierig",
        "bron", "toegang", "allerg", "medic", "lola",
    )
    routine_stems = (
        "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag",
        "ochtend", "middag", "avond", "routine", "agenda", "afspraak", "eten", "koken",
        "menu", "week", "morgen", "vandaag", "uur",
    )
    boundary_stems = (
        "uitzondering", "niet", "geen", "stop", "anders", "laat maar", "hoeft niet",
        "vandaag niet", "loopt uit", "bestellen", "never", "don't", "cancel",
    )
    urgency_stems = (
        "spoed", "nu", "direct", "haast", "druk", "storing", "fout", "kapot",
        "urgent", "alert", "help", "meteen",
    )

    act_hits = sum(1 for s in action_stems if s in lower)
    epi_hits = sum(1 for s in epistemic_stems if s in lower)
    rou_hits = sum(1 for s in routine_stems if s in lower)
    bnd_hits = sum(1 for s in boundary_stems if s in lower)
    urg_hits = sum(1 for s in urgency_stems if s in lower)

    if relational_checkin:
        v_greeting = 0.45
        v_sharing = 0.20
        v_checkin = 0.94
        v_epistemic = 0.08
        v_action = 0.04
    elif n_tok <= 3 and not has_qmark and not has_digits and act_hits == 0 and epi_hits == 0 and rou_hits == 0 and bnd_hits == 0:
        # Language-agnostic short greeting/acknowledgment ("Hi", "Bonjour!", "Hola", "Shalom", "Hej Eva", "Goedemorgen")
        v_greeting = 0.95 if n_tok > 0 else 0.50
        v_sharing = 0.25
        v_checkin = 0.18
        v_epistemic = 0.04
        v_action = 0.02
    else:
        v_greeting = 0.25 if n_tok <= 4 and not has_qmark and act_hits == 0 and epi_hits == 0 else 0.05
        v_checkin = 0.15 if has_qmark and n_tok <= 5 and epi_hits == 0 else 0.05
        v_epistemic = min(1.0, (0.52 if has_qmark else 0.08) + 0.28 * epi_hits)
        v_action = min(1.0, 0.36 * act_hits + (0.18 if has_digits and act_hits > 0 else 0.0))
        v_sharing = (
            max(0.15, 0.86 - 0.65 * max(v_epistemic, v_action))
            if not has_qmark and act_hits == 0 and epi_hits == 0
            else 0.18
        )

    v_routine = min(1.0, 0.34 * rou_hits + (0.15 if has_digits else 0.0))
    v_boundary = min(1.0, 0.38 * bnd_hits)
    v_urgency = min(1.0, 0.42 * urg_hits + (0.12 if "!" in clean and urg_hits > 0 else 0.0))

    vec = tuple(
        round(max(0.0, min(1.0, x)), 4)
        for x in (
            v_greeting,
            v_sharing,
            v_checkin,
            v_epistemic,
            v_action,
            v_routine,
            v_boundary,
            v_urgency,
        )
    )
    dims = {name: vec[i] for i, name in enumerate(_CONCEPT_DIMENSIONS)}
    dominant_idx = max(range(8), key=lambda i: vec[i])
    dominant_concept = _CONCEPT_DIMENSIONS[dominant_idx]
    is_non_task_social = (
        dominant_concept in ("social_greeting", "casual_sharing", "status_checkin")
        and dims["epistemic_question"] < 0.40
        and dims["actionable_delegation"] < 0.35
        and dims["routine_schedule"] < 0.45
        and dims["boundary_correction"] < 0.45
    )
    requires_memory_lookup = (
        not is_non_task_social
        and (
            dims["epistemic_question"] >= 0.40
            or dims["actionable_delegation"] >= 0.36
            or dims["routine_schedule"] >= 0.34
            or dims["boundary_correction"] >= 0.38
        )
    )
    requires_verified_receipt = dims["actionable_delegation"] >= 0.50
    return {
        "vector": vec,
        "dimensions": dims,
        "dominant_concept": dominant_concept,
        "is_non_task_social": is_non_task_social,
        "requires_memory_lookup": requires_memory_lookup,
        "requires_verified_receipt": requires_verified_receipt,
    }


def _finite_clamp(val: Any, lo: float, hi: float, default: float) -> float:
    try:
        fv = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(fv):
        return default
    return max(lo, min(hi, fv))


def evolve_liquid_ode_fast(
    state_in: tuple[float, ...] | list[float],
    *,
    dt_seconds: float,
    hour_of_day: float,
    stimulus_intensity: float = 0.0,
    house_activity: float = 0.25,
    unresolved_tension: float = 0.0,
) -> dict[str, Any]:
    """Evolve Eva's 6D Liquid Time-Constant (dx/dt) ODE via RK4 in compiled C/SIMD at 0 LLM tokens."""
    padded = [_finite_clamp(v, 0.0, 1.0, 0.60) for v in list(state_in)[:6]]
    while len(padded) < 6:
        padded.append(0.60)
    dt = _finite_clamp(dt_seconds, 0.0, 21600.0, 0.0)
    stim = _finite_clamp(stimulus_intensity, 0.0, 1.0, 0.0)
    act = _finite_clamp(house_activity, 0.0, 1.0, 0.25)
    tens = _finite_clamp(unresolved_tension, 0.0, 1.0, 0.0)
    hod_raw = float(hour_of_day) if isinstance(hour_of_day, (int, float)) and math.isfinite(float(hour_of_day)) else 12.0
    hod = ((hod_raw % 24.0) + 24.0) % 24.0

    labels = (
        "arousal",
        "social_attunement",
        "epistemic_drive",
        "home_proprioception",
        "temporal_anticipation",
        "affective_coherence",
    )

    if _NATIVE_LIB is not None and hasattr(_NATIVE_LIB, "eva_liquid_ode_step"):
        c_in = (ctypes.c_double * 6)(*padded)
        c_out = (ctypes.c_double * 6)()
        c_tau = (ctypes.c_double * 6)()
        c_vel = ctypes.c_double()
        c_steps = ctypes.c_int()
        _NATIVE_LIB.eva_liquid_ode_step(
            c_in,
            dt,
            hod,
            stim,
            act,
            tens,
            c_out,
            c_tau,
            ctypes.byref(c_vel),
            ctypes.byref(c_steps),
        )
        state_vec = tuple(float(c_out[i]) for i in range(6))
        tau_vec = tuple(float(c_tau[i]) for i in range(6))
        return {
            "state": dict(zip(labels, state_vec)),
            "state_vector": state_vec,
            "tau_effective_seconds": dict(zip(labels, tau_vec)),
            "mean_tau_seconds": round(sum(tau_vec) / 6.0, 2),
            "phase_velocity": float(c_vel.value),
            "rk4_substeps": int(c_steps.value),
            "native_c_active": True,
        }

    # Identical pure-Python RK4 fallback
    tau0 = (240.0, 900.0, 1800.0, 1200.0, 1500.0, 3600.0)
    gain_w = (0.14, 0.08, 0.045, 0.06, 0.07, 0.03)
    lateral = (
        (0.00, 0.35, 0.25, -0.20, 0.30, 0.10),
        (0.30, 0.00, 0.20, 0.25, 0.15, 0.35),
        (0.25, 0.15, 0.00, 0.30, 0.20, 0.20),
        (-0.15, 0.20, 0.10, 0.00, -0.10, 0.45),
        (0.35, 0.20, 0.25, -0.10, 0.00, 0.15),
        (-0.10, 0.30, 0.15, 0.40, 0.10, 0.00),
    )
    stim_drive = (3.2, 2.8, 2.1, 2.4, 2.6, 1.6)
    bias = (-1.35, -1.20, -1.40, -1.50, -1.30, -1.60)
    combined_stim = max(0.0, min(1.0, 0.75 * stim + 0.35 * act))
    circ = math.cos(((hod - 14.5) / 24.0) * 2.0 * math.pi)
    daytime = 7.0 <= hod <= 22.5
    trans = max(
        math.exp(-((hod - 7.75) / 1.4) ** 2),
        math.exp(-((hod - 17.75) / 1.6) ** 2),
    )
    base = (
        max(0.0, min(1.0, 0.42 + 0.28 * circ + 0.15 * act)),
        0.58 if daytime else 0.28,
        max(0.0, min(1.0, 0.45 + 0.22 * circ * (1.0 - tens))),
        max(0.0, min(1.0, 0.88 - 0.42 * tens)),
        max(0.0, min(1.0, 0.24 + 0.52 * trans + 0.15 * tens)),
        max(0.0, min(1.0, 0.84 - 0.25 * tens)),
    )
    rev_a = (
        max(0.0, min(1.0, 0.92 * combined_stim + 0.35 * tens + 0.25)),
        max(0.0, min(1.0, 0.90 * combined_stim + (0.45 if daytime else 0.25))),
        max(0.0, min(1.0, 0.82 - 0.30 * tens + 0.15 * combined_stim)),
        max(0.0, min(1.0, 0.94 - 0.65 * tens)),
        max(0.0, min(1.0, 0.45 + 0.45 * trans + 0.30 * combined_stim)),
        max(0.0, min(1.0, 0.90 - 0.35 * tens + 0.10 * combined_stim)),
    )

    def rhs(xv: list[float], st: float) -> tuple[list[float], list[float]]:
        dx: list[float] = []
        teff: list[float] = []
        for i in range(6):
            rec = bias[i] + stim_drive[i] * st + (1.8 * tens if i in (0, 3) else 0.4 * tens)
            rec += sum(lateral[i][j] * xv[j] for j in range(6))
            sig = 1.0 / (1.0 + math.exp(-max(-16.0, min(16.0, rec))))
            g = gain_w[i] * sig
            leak = 1.0 / tau0[i]
            dx.append(-leak * (xv[i] - base[i]) + g * (rev_a[i] - xv[i]))
            teff.append(1.0 / (leak + g))
        return dx, teff

    x = list(padded)
    steps = max(1, min(1800, int(math.ceil(dt / 12.0)) if dt > 0 else 1))
    h = dt / steps
    cur_s = combined_stim
    if dt > 1e-6:
        for _ in range(steps):
            k1, _ = rhs(x, cur_s)
            k2, _ = rhs([max(0.0, min(1.0, x[i] + 0.5 * h * k1[i])) for i in range(6)], cur_s)
            k3, _ = rhs([max(0.0, min(1.0, x[i] + 0.5 * h * k2[i])) for i in range(6)], cur_s)
            k4, _ = rhs([max(0.0, min(1.0, x[i] + h * k3[i])) for i in range(6)], cur_s)
            x = [
                max(0.0, min(1.0, x[i] + (h / 6.0) * (k1[i] + 2.0 * k2[i] + 2.0 * k3[i] + k4[i])))
                for i in range(6)
            ]
            cur_s *= math.exp(-h / 45.0)
    inst_dx, tau_raw = rhs(x, combined_stim)
    state_vec = tuple(round(v, 4) for v in x)
    tau_vec = tuple(round(max(1.0, min(7200.0, t)), 2) for t in tau_raw)
    vel = round(math.sqrt(sum(d * d for d in inst_dx)), 6)
    return {
        "state": dict(zip(labels, state_vec)),
        "state_vector": state_vec,
        "tau_effective_seconds": dict(zip(labels, tau_vec)),
        "mean_tau_seconds": round(sum(tau_vec) / 6.0, 2),
        "phase_velocity": vel,
        "rk4_substeps": steps,
        "native_c_active": False,
    }


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
    "compute_concept_vector_fast",
    "compute_expected_free_energy_fast",
    "compute_homeostatic_step_fast",
    "evolve_liquid_ode_fast",
    "fast_canonical_sha256",
    "rust_json_bytes",
    "simulate_world_model_fast",
    "update_titans_synaptic_memory_fast",
]
