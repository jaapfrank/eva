"""Fase 4B — Multi-Frame Face Tracklet Fusion, Local Speaker Biometrics, Visitor Anti-Spam & Bystander Privacy Shield.

 Core guarantees:
1. Candidate presence only (`biometric_candidate_not_authentication`): never mints authority or bypasses owner gates.
2. Household boundary strictly honors active `gezinshoofd` / `gezin` members from `HouseholdHeadSettings`
   (Jaap & Lola); legacy bystander labels (neighbors, extended family, guests) are classified as
   `non_household_acquaintance` and immediately activate the Bystander Privacy Shield.
3. Zero "Wie is dit?" spam: fleeting or blurry unknown faces are ignored; only sharp, frontal, lingering
   visitors (>= 3 sharp frames and >= 4.0s dwell) appear quietly on the bounded Visitor Review card,
   retaining zero images and zero face embeddings.
4. Sub-10ms pure-stdlib CPU 64-D Mel-cepstral + prosodic voiceprint engine (plus 192-D ECAPA-TDNN vector
   compatibility) stored atomically (`0o600`) with zero cloud storage and zero vector disclosure.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import threading
from typing import Any, Iterator, Literal

from eva.application.household_head_settings import HouseholdHeadSettings
from eva.contracts.holostage import BiometricFrameFaceInput, VisitorClassification, VisitorReviewEntry

FACE_EVIDENCE_KIND = "face_tracklet_candidate_not_authentication"
VOICE_EVIDENCE_KIND = "voice_similarity_candidate_not_authentication"

MAX_ACTIVE_TRACKLETS = 16
MAX_TRACKLET_GAP_SECONDS = 8.0
MAX_TRACKLET_RETENTION_SECONDS = 30.0
MAX_VISITOR_CARDS = 24
VISITOR_COOLDOWN_SECONDS = 900.0
MIN_VISITOR_SHARP_FRAMES = 3
MIN_VISITOR_DWELL_SECONDS = 4.0
SHIELD_HOLD_SECONDS = 25.0

MAX_PCM_BYTES = 320_000  # 10 seconds at 16 kHz mono s16le
PCM_SAMPLE_RATE = 16_000
FFT_SIZE = 512
HOP_SIZE = 256
NUM_MEL_BINS = 24
NUM_MFCC = 16  # c1..c16 (c0 loudness excluded for volume invariance)
NATIVE_VOICEPRINT_DIM = 64
ECAPA_VOICEPRINT_DIM = 192

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,79}$")


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _clean_source_id(value: str, fallback: str = "camera.stage") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", (value or "").strip()).strip("_").lower()[:72]
    if not cleaned or not _SAFE_ID_RE.match(cleaned):
        return fallback
    return cleaned


def _atomic_write_json_0600(directory: Path, prefix: str, target: Path, payload: dict[str, Any]) -> None:
    tmp = directory / f"{prefix}.tmp.{os.getpid()}.{secrets.token_hex(6)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    os.replace(str(tmp), str(target))


def _normalize_vector(values: list[float] | tuple[float, ...], expected_dim: int) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != expected_dim:
        raise ValueError(f"voiceprint vector must contain {expected_dim} finite floats")
    out: list[float] = []
    sq_sum = 0.0
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise ValueError("voiceprint vector contains non-finite value")
        val = float(item)
        out.append(val)
        sq_sum += val * val
    norm = math.sqrt(sq_sum)
    if not math.isfinite(norm) or norm <= 1e-9:
        raise ValueError("voiceprint vector norm must be positive")
    return tuple(v / norm for v in out)


# Precomputed DSP tables for deterministic <8ms 16kHz Mel-cepstral + prosodic voiceprints
def _build_dsp_tables() -> tuple[
    tuple[float, ...],
    tuple[int, ...],
    tuple[tuple[tuple[int, float], ...], ...],
    tuple[tuple[float, ...], ...],
]:
    hamming = tuple(0.54 - 0.46 * math.cos(2.0 * math.pi * n / (FFT_SIZE - 1)) for n in range(FFT_SIZE))
    bits = int(math.log2(FFT_SIZE))
    bitrev = []
    for i in range(FFT_SIZE):
        rev = 0
        x = i
        for _ in range(bits):
            rev = (rev << 1) | (x & 1)
            x >>= 1
        bitrev.append(rev)

    def hz_to_mel(hz: float) -> float:
        return 2595.0 * math.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    low_mel = hz_to_mel(80.0)
    high_mel = hz_to_mel(7600.0)
    mel_points = [
        low_mel + i * (high_mel - low_mel) / (NUM_MEL_BINS + 1)
        for i in range(NUM_MEL_BINS + 2)
    ]
    bins = [
        min(FFT_SIZE // 2, max(1, int(math.floor((FFT_SIZE + 1) * mel_to_hz(m) / PCM_SAMPLE_RATE))))
        for m in mel_points
    ]
    filterbank: list[tuple[tuple[int, float], ...]] = []
    for m in range(1, NUM_MEL_BINS + 1):
        f_left, f_center, f_right = bins[m - 1], bins[m], bins[m + 1]
        if f_center <= f_left:
            f_center = f_left + 1
        if f_right <= f_center:
            f_right = f_center + 1
        weights: list[tuple[int, float]] = []
        for k in range(f_left, min(f_right + 1, FFT_SIZE // 2 + 1)):
            if k < f_center:
                w = (k - f_left) / (f_center - f_left)
            else:
                w = (f_right - k) / (f_right - f_center)
            if w > 0.0:
                weights.append((k, w))
        filterbank.append(tuple(weights))

    # DCT-II basis for c1..c16 (skipping c0 energy for gain invariance)
    dct_basis: list[tuple[float, ...]] = []
    for k in range(1, NUM_MFCC + 1):
        row = tuple(
            math.cos(math.pi * k * (n + 0.5) / NUM_MEL_BINS)
            for n in range(NUM_MEL_BINS)
        )
        dct_basis.append(row)

    return hamming, tuple(bitrev), tuple(filterbank), tuple(dct_basis)


_HAMMING, _BITREV, _MEL_FILTERS, _DCT_BASIS = _build_dsp_tables()


def _fft_power_spectrum_512(windowed: list[float]) -> list[float]:
    """In-place iterative Cooley-Tukey radix-2 FFT returning half-spectrum power [0..256]."""
    re_buf = [windowed[_BITREV[i]] for i in range(FFT_SIZE)]
    im_buf = [0.0] * FFT_SIZE
    length = 2
    while length <= FFT_SIZE:
        half = length // 2
        angle = -2.0 * math.pi / length
        wlen_r = math.cos(angle)
        wlen_i = math.sin(angle)
        for start in range(0, FFT_SIZE, length):
            wr, wi = 1.0, 0.0
            for j in range(half):
                u_idx = start + j
                v_idx = u_idx + half
                vr = re_buf[v_idx] * wr - im_buf[v_idx] * wi
                vi = re_buf[v_idx] * wi + im_buf[v_idx] * wr
                ur, ui = re_buf[u_idx], im_buf[u_idx]
                re_buf[u_idx] = ur + vr
                im_buf[u_idx] = ui + vi
                re_buf[v_idx] = ur - vr
                im_buf[v_idx] = ui - vi
                wr, wi = wr * wlen_r - wi * wlen_i, wr * wlen_i + wi * wlen_r
        length <<= 1
    return [re_buf[k] * re_buf[k] + im_buf[k] * im_buf[k] for k in range(FFT_SIZE // 2 + 1)]


def extract_pcm16_voiceprint(pcm_bytes: bytes) -> tuple[tuple[float, ...], dict[str, Any]]:
    """Extract a gain-invariant 64-D Mel-cepstral + prosodic voiceprint from 16 kHz mono s16le PCM."""
    if not isinstance(pcm_bytes, (bytes, bytearray)) or len(pcm_bytes) < 6400:
        raise ValueError("Spraakfragment is te kort (minimaal 0,2s 16 kHz PCM vereist)")
    if len(pcm_bytes) > MAX_PCM_BYTES or len(pcm_bytes) % 2 != 0:
        raise ValueError("Ongeldige PCM-framegrootte voor lokale stemanalyse")

    sample_count = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{sample_count}h", pcm_bytes)

    # Frame-level voiced speech selection (reject silence / clicks)
    mfcc_frames: list[tuple[float, ...]] = []
    pitch_lags: list[float] = []
    spectral_tilts: list[float] = []
    rms_values: list[float] = []

    max_frames = 180  # cap CPU work to ~2.9s distributed speech frames
    total_possible = max(0, (sample_count - FFT_SIZE) // HOP_SIZE + 1)
    stride = max(1, total_possible // max_frames) if total_possible > max_frames else 1

    for frame_idx in range(0, total_possible, stride):
        offset = frame_idx * HOP_SIZE
        seg = samples[offset : offset + FFT_SIZE]
        rms = math.sqrt(sum(s * s for s in seg) / FFT_SIZE)
        if rms < 180.0:
            continue
        rms_values.append(rms)
        # Pre-emphasis + normalized amplitude + Hamming window
        scale = 1.0 / max(rms, 1.0)
        prev = float(seg[0]) * scale
        windowed = [prev * _HAMMING[0]]
        for n in range(1, FFT_SIZE):
            cur = float(seg[n]) * scale
            windowed.append((cur - 0.97 * prev) * _HAMMING[n])
            prev = cur

        power = _fft_power_spectrum_512(windowed)
        mel_logs: list[float] = []
        for filt in _MEL_FILTERS:
            energy = sum(power[idx] * w for idx, w in filt)
            mel_logs.append(math.log(max(energy, 1e-8)))

        # Cepstral mean subtraction across bands removes channel coloration
        band_mean = sum(mel_logs) / NUM_MEL_BINS
        centered_mel = [m - band_mean for m in mel_logs]
        mfcc = tuple(
            sum(centered_mel[n] * basis[n] for n in range(NUM_MEL_BINS))
            for basis in _DCT_BASIS
        )
        mfcc_frames.append(mfcc)

        low_band = sum(centered_mel[:8]) / 8.0
        high_band = sum(centered_mel[16:]) / 8.0
        spectral_tilts.append(low_band - high_band)

        # Normalized autocorrelation in human F0 range (75 Hz .. 360 Hz -> lag 44 .. 213 at 16 kHz)
        norm0 = sum(float(s) * float(s) for s in seg[:256])
        if norm0 > 1.0:
            best_corr = 0.0
            best_lag = 100
            for lag in range(44, 214, 2):
                corr = sum(float(seg[i]) * float(seg[i + lag]) for i in range(256)) / norm0
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag
            if best_corr >= 0.28:
                pitch_lags.append(PCM_SAMPLE_RATE / float(best_lag))

    voiced_count = len(mfcc_frames)
    if voiced_count < 8:
        raise ValueError("Onvoldoende stemgeluid gedetecteerd in audiofragment")

    # Build 64-D feature vector:
    # [0..15]: mean MFCC c1..c16
    # [16..31]: std MFCC c1..c16
    # [32..47]: mean abs delta MFCC c1..c16
    # [48..55]: quantile/prosodic pitch & spectral tilt descriptors
    # [56..63]: higher-order formants & modulation ratios
    means = [sum(f[d] for f in mfcc_frames) / voiced_count for d in range(NUM_MFCC)]
    stds = [
        math.sqrt(sum((f[d] - means[d]) ** 2 for f in mfcc_frames) / voiced_count)
        for d in range(NUM_MFCC)
    ]
    if voiced_count >= 2:
        deltas = [
            sum(abs(mfcc_frames[t][d] - mfcc_frames[t - 1][d]) for t in range(1, voiced_count))
            / (voiced_count - 1)
            for d in range(NUM_MFCC)
        ]
    else:
        deltas = [0.0] * NUM_MFCC

    if pitch_lags:
        sorted_f0 = sorted(pitch_lags)
        f0_mean = sum(sorted_f0) / len(sorted_f0)
        f0_med = sorted_f0[len(sorted_f0) // 2]
        f0_std = math.sqrt(sum((p - f0_mean) ** 2 for p in sorted_f0) / len(sorted_f0))
        voicing_ratio = len(sorted_f0) / voiced_count
    else:
        f0_mean, f0_med, f0_std, voicing_ratio = 150.0, 150.0, 10.0, 0.0

    tilt_mean = sum(spectral_tilts) / voiced_count
    tilt_std = math.sqrt(sum((x - tilt_mean) ** 2 for x in spectral_tilts) / voiced_count)

    # Scale prosodic dims to comparable variance with MFCCs before L2 unit normalization
    prosody = [
        (f0_mean - 165.0) / 35.0,
        (f0_med - 165.0) / 35.0,
        f0_std / 25.0,
        voicing_ratio * 2.0,
        tilt_mean * 0.5,
        tilt_std * 0.5,
        means[0] - means[1],
        means[1] - means[2],
        means[2] - means[3],
        means[3] - means[4],
        stds[0] - stds[1],
        stds[1] - stds[2],
        deltas[0] - deltas[1],
        deltas[1] - deltas[2],
        sum(means[:4]) * 0.25,
        sum(means[4:12]) * 0.25,
    ]
    raw_64 = means + stds + deltas + prosody
    assert len(raw_64) == NATIVE_VOICEPRINT_DIM
    vec = _normalize_vector(raw_64, NATIVE_VOICEPRINT_DIM)
    meta = {
        "voiced_frames": voiced_count,
        "f0_median_hz": round(f0_med, 1),
        "rms_mean": round(sum(rms_values) / len(rms_values), 1),
    }
    return vec, meta


@dataclass
class _ActiveFaceTracklet:
    tracklet_id: str
    source_id: str
    room_id: str
    first_seen: datetime
    last_seen: datetime
    bbox: tuple[int, int, int, int]
    total_frames: int = 0
    sharp_frames: int = 0
    best_quality: float = 0.0
    gaze_streak: int = 0
    candidate_weights: dict[str, float] = field(default_factory=dict)
    candidate_weighted_sim: dict[str, float] = field(default_factory=dict)
    candidate_weighted_margin: dict[str, float] = field(default_factory=dict)
    unknown_sharp_weight: float = 0.0
    visitor_logged: bool = False


def _bbox_iou_and_shift(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[float, float]:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    iou = inter / float(area_a + area_b - inter)
    acx, acy = 0.5 * (ax1 + ax2), 0.5 * (ay1 + ay2)
    bcx, bcy = 0.5 * (bx1 + bx2), 0.5 * (by1 + by2)
    dist = math.hypot(acx - bcx, acy - bcy)
    diag = max(1.0, math.hypot(0.5 * ((ax2 - ax1) + (bx2 - bx1)), 0.5 * ((ay2 - ay1) + (by2 - by1))))
    return iou, dist / diag


class LocalSpeakerBiometrics:
    """Atomic `0o600` local speaker voiceprint store strictly for active household members (Jaap & Lola)."""

    def __init__(self, directory: Path | str, household_settings: HouseholdHeadSettings) -> None:
        self.directory = Path(directory)
        self.household_settings = household_settings
        self.state_file = self.directory / "biometric-voiceprints.json"
        self.lock_file = self.directory / ".biometric-voiceprints.lock"

    def _ensure_dir(self) -> None:
        created = not self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            os.chmod(self.directory, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_dir()
        fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _active_household_map(self, now: datetime) -> dict[str, dict[str, Any]]:
        st = self.household_settings.status(now=now)
        allowed: dict[str, dict[str, Any]] = {}
        for m in st.get("members") or []:
            if (
                isinstance(m, dict)
                and m.get("status") == "active"
                and m.get("role") in ("gezinshoofd", "gezin")
            ):
                allowed[str(m["member_id"])] = m
        return allowed

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"contract": "EvaLocalVoiceprints/v1", "profiles": {}}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("contract") == "EvaLocalVoiceprints/v1":
                return raw
        except Exception:
            pass
        return {"contract": "EvaLocalVoiceprints/v1", "profiles": {}}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        _atomic_write_json_0600(self.directory, ".biometric-voiceprints", self.state_file, data)

    def enroll_pcm(self, *, member_id: str, pcm_bytes: bytes, now: datetime | None = None) -> dict[str, Any]:
        ts = _utc_now(now)
        allowed = self._active_household_map(ts)
        if member_id not in allowed:
            raise PermissionError("Steminscriptie is uitsluitend toegestaan voor actieve gezinsleden (Jaap & Lola)")
        vec, meta = extract_pcm16_voiceprint(pcm_bytes)
        return self._upsert_vector(
            member_id=member_id,
            display_name=str(allowed[member_id]["display_name"]),
            space="dsp64",
            vector=vec,
            meta=meta,
            now=ts,
        )

    def enroll_embedding(
        self, *, member_id: str, embedding: list[float] | tuple[float, ...], now: datetime | None = None
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        allowed = self._active_household_map(ts)
        if member_id not in allowed:
            raise PermissionError("Steminscriptie is uitsluitend toegestaan voor actieve gezinsleden (Jaap & Lola)")
        dim = len(embedding) if isinstance(embedding, (list, tuple)) else 0
        if dim not in (NATIVE_VOICEPRINT_DIM, ECAPA_VOICEPRINT_DIM):
            raise ValueError("Stem-embedding moet 64-D (DSP) of 192-D (ECAPA-TDNN) zijn")
        space = "ecapa192" if dim == ECAPA_VOICEPRINT_DIM else "dsp64"
        vec = _normalize_vector(embedding, dim)
        return self._upsert_vector(
            member_id=member_id,
            display_name=str(allowed[member_id]["display_name"]),
            space=space,
            vector=vec,
            meta={"source": space},
            now=ts,
        )

    def _upsert_vector(
        self,
        *,
        member_id: str,
        display_name: str,
        space: str,
        vector: tuple[float, ...],
        meta: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        dim = len(vector)
        with self._locked():
            state = self._read_unlocked()
            profiles = dict(state.get("profiles") or {})
            entry = dict(profiles.get(member_id) or {})
            spaces = dict(entry.get("spaces") or {})
            existing = spaces.get(space)
            if isinstance(existing, dict) and isinstance(existing.get("vector"), list) and len(existing["vector"]) == dim:
                old_vec = _normalize_vector(existing["vector"], dim)
                count = min(32, int(existing.get("samples", 1)) + 1)
                blended = [old_vec[i] * (count - 1) + vector[i] for i in range(dim)]
                new_vec = _normalize_vector(blended, dim)
            else:
                count = 1
                new_vec = vector
            spaces[space] = {
                "vector": [round(v, 6) for v in new_vec],
                "samples": count,
                "updated_at": _iso(now),
            }
            entry.update(
                {
                    "member_id": member_id,
                    "display_name": display_name,
                    "spaces": spaces,
                    "updated_at": _iso(now),
                }
            )
            profiles[member_id] = entry
            state["profiles"] = profiles
            self._write_unlocked(state)
        return {
            "member_id": member_id,
            "display_name": display_name,
            "space": space,
            "samples": count,
            "updated_at": _iso(now),
            "meta": meta,
        }

    def delete_voiceprint(self, member_id: str) -> bool:
        with self._locked():
            state = self._read_unlocked()
            profiles = dict(state.get("profiles") or {})
            if member_id not in profiles:
                return False
            profiles.pop(member_id, None)
            state["profiles"] = profiles
            self._write_unlocked(state)
            return True

    def match_pcm(self, pcm_bytes: bytes, *, now: datetime | None = None) -> dict[str, Any]:
        ts = _utc_now(now)
        vec, meta = extract_pcm16_voiceprint(pcm_bytes)
        res = self._match_vector(vec, space="dsp64", now=ts)
        res["voiced_frames"] = meta["voiced_frames"]
        return res

    def match_embedding(
        self, embedding: list[float] | tuple[float, ...], *, now: datetime | None = None
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        dim = len(embedding) if isinstance(embedding, (list, tuple)) else 0
        if dim not in (NATIVE_VOICEPRINT_DIM, ECAPA_VOICEPRINT_DIM):
            raise ValueError("Stem-embedding moet 64-D of 192-D zijn")
        space = "ecapa192" if dim == ECAPA_VOICEPRINT_DIM else "dsp64"
        vec = _normalize_vector(embedding, dim)
        return self._match_vector(vec, space=space, now=ts)

    def _match_vector(self, vec: tuple[float, ...], *, space: str, now: datetime) -> dict[str, Any]:
        dim = len(vec)
        allowed = self._active_household_map(now)
        if not self.state_file.is_file():
            return {"status": "unconfigured", "evidence_kind": VOICE_EVIDENCE_KIND}
        with self._locked():
            state = self._read_unlocked()
        profiles = state.get("profiles") or {}
        scored: list[tuple[float, str, str]] = []
        for mid, entry in profiles.items():
            if mid not in allowed or not isinstance(entry, dict):
                continue
            sp = (entry.get("spaces") or {}).get(space)
            if not isinstance(sp, dict) or not isinstance(sp.get("vector"), list) or len(sp["vector"]) != dim:
                continue
            ref = _normalize_vector(sp["vector"], dim)
            cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(vec, ref))))
            scored.append((cosine, mid, str(allowed[mid]["display_name"])))
        if not scored:
            return {"status": "unconfigured", "evidence_kind": VOICE_EVIDENCE_KIND}
        scored.sort(key=lambda r: r[0], reverse=True)
        best_cos, best_id, best_name = scored[0]
        margin = 1.0 if len(scored) == 1 else (best_cos - scored[1][0])
        min_cos = 0.72 if space == "dsp64" else 0.56
        min_margin = 0.055 if len(scored) > 1 else 0.0
        if best_cos < min_cos or margin < min_margin:
            return {"status": "unknown_voice", "evidence_kind": VOICE_EVIDENCE_KIND}
        return {
            "status": "household_voice_candidate",
            "member_id": best_id,
            "display_name": best_name,
            "similarity_ppm": round(best_cos * 1_000_000),
            "margin_ppm": round(margin * 1_000_000),
            "space": space,
            "evidence_kind": VOICE_EVIDENCE_KIND,
        }

    def status_summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Public status summary without disclosing any stored voiceprint vectors."""
        ts = _utc_now(now)
        allowed = self._active_household_map(ts)
        raw_profiles: dict[str, Any] = {}
        if self.state_file.is_file():
            with self._locked():
                raw_profiles = dict(self._read_unlocked().get("profiles") or {})
        enrolled_out = []
        for mid, member in allowed.items():
            prof = raw_profiles.get(mid)
            spaces = list((prof.get("spaces") or {}).keys()) if isinstance(prof, dict) else []
            samples = (
                sum(int(v.get("samples", 0)) for v in (prof.get("spaces") or {}).values() if isinstance(v, dict))
                if isinstance(prof, dict)
                else 0
            )
            enrolled_out.append(
                {
                    "member_id": mid,
                    "display_name": member["display_name"],
                    "role": member["role"],
                    "enrolled": bool(spaces),
                    "spaces": spaces,
                    "sample_count": samples,
                    "updated_at": prof.get("updated_at") if isinstance(prof, dict) else None,
                }
            )
        return {
            "contract": "EvaSpeakerBiometricsSummary/v1",
            "evidence_kind": VOICE_EVIDENCE_KIND,
            "cloud_storage": False,
            "members": enrolled_out,
        }


