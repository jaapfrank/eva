"""Fase 5A & 5B (P1-05 / F2.1) — Privacy-Preserving Household Observation & 7-Day Stability Ledger.

Tracks real installed household usage for Jaap and Lola across one contiguous release window
without storing conversation text, media bytes, network addresses, or credentials.

Invariants:
- Household scope is strictly Jaap (`owner-jaap`) and Lola (`family-lola`).
- Release changes archive the previous window and restart the contiguous 7-day (`168h`) clock
  required by `F2.1` ("Zeven aaneengesloten dagen op één stabiele release").
- Unknown or subscription-backed model costs remain `None` (`AGENTS.md`: never invent costs
  or claim household acceptance prematurely).
- Persistent state uses `0o700` directory + `0o600` atomic file replacement under `fcntl.flock`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Iterator

_SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_VALID_CHANNELS: frozenset[str] = frozenset(
    {"telegram", "holostage", "voice_realtime", "cockpit", "native_speaker"}
)
MAX_DAILY_BUCKETS = 30
MAX_PRIOR_WINDOWS = 16
SEVEN_DAYS_HOURS = 168.0
REQUIRED_ACTIVE_DAYS = 7


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _normalize_release_id(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        candidate = os.environ.get("EVA_RELEASE_ID", "").strip() or "installed-local-release"
    if not _SAFE_RELEASE_RE.fullmatch(candidate):
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", candidate).strip("-")[:96]
        return cleaned or "installed-local-release"
    return candidate


def _classify_household_member(principal_id: str | None) -> str | None:
    norm = (principal_id or "").strip().lower()
    if norm in {"owner-jaap", "jaap", "owner"}:
        return "jaap"
    if norm in {"family-lola", "lola"}:
        return "lola"
    if norm == "household-shared" or norm.startswith("household-device:"):
        return "shared_household"
    return None


def _empty_day_bucket(day_key: str) -> dict[str, Any]:
    return {
        "date": day_key,
        "turns_jaap": 0,
        "turns_lola": 0,
        "turns_shared_household": 0,
        "rejected_non_household_attempts": 0,
        "channel_turns": {ch: 0 for ch in sorted(_VALID_CHANNELS)},
        "zero_token_idle_ticks": 0,
        "reflex_organogenesis_hits": 0,
        "reflex_latency_samples_ms": [],
        "proactive_staged_accepted": 0,
        "proactive_staged_dismissed": 0,
        "runtime_errors_detected": 0,
        "self_repairs_verified": 0,
        "measured_model_calls": 0,
        "unpriced_model_calls": 0,
        "known_cost_eur": 0.0,
    }


def _default_state_file() -> Path:
    configured = os.environ.get("EVA_HOUSEHOLD_OBSERVATION_FILE", "").strip()
    if configured:
        return Path(configured)
    for candidate in (
        Path("/var/lib/eva/work/household-observation/ledger-v1.json"),
        Path("/tmp/eva-household-observation/ledger-v1.json"),
    ):
        try:
            candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.access(candidate.parent, os.W_OK):
                return candidate
        except OSError:
            continue
    return Path("/tmp/eva-household-observation/ledger-v1.json")


class HouseholdObservationLedger:
    """Durable, privacy-safe household observation and 7-day release stability tracker."""

    def __init__(
        self,
        *,
        state_file: Path | None = None,
        release_id: str | None = None,
        runtime_metrics_probe: Callable[[], dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> None:
        raw_target = state_file or _default_state_file()
        if raw_target.is_symlink() or raw_target.parent.is_symlink():
            raise OSError("HouseholdObservationLedger refuses symlinked state path")
        self._state_file = raw_target.parent.resolve() / raw_target.name
        self._lock_file = self._state_file.with_suffix(".lock")
        if self._state_file.is_symlink() or self._lock_file.is_symlink():
            raise OSError("HouseholdObservationLedger refuses symlinked state or lock file")
        self._release_id = _normalize_release_id(release_id)
        self._runtime_metrics_probe = runtime_metrics_probe
        self._mem_lock = threading.Lock()
        self._ensure_release_window(now=now)

    @contextmanager
    def _locked_state(self, *, write: bool, now: datetime | None = None) -> Iterator[dict[str, Any]]:
        current = _utc_now(now)
        if self._state_file.parent.is_symlink():
            raise OSError("HouseholdObservationLedger refuses symlinked state directory")
        self._state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._state_file.parent, 0o700)
        except OSError:
            pass
        if self._state_file.is_symlink() or self._lock_file.is_symlink():
            raise OSError("HouseholdObservationLedger refuses symlinked state or lock file")
        with self._mem_lock:
            lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(self._lock_file), lock_flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                state = self._read_or_default(current)
                state_changed = self._reconcile_release_in_place(state, current)
                yield state
                if write or state_changed:
                    self._write_atomic(state)
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _ensure_release_window(self, *, now: datetime | None = None) -> None:
        with self._locked_state(write=True, now=now):
            pass

    def _read_or_default(self, current: datetime) -> dict[str, Any]:
        if self._state_file.is_symlink():
            raise OSError("HouseholdObservationLedger refuses symlinked state file")
        if self._state_file.exists():
            read_fd = os.open(
                str(self._state_file), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                with os.fdopen(read_fd, "r", encoding="utf-8") as handle:
                    raw_text = handle.read()
                loaded = json.loads(raw_text)
                if isinstance(loaded, dict) and loaded.get("contract") == "EvaHouseholdObservationLedger/v1":
                    return loaded
            except json.JSONDecodeError:
                pass
        return {
            "contract": "EvaHouseholdObservationLedger/v1",
            "current_release_id": self._release_id,
            "release_started_at": _iso(current),
            "unresolved_critical_errors": 0,
            "owner_acceptance": {
                "accepted": False,
                "accepted_at": None,
                "accepted_release_id": None,
                "note": None,
            },
            "daily_buckets": {},
            "prior_release_windows": [],
        }

    def _reconcile_release_in_place(self, state: dict[str, Any], current: datetime) -> bool:
        stored_release = str(state.get("current_release_id") or "")
        if stored_release == self._release_id:
            return False
        started_raw = str(state.get("release_started_at") or _iso(current))
        try:
            started_dt = _parse_iso(started_raw)
        except Exception:
            started_dt = current
        elapsed_hours = max(0.0, (current - started_dt).total_seconds() / 3600.0)
        active_days = self._count_active_days_since(state.get("daily_buckets") or {}, started_dt)
        prior = list(state.get("prior_release_windows") or [])
        prior.append(
            {
                "release_id": stored_release or "unknown",
                "started_at": _iso(started_dt),
                "ended_at": _iso(current),
                "contiguous_hours": round(elapsed_hours, 2),
                "distinct_active_days": active_days,
            }
        )
        state["prior_release_windows"] = prior[-MAX_PRIOR_WINDOWS:]
        state["current_release_id"] = self._release_id
        state["release_started_at"] = _iso(current)
        state["unresolved_critical_errors"] = 0
        state["owner_acceptance"] = {
            "accepted": False,
            "accepted_at": None,
            "accepted_release_id": None,
            "note": None,
        }
        return True

    def _write_atomic(self, state: dict[str, Any]) -> None:
        buckets = state.get("daily_buckets")
        if isinstance(buckets, dict) and len(buckets) > MAX_DAILY_BUCKETS:
            keep_keys = sorted(buckets.keys())[-MAX_DAILY_BUCKETS:]
            state["daily_buckets"] = {k: buckets[k] for k in keep_keys}
        tmp_path = self._state_file.with_name(f"{self._state_file.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp_path), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if self._state_file.is_symlink():
                raise OSError("HouseholdObservationLedger refuses symlinked state file")
            os.replace(tmp_path, self._state_file)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _bucket_for(state: dict[str, Any], current: datetime) -> dict[str, Any]:
        day_key = current.strftime("%Y-%m-%d")
        buckets = state.setdefault("daily_buckets", {})
        bucket = buckets.get(day_key)
        if not isinstance(bucket, dict):
            bucket = _empty_day_bucket(day_key)
            buckets[day_key] = bucket
        return bucket

    @staticmethod
    def _count_active_days_since(buckets: dict[str, Any], started_dt: datetime) -> int:
        start_day = started_dt.strftime("%Y-%m-%d")
        active = 0
        for day_key, bucket in buckets.items():
            if not isinstance(bucket, dict) or day_key < start_day:
                continue
            turns = (
                int(bucket.get("turns_jaap", 0))
                + int(bucket.get("turns_lola", 0))
                + int(bucket.get("turns_shared_household", 0))
            )
            reflexes = int(bucket.get("reflex_organogenesis_hits", 0))
            proactive = int(bucket.get("proactive_staged_accepted", 0)) + int(
                bucket.get("proactive_staged_dismissed", 0)
            )
            if turns + reflexes + proactive > 0:
                active += 1
        return active

    def record_turn(
        self,
        *,
        principal_id: str | None,
        channel: str,
        used_model: bool = True,
        known_cost_eur: float | None = None,
        now: datetime | None = None,
    ) -> None:
        current = _utc_now(now)
        safe_channel = channel if channel in _VALID_CHANNELS else "cockpit"
        member = _classify_household_member(principal_id)
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            if member is None:
                bucket["rejected_non_household_attempts"] = int(
                    bucket.get("rejected_non_household_attempts", 0)
                ) + 1
                return
            key = f"turns_{member}"
            bucket[key] = int(bucket.get(key, 0)) + 1
            ch_map = bucket.setdefault("channel_turns", {ch: 0 for ch in sorted(_VALID_CHANNELS)})
            ch_map[safe_channel] = int(ch_map.get(safe_channel, 0)) + 1
            if used_model:
                bucket["measured_model_calls"] = int(bucket.get("measured_model_calls", 0)) + 1
                if known_cost_eur is None or math.isnan(known_cost_eur) or known_cost_eur < 0:
                    bucket["unpriced_model_calls"] = int(bucket.get("unpriced_model_calls", 0)) + 1
                else:
                    bucket["known_cost_eur"] = round(
                        float(bucket.get("known_cost_eur", 0.0)) + float(known_cost_eur), 6
                    )

    def record_zero_token_idle_tick(self, *, count: int = 1, now: datetime | None = None) -> None:
        if count <= 0:
            return
        current = _utc_now(now)
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            bucket["zero_token_idle_ticks"] = int(bucket.get("zero_token_idle_ticks", 0)) + int(count)

    def record_reflex_hit(self, *, latency_ms: float, now: datetime | None = None) -> None:
        current = _utc_now(now)
        clamped_ms = max(0.0, min(10_000.0, float(latency_ms)))
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            bucket["reflex_organogenesis_hits"] = int(bucket.get("reflex_organogenesis_hits", 0)) + 1
            samples = list(bucket.get("reflex_latency_samples_ms") or [])
            samples.append(round(clamped_ms, 3))
            bucket["reflex_latency_samples_ms"] = samples[-64:]

    def record_proactive_decision(self, *, accepted: bool, now: datetime | None = None) -> None:
        current = _utc_now(now)
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            field = "proactive_staged_accepted" if accepted else "proactive_staged_dismissed"
            bucket[field] = int(bucket.get(field, 0)) + 1

    def record_runtime_fault(
        self, *, critical: bool = False, repaired: bool = False, now: datetime | None = None
    ) -> None:
        current = _utc_now(now)
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            bucket["runtime_errors_detected"] = int(bucket.get("runtime_errors_detected", 0)) + 1
            if repaired:
                bucket["self_repairs_verified"] = int(bucket.get("self_repairs_verified", 0)) + 1
            elif critical:
                state["unresolved_critical_errors"] = int(state.get("unresolved_critical_errors", 0)) + 1

    def record_self_repair_verified(self, *, resolved_critical: bool = True, now: datetime | None = None) -> None:
        current = _utc_now(now)
        with self._locked_state(write=True, now=current) as state:
            bucket = self._bucket_for(state, current)
            bucket["self_repairs_verified"] = int(bucket.get("self_repairs_verified", 0)) + 1
            if resolved_critical and int(state.get("unresolved_critical_errors", 0)) > 0:
                state["unresolved_critical_errors"] = int(state["unresolved_critical_errors"]) - 1

    def record_owner_acceptance(self, *, note: str, now: datetime | None = None) -> dict[str, Any]:
        cleaned_note = re.sub(r"\s+", " ", str(note or "")).strip()[:280]
        if not cleaned_note:
            raise ValueError("Owner acceptance requires a short confirmation note")
        current = _utc_now(now)
        with self._locked_state(write=True, now=current) as state:
            summary = self._build_summary(state, current, include_external_probe=False)
            if not summary["release_window"]["seven_day_gate_met"]:
                raise ValueError(
                    "F2.2 owner acceptance is refused until 7 contiguous days of stable household usage (F2.1) are complete"
                )
            state["owner_acceptance"] = {
                "accepted": True,
                "accepted_at": _iso(current),
                "accepted_release_id": state["current_release_id"],
                "note": cleaned_note,
            }
            return self._build_summary(state, current, include_external_probe=False)

    def snapshot(
        self,
        *,
        now: datetime | None = None,
        include_external_probe: bool = True,
    ) -> dict[str, Any]:
        current = _utc_now(now)
        with self._locked_state(write=False, now=current) as state:
            state_copy = json.loads(json.dumps(state))
        return self._build_summary(
            state_copy, current, include_external_probe=include_external_probe
        )

    def _build_summary(
        self,
        state: dict[str, Any],
        current: datetime,
        *,
        include_external_probe: bool,
    ) -> dict[str, Any]:
        started_dt = _parse_iso(str(state["release_started_at"]))
        elapsed_hours = max(0.0, (current - started_dt).total_seconds() / 3600.0)
        contiguous_days = int(elapsed_hours // 24.0)
        buckets: dict[str, Any] = state.get("daily_buckets") or {}
        start_day = started_dt.strftime("%Y-%m-%d")
        window_buckets = [
            b for day, b in sorted(buckets.items()) if isinstance(b, dict) and day >= start_day
        ]

        turns_jaap = sum(int(b.get("turns_jaap", 0)) for b in window_buckets)
        turns_lola = sum(int(b.get("turns_lola", 0)) for b in window_buckets)
        turns_shared = sum(int(b.get("turns_shared_household", 0)) for b in window_buckets)
        rejected_strangers = sum(int(b.get("rejected_non_household_attempts", 0)) for b in window_buckets)
        idle_ticks = sum(int(b.get("zero_token_idle_ticks", 0)) for b in window_buckets)
        reflex_hits = sum(int(b.get("reflex_organogenesis_hits", 0)) for b in window_buckets)
        accepted = sum(int(b.get("proactive_staged_accepted", 0)) for b in window_buckets)
        dismissed = sum(int(b.get("proactive_staged_dismissed", 0)) for b in window_buckets)
        errors = sum(int(b.get("runtime_errors_detected", 0)) for b in window_buckets)
        repairs = sum(int(b.get("self_repairs_verified", 0)) for b in window_buckets)
        model_calls = sum(int(b.get("measured_model_calls", 0)) for b in window_buckets)
        unpriced_calls = sum(int(b.get("unpriced_model_calls", 0)) for b in window_buckets)
        known_cost = sum(float(b.get("known_cost_eur", 0.0)) for b in window_buckets)

        channel_totals = {ch: 0 for ch in sorted(_VALID_CHANNELS)}
        latency_samples: list[float] = []
        for b in window_buckets:
            for ch, cnt in (b.get("channel_turns") or {}).items():
                if ch in channel_totals:
                    channel_totals[ch] += int(cnt)
            for sample in b.get("reflex_latency_samples_ms") or []:
                if isinstance(sample, (int, float)):
                    latency_samples.append(float(sample))

        reflex_p95_ms: float | None = None
        if latency_samples:
            ordered = sorted(latency_samples)
            idx = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
            reflex_p95_ms = round(ordered[idx], 3)

        total_events = idle_ticks + reflex_hits + model_calls
        zero_token_ratio: float | None = (
            round((idle_ticks + reflex_hits) / total_events, 4) if total_events > 0 else None
        )
        proactive_total = accepted + dismissed
        proactive_ratio: float | None = (
            round(accepted / proactive_total, 4) if proactive_total > 0 else None
        )

        if model_calls == 0:
            estimated_cost_eur: float | None = 0.0
            cost_status = "zero_model_calls"
        elif unpriced_calls > 0:
            estimated_cost_eur = None
            cost_status = "unknown_subscription_or_partial_credits"
        else:
            estimated_cost_eur = round(known_cost, 4)
            cost_status = "measured_exact"

        active_days = self._count_active_days_since(buckets, started_dt)
        unresolved_critical = int(state.get("unresolved_critical_errors", 0))
        seven_day_met = (
            elapsed_hours >= SEVEN_DAYS_HOURS
            and active_days >= REQUIRED_ACTIVE_DAYS
            and unresolved_critical == 0
        )
        acceptance = state.get("owner_acceptance") or {}
        owner_accepted = bool(
            seven_day_met
            and acceptance.get("accepted") is True
            and acceptance.get("accepted_release_id") == state["current_release_id"]
        )

        live_db_window: dict[str, Any] | None = None
        if include_external_probe and self._runtime_metrics_probe is not None:
            try:
                probed = self._runtime_metrics_probe()
                if isinstance(probed, dict):
                    live_db_window = probed
            except Exception:
                live_db_window = {"state": "unavailable"}

        return {
            "contract": "EvaHouseholdObservation/v1",
            "checked_at": _iso(current),
            "privacy_boundary": {
                "household_members": ["jaap", "lola"],
                "stores_message_content": False,
                "stores_credentials_or_ips": False,
            },
            "release_window": {
                "release_id": state["current_release_id"],
                "started_at": state["release_started_at"],
                "contiguous_hours": round(elapsed_hours, 2),
                "contiguous_days": contiguous_days,
                "required_contiguous_hours": SEVEN_DAYS_HOURS,
                "distinct_active_days": active_days,
                "required_active_days": REQUIRED_ACTIVE_DAYS,
                "unresolved_critical_errors": unresolved_critical,
                "seven_day_gate_met": seven_day_met,
                "f2_1_status": "ready_for_owner_judgment" if seven_day_met else "observing_in_progress",
                "f2_2_owner_accepted": owner_accepted,
                "prior_release_windows_count": len(state.get("prior_release_windows") or []),
            },
            "symbiotic_metrics": {
                "turns_jaap": turns_jaap,
                "turns_lola": turns_lola,
                "turns_shared_household": turns_shared,
                "rejected_non_household_attempts": rejected_strangers,
                "channel_turns": channel_totals,
                "zero_token_idle_ticks": idle_ticks,
                "reflex_organogenesis_hits": reflex_hits,
                "reflex_p95_ms": reflex_p95_ms,
                "zero_token_autonomy_ratio": zero_token_ratio,
                "proactive_staged_accepted": accepted,
                "proactive_staged_dismissed": dismissed,
                "proactive_acceptance_ratio": proactive_ratio,
                "runtime_errors_detected": errors,
                "self_repairs_verified": repairs,
                "measured_model_calls": model_calls,
                "unpriced_model_calls": unpriced_calls,
                "estimated_runtime_cost_eur": estimated_cost_eur,
                "cost_measurement_status": cost_status,
            },
            "live_database_window": live_db_window,
        }


__all__ = ["HouseholdObservationLedger"]