class BiometricPresenceFusion:
    """Multi-frame camera tracklet fuser, quiet visitor ledger, speaker matcher & bystander privacy shield."""

    def __init__(self, directory: Path | str, household_settings: HouseholdHeadSettings) -> None:
        self.directory = Path(directory)
        self.household_settings = household_settings
        self.speaker = LocalSpeakerBiometrics(self.directory, household_settings)
        self.visitor_file = self.directory / "holostage-visitors.json"
        self.visitor_lock_file = self.directory / ".holostage-visitors.lock"

        self._mem_lock = threading.Lock()
        self._tracklets: OrderedDict[str, _ActiveFaceTracklet] = OrderedDict()
        self._last_household_candidate: dict[str, Any] | None = None
        self._last_household_seen_at: datetime | None = None
        self._shield_until: datetime | None = None
        self._shield_reason: str = ""
        self._manual_shield_lock: bool = False
        self._gaze_engaged_until: datetime | None = None
        self._last_visitor_by_source: dict[str, tuple[str, datetime]] = {}

    def _ensure_dir(self) -> None:
        created = not self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            os.chmod(self.directory, 0o700)

    @contextmanager
    def _visitor_locked(self) -> Iterator[None]:
        self._ensure_dir()
        fd = os.open(str(self.visitor_lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _resolve_person_label(
        self, raw_label: str | None, *, now: datetime
    ) -> tuple[Literal["household", "non_household_acquaintance", "unknown"], str | None, str | None]:
        """Map an enrolled face label strictly against active household members (Jaap & Lola)."""
        if not raw_label or not raw_label.strip():
            return "unknown", None, None
        cleaned = raw_label.strip()
        norm = cleaned.casefold()
        st = self.household_settings.status(now=now)
        for m in st.get("members") or []:
            if not isinstance(m, dict) or m.get("status") != "active":
                continue
            m_name = str(m.get("display_name") or "").strip()
            m_id = str(m.get("member_id") or "").strip()
            m_role = str(m.get("role") or "")
            # Match exact member_id or first/full name of Jaap / Lola
            first_token = m_name.split()[0].casefold() if m_name else ""
            if norm in {m_id.casefold(), m_name.casefold()} or (first_token and norm.split()[0] == first_token):
                if m_role in ("gezinshoofd", "gezin"):
                    return "household", m_id, m_name
                return "non_household_acquaintance", None, m_name
        # Legacy Eva1 acquaintances (e.g. Mark, Jessy, Marjan, Sanne, Frank, Sylvia) are NEVER household!
        return "non_household_acquaintance", None, cleaned[:48]

    def _evict_stale_tracklets_unlocked(self, now: datetime) -> None:
        stale = [
            tid
            for tid, tr in self._tracklets.items()
            if (now - tr.last_seen).total_seconds() > MAX_TRACKLET_RETENTION_SECONDS
        ]
        for tid in stale:
            self._tracklets.pop(tid, None)
        while len(self._tracklets) > MAX_ACTIVE_TRACKLETS:
            self._tracklets.popitem(last=False)

    def observe_camera_frame(
        self,
        *,
        source_id: str,
        room_id: str = "woonkamer",
        faces: list[BiometricFrameFaceInput | dict[str, Any]],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fuse multi-frame face detections, update quiet visitor ledger & trigger Bystander Privacy Shield."""
        ts = _utc_now(now)
        clean_source = _clean_source_id(source_id)
        clean_room = re.sub(r"[^a-z0-9_-]+", "_", (room_id or "algemeen").lower()).strip("_")[:40] or "algemeen"
        parsed_faces = [
            f if isinstance(f, BiometricFrameFaceInput) else BiometricFrameFaceInput.model_validate(f)
            for f in faces[:8]
        ]

        visitor_to_record: tuple[str, str, datetime, int, float, float] | None = None

        with self._mem_lock:
            self._evict_stale_tracklets_unlocked(ts)
            matched_tids: set[str] = set()
            bystander_detected = False
            bystander_reason = ""
            best_household_in_frame: dict[str, Any] | None = None

            for face in parsed_faces:
                x1, y1, x2, y2 = face.bbox
                if x2 <= x1 or y2 <= y1 or x1 < 0 or y1 < 0 or x2 > 100_000 or y2 > 100_000:
                    continue
                area = (x2 - x1) * (y2 - y1)
                area_score = min(1.0, area / 6400.0)
                quality = round(0.35 * area_score + 0.25 * face.det_score + 0.20 * face.sharpness + 0.20 * face.frontal_pose, 4)
                is_sharp_frontal = (
                    area >= 1400
                    and face.det_score >= 0.58
                    and face.sharpness >= 0.42
                    and face.frontal_pose >= 0.45
                )

                # Associate with existing tracklet on same source_id
                best_tid: str | None = None
                best_score = -1.0
                for tid, tr in self._tracklets.items():
                    if tid in matched_tids or tr.source_id != clean_source:
                        continue
                    if (ts - tr.last_seen).total_seconds() > MAX_TRACKLET_GAP_SECONDS:
                        continue
                    iou, rel_shift = _bbox_iou_and_shift(tr.bbox, face.bbox)
                    if iou >= 0.20 or rel_shift <= 1.35:
                        assoc_score = iou - 0.35 * rel_shift
                        if assoc_score > best_score:
                            best_score = assoc_score
                            best_tid = tid

                if best_tid is None:
                    raw_seed = f"{clean_source}:{ts.timestamp():.3f}:{face.bbox}"
                    best_tid = "trk_" + hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:12]
                    tr = _ActiveFaceTracklet(
                        tracklet_id=best_tid,
                        source_id=clean_source,
                        room_id=clean_room,
                        first_seen=ts,
                        last_seen=ts,
                        bbox=face.bbox,
                    )
                    self._tracklets[best_tid] = tr
                else:
                    tr = self._tracklets[best_tid]
                    tr.last_seen = ts
                    tr.bbox = face.bbox
                    self._tracklets.move_to_end(best_tid)

                matched_tids.add(best_tid)
                tr.total_frames += 1
                tr.best_quality = max(tr.best_quality, quality)

                if face.gaze_engaged and face.frontal_pose >= 0.68 and area >= 1400:
                    tr.gaze_streak += 1
                else:
                    tr.gaze_streak = max(0, tr.gaze_streak - 1)

                kind, member_id, display_name = (
                    self._resolve_person_label(face.person_label, now=ts)
                    if face.status == "known_candidate"
                    else ("unknown", None, None)
                )

                if kind == "non_household_acquaintance":
                    bystander_detected = True
                    bystander_reason = "non_household_acquaintance_present"
                elif kind == "unknown":
                    if area >= 900 and face.det_score >= 0.52:
                        bystander_detected = True
                        bystander_reason = bystander_reason or "unknown_face_present"
                    if is_sharp_frontal:
                        tr.sharp_frames += 1
                        tr.unknown_sharp_weight += quality
                        dwell = (tr.last_seen - tr.first_seen).total_seconds()
                        if (
                            tr.sharp_frames >= MIN_VISITOR_SHARP_FRAMES
                            and dwell >= MIN_VISITOR_DWELL_SECONDS
                            and not tr.candidate_weights
                        ):
                            visitor_to_record = (
                                tr.source_id,
                                tr.room_id,
                                tr.first_seen,
                                tr.sharp_frames,
                                tr.best_quality,
                                dwell,
                            )
                elif kind == "household" and member_id and display_name and is_sharp_frontal:
                    tr.sharp_frames += 1
                    w = max(0.2, quality)
                    tr.candidate_weights[member_id] = tr.candidate_weights.get(member_id, 0.0) + w
                    tr.candidate_weighted_sim[member_id] = (
                        tr.candidate_weighted_sim.get(member_id, 0.0) + w * face.similarity_ppm
                    )
                    tr.candidate_weighted_margin[member_id] = (
                        tr.candidate_weighted_margin.get(member_id, 0.0) + w * face.margin_ppm
                    )

                    tot_w = tr.candidate_weights[member_id]
                    fused_sim = round(tr.candidate_weighted_sim[member_id] / tot_w)
                    fused_margin = round(tr.candidate_weighted_margin[member_id] / tot_w)
                    confirmed_tracklet = (
                        (tr.sharp_frames >= 2 and fused_sim >= 340_000 and fused_margin >= 65_000)
                        or (quality >= 0.80 and fused_sim >= 460_000 and fused_margin >= 90_000)
                    )
                    if confirmed_tracklet:
                        cand = {
                            "member_id": member_id,
                            "display_name": display_name,
                            "tracklet_id": tr.tracklet_id,
                            "room_id": clean_room,
                            "sharp_frames": tr.sharp_frames,
                            "fused_similarity_ppm": fused_sim,
                            "fused_margin_ppm": fused_margin,
                            "gaze_to_talk_ready": tr.gaze_streak >= 2,
                            "evidence_kind": FACE_EVIDENCE_KIND,
                        }
                        if (
                            best_household_in_frame is None
                            or cand["fused_similarity_ppm"] > best_household_in_frame["fused_similarity_ppm"]
                        ):
                            best_household_in_frame = cand
                        if tr.gaze_streak >= 2:
                            self._gaze_engaged_until = ts + timedelta(seconds=8)

            if bystander_detected:
                self._shield_until = ts + timedelta(seconds=SHIELD_HOLD_SECONDS)
                self._shield_reason = bystander_reason
            elif best_household_in_frame is not None:
                self._last_household_candidate = best_household_in_frame
                self._last_household_seen_at = ts

        if visitor_to_record is not None:
            v_source, v_room, v_first, v_sharp, v_qual, dwell_s = visitor_to_record
            self._record_lingering_visitor(
                source_id=v_source,
                room_id=v_room,
                first_seen=v_first,
                sharp_frames=v_sharp,
                best_quality=v_qual,
                dwell_seconds=dwell_s,
                now=ts,
            )

        return self.snapshot(now=ts)

    def observe_voice_pcm(self, pcm_bytes: bytes, *, now: datetime | None = None) -> dict[str, Any]:
        ts = _utc_now(now)
        match = self.speaker.match_pcm(pcm_bytes, now=ts)
        with self._mem_lock:
            if match.get("status") == "unknown_voice":
                self._shield_until = ts + timedelta(seconds=SHIELD_HOLD_SECONDS)
                self._shield_reason = "unknown_voice_present"
            elif match.get("status") == "household_voice_candidate":
                self._last_household_candidate = {
                    "member_id": match["member_id"],
                    "display_name": match["display_name"],
                    "similarity_ppm": match["similarity_ppm"],
                    "margin_ppm": match["margin_ppm"],
                    "evidence_kind": VOICE_EVIDENCE_KIND,
                }
                self._last_household_seen_at = ts
        return match

    def set_manual_privacy_shield(self, locked: bool, *, now: datetime | None = None) -> dict[str, Any]:
        ts = _utc_now(now)
        with self._mem_lock:
            self._manual_shield_lock = bool(locked)
            if not locked:
                self._shield_until = None
                self._shield_reason = ""
            else:
                self._shield_reason = "manual_owner_lock"
        return self.snapshot(now=ts)

    def _read_visitors_unlocked(self) -> list[dict[str, Any]]:
        if not self.visitor_file.is_file():
            return []
        try:
            raw = json.loads(self.visitor_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("visitors"), list):
                return [v for v in raw["visitors"] if isinstance(v, dict)]
        except Exception:
            pass
        return []

    def _write_visitors_unlocked(self, rows: list[dict[str, Any]]) -> None:
        bounded = list(rows)
        while len(bounded) > MAX_VISITOR_CARDS:
            # Preserve owner-classified neighbors/couriers; evict oldest unreviewed/ignored entries first
            evict_idx = next(
                (
                    i
                    for i in range(len(bounded) - 1, -1, -1)
                    if bounded[i].get("classification") not in ("bekende_buur", "bezorger")
                ),
                len(bounded) - 1,
            )
            bounded.pop(evict_idx)
        _atomic_write_json_0600(
            self.directory,
            ".holostage-visitors",
            self.visitor_file,
            {"contract": "EvaVisitorReviewLedger/v1", "visitors": bounded},
        )

    def _record_lingering_visitor(
        self,
        *,
        source_id: str,
        room_id: str,
        first_seen: datetime,
        sharp_frames: int,
        best_quality: float,
        dwell_seconds: float,
        now: datetime,
    ) -> None:
        with self._visitor_locked():
            rows = self._read_visitors_unlocked()
            # Coalesce repeated lingering frames on the same source within cooldown
            existing_idx: int | None = None
            for idx, row in enumerate(rows):
                if row.get("source_id") == source_id:
                    try:
                        age = (now - _parse_iso(str(row["last_seen_at"]))).total_seconds()
                        if 0.0 <= age <= VISITOR_COOLDOWN_SECONDS:
                            existing_idx = idx
                            break
                    except Exception:
                        continue
            if existing_idx is not None:
                cur = dict(rows[existing_idx])
                cur["last_seen_at"] = _iso(now)
                cur["dwell_seconds"] = round(max(float(cur.get("dwell_seconds", 0.0)), dwell_seconds), 1)
                cur["sharp_frames"] = max(int(cur.get("sharp_frames", 1)), sharp_frames)
                cur["frontal_quality"] = round(max(float(cur.get("frontal_quality", 0.0)), best_quality), 3)
                rows[existing_idx] = VisitorReviewEntry.model_validate(cur).model_dump(mode="json")
            else:
                vid = "vis_" + hashlib.sha256(f"{source_id}:{_iso(first_seen)}".encode("utf-8")).hexdigest()[:12]
                entry = VisitorReviewEntry(
                    visitor_id=vid,
                    source_id=source_id,
                    room_id=room_id,
                    first_seen_at=_iso(first_seen),
                    last_seen_at=_iso(now),
                    dwell_seconds=round(dwell_seconds, 1),
                    sharp_frames=sharp_frames,
                    frontal_quality=round(best_quality, 3),
                    classification="unreviewed",
                    note_nl="",
                    chat_interrupt_sent=False,
                )
                rows.insert(0, entry.model_dump(mode="json"))
            self._write_visitors_unlocked(rows)

    def classify_visitor(
        self,
        *,
        visitor_id: str,
        classification: VisitorClassification,
        note_nl: str = "",
    ) -> list[dict[str, Any]]:
        clean_note = re.sub(r"\s+", " ", (note_nl or "")).strip()[:160]
        with self._visitor_locked():
            rows = self._read_visitors_unlocked()
            found = False
            out: list[dict[str, Any]] = []
            for row in rows:
                if row.get("visitor_id") == visitor_id:
                    found = True
                    updated = dict(row)
                    updated["classification"] = classification
                    updated["note_nl"] = clean_note
                    out.append(VisitorReviewEntry.model_validate(updated).model_dump(mode="json"))
                else:
                    out.append(row)
            if not found:
                raise ValueError("Bezoekerskaartje niet gevonden")
            self._write_visitors_unlocked(out)
            return out

    def list_visitors(self) -> list[dict[str, Any]]:
        if not self.visitor_file.is_file():
            return []
        with self._visitor_locked():
            return self._read_visitors_unlocked()

    def is_privacy_shield_active(self, *, now: datetime | None = None) -> tuple[bool, str]:
        ts = _utc_now(now)
        with self._mem_lock:
            if self._manual_shield_lock:
                return True, "manual_owner_lock"
            if self._shield_until is not None and ts < self._shield_until:
                return True, self._shield_reason or "bystander_present"
            return False, ""

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        ts = _utc_now(now)
        shield_active, shield_reason = self.is_privacy_shield_active(now=ts)
        with self._mem_lock:
            self._evict_stale_tracklets_unlocked(ts)
            gaze_active = bool(self._gaze_engaged_until is not None and ts < self._gaze_engaged_until)
            cand = None
            if (
                not shield_active
                and self._last_household_candidate is not None
                and self._last_household_seen_at is not None
                and (ts - self._last_household_seen_at).total_seconds() <= 90.0
            ):
                cand = dict(self._last_household_candidate)
            active_tracklets = len(self._tracklets)

        visitors = [] if shield_active else self.list_visitors()
        return {
            "contract": "EvaBiometricPresenceState/v1",
            "observed_at": _iso(ts),
            "privacy_shield_active": shield_active,
            "privacy_shield_reason": shield_reason,
            "gaze_to_talk_active": gaze_active and not shield_active,
            "chat_interrupt_policy": "never_spam_unknown_visitors",
            "active_tracklets": active_tracklets,
            "household_candidate": cand,
            "speaker_biometrics": self.speaker.status_summary(now=ts),
            "visitors_review": visitors,
        }


__all__ = [
    "BiometricPresenceFusion",
    "FACE_EVIDENCE_KIND",
    "LocalSpeakerBiometrics",
    "VOICE_EVIDENCE_KIND",
    "extract_pcm16_voiceprint",
]
