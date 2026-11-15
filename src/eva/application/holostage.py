"""Fase 4A & 4C — Eva HoloStage Shared Stage Engine, Scoped Device Grants & Sub-300ms Clause Speech Chunker.

Provides one unified 3D + visual-card stage across:
- Owner Web Cockpit (`cockpit`)
- UniFi Screen wall kiosk with Gaze-to-Talk & hardware AEC (`unifi_screen`)
- Google Cast / Android TV 10-foot receiver (`tv_cast`)
- `Eva Pocket` mobile PWA (`pocket`)

Security & Privacy Invariants:
- Scoped `stg_<256-bit>` device tokens are stored strictly as SHA-256 digests in `0o600` atomic storage.
- Private cards (`household_today`, `visitors_review`, `private_household` spotlights) are redacted
  SERVER-SIDE before serialization whenever `BiometricPresenceFusion.is_privacy_shield_active()` is True
  or when accessed by a non-private ambient stage token (`allow_private_cards_when_alone == False`).
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit

from eva.application.biometric_presence import BiometricPresenceFusion
from eva.application.home_proprioception import HomeProprioceptionManager, is_safe_lan_ipv4
from eva.application.household_head_settings import HouseholdHeadSettings
from eva.contracts.holostage import (
    ShoppingListItem,
    StageCardKind,
    StageDeviceGrantSummary,
    StageFormFactor,
    StageSpotlightCard,
)

STAGE_TOKEN_PATTERN = re.compile(r"^stg_[A-Za-z0-9_-]{40,48}$")
MAX_SHOPPING_ITEMS = 64
MAX_SPOTLIGHT_CARDS = 8
MAX_DEVICE_GRANTS = 24
VALID_STAGE_CARD_KINDS: tuple[StageCardKind, ...] = (
    "shopping_list",
    "household_today",
    "home_overview",
    "visitors_review",
    "custom_spotlight",
)
PRIVATE_STAGE_CARD_KINDS: frozenset[str] = frozenset({"household_today", "visitors_review"})

_DUTCH_WEEKDAYS = (
    "maandag",
    "dinsdag",
    "woensdag",
    "donderdag",
    "vrijdag",
    "zaterdag",
    "zondag",
)

_ADD_SHOPPING_RE = re.compile(
    r"\b(?:zet|voeg|plaats|schrijf)\s+(.{1,200}?)\s+(?:op|aan|bij)\s+(?:de\s+)?boodschappen(?:lijst|lijstje)?\b",
    re.IGNORECASE,
)
_REMOVE_SHOPPING_RE = re.compile(
    r"\b(?:haal|verwijder|streep)\s+(.{1,200}?)\s+(?:van|uit|door\s+op)\s+(?:de\s+)?boodschappen(?:lijst|lijstje)?\b",
    re.IGNORECASE,
)


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _clean_text(value: str, *, field_name: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} moet tekst zijn")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise ValueError(f"{field_name} mag niet leeg zijn")
    if len(cleaned) > max_len:
        raise ValueError(f"{field_name} is te lang (max {max_len} tekens)")
    return cleaned


def _strip_markdown_for_speech(text: str) -> str:
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class SentenceBoundaryChunker:
    """Low-latency (~250ms first-clause) streaming speech chunker.

    Emits spoken Dutch clauses as soon as the first natural sentence or clause
    boundary arrives so TTS/WebAudio starts speaking immediately without waiting
    for the full LLM completion.
    """

    def __init__(self, *, first_clause_min_words: int = 5, steady_min_words: int = 9) -> None:
        self._buf = ""
        self._emitted_any = False
        self._first_min = max(3, first_clause_min_words)
        self._steady_min = max(5, steady_min_words)

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            min_words = self._first_min if not self._emitted_any else self._steady_min
            # Strong terminal punctuation (., !, ?, :, ;) or soft clause comma/dash once enough words arrived
            strong_match = re.search(r"[.!?;:]\s+", self._buf)
            soft_match = re.search(r"(?:,|—|–)\s+", self._buf) if not self._emitted_any else None

            candidate_end: int | None = None
            if strong_match is not None:
                prefix = _strip_markdown_for_speech(self._buf[: strong_match.end()])
                if len(prefix.split()) >= max(3, min_words - 1):
                    candidate_end = strong_match.end()
            if candidate_end is None and soft_match is not None:
                prefix = _strip_markdown_for_speech(self._buf[: soft_match.end()])
                if len(prefix.split()) >= min_words + 1:
                    candidate_end = soft_match.end()
            if candidate_end is None and len(self._buf) >= 180:
                ws = self._buf.rfind(" ", 0, 180)
                if ws > 32:
                    candidate_end = ws + 1
                elif len(self._buf) >= 240:
                    candidate_end = 180

            if candidate_end is None:
                break

            raw_chunk = self._buf[:candidate_end]
            self._buf = self._buf[candidate_end:]
            spoken = _strip_markdown_for_speech(raw_chunk)
            if spoken:
                self._emitted_any = True
                out.append(spoken)
        return out

    def flush(self) -> list[str]:
        rem = _strip_markdown_for_speech(self._buf)
        self._buf = ""
        return [rem] if rem else []


class HoloStageManager:
    """Atomic, file-locked (`0o600`) shared visual stage & scoped device pairing manager."""

    _SHARED_BIOMETRICS_LOCK = threading.Lock()
    _SHARED_BIOMETRICS: OrderedDict[str, BiometricPresenceFusion] = OrderedDict()
    _SHARED_VISUAL_LOCK = threading.Lock()
    _SHARED_VISUAL_CONTEXT: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        household_settings: HouseholdHeadSettings | None = None,
        proprioception: HomeProprioceptionManager | None = None,
    ) -> None:
        base = Path(
            directory
            or os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
        )
        self.directory = base
        self.state_file = base / "holostage-state.json"
        self.lock_file = base / ".holostage-state.lock"
        self.household_settings = household_settings or HouseholdHeadSettings(base)
        self.proprioception = proprioception or HomeProprioceptionManager(base)
        key = str(base.resolve(strict=False))
        with self._SHARED_BIOMETRICS_LOCK:
            bio = self._SHARED_BIOMETRICS.get(key)
            if bio is None:
                bio = BiometricPresenceFusion(base, self.household_settings)
                self._SHARED_BIOMETRICS[key] = bio
                while len(self._SHARED_BIOMETRICS) > 16:
                    self._SHARED_BIOMETRICS.popitem(last=False)
            else:
                self._SHARED_BIOMETRICS.move_to_end(key)
        self.biometrics = bio

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

    def _default_state(self, now: datetime) -> dict[str, Any]:
        ts = _iso(now)
        return {
            "contract": "EvaHoloStageStore/v1",
            "revision": 1,
            "updated_at": ts,
            "active_card_focus": "none",
            "lan_stage_origin": "",
            "conjured_cards": [],
            "live_utterance": None,
            "stage_animation": None,
            "shopping_list": [],
            "household_notes": [
                {
                    "note_id": "lola_kookdagen",
                    "member": "Lola",
                    "title": "Lola's vaste kookdagen",
                    "detail_nl": "Maandag en dinsdag kookt Lola (favoriet: verse sushi & pokébowls).",
                    "days": ["maandag", "dinsdag"],
                },
                {
                    "note_id": "gezin_kern",
                    "member": "Jaap & Lola",
                    "title": "Gezinsagenda & Somtoday",
                    "detail_nl": "Alleen zichtbaar wanneer geen onbekenden of gasten meekijken.",
                    "days": [],
                },
            ],
            "spotlights": [],
            "device_grants": [],
        }

    def _read_unlocked(self, now: datetime) -> dict[str, Any]:
        if not self.state_file.is_file():
            return self._default_state(now)
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("contract") != "EvaHoloStageStore/v1":
                raise ValueError("invalid holostage contract")
        except Exception:
            return self._default_state(now)

        dirty = False
        # Expire spotlight cards, conjured overlays, live utterances & device grants
        kept_spotlights = []
        for sp in raw.get("spotlights") or []:
            if isinstance(sp, dict):
                try:
                    if _parse_iso(str(sp["expires_at"])) > now:
                        kept_spotlights.append(sp)
                    else:
                        dirty = True
                except Exception:
                    dirty = True
        raw["spotlights"] = kept_spotlights

        kept_conjured = []
        seen_kinds: set[str] = set()
        for cj in raw.get("conjured_cards") or []:
            if isinstance(cj, dict):
                kind = str(cj.get("card_kind") or "")
                if kind not in VALID_STAGE_CARD_KINDS or kind in seen_kinds:
                    dirty = True
                    continue
                try:
                    if _parse_iso(str(cj["expires_at"])) > now:
                        kept_conjured.append(cj)
                        seen_kinds.add(kind)
                    else:
                        dirty = True
                except Exception:
                    dirty = True
        raw["conjured_cards"] = kept_conjured

        utt = raw.get("live_utterance")
        if isinstance(utt, dict):
            try:
                if _parse_iso(str(utt["expires_at"])) <= now:
                    raw["live_utterance"] = None
                    dirty = True
            except Exception:
                raw["live_utterance"] = None
                dirty = True
        elif utt is not None:
            raw["live_utterance"] = None
            dirty = True

        anim = raw.get("stage_animation")
        if isinstance(anim, dict):
            try:
                if _parse_iso(str(anim["expires_at"])) <= now:
                    raw["stage_animation"] = None
                    dirty = True
            except Exception:
                raw["stage_animation"] = None
                dirty = True
        elif anim is not None:
            raw["stage_animation"] = None
            dirty = True

        for gr in raw.get("device_grants") or []:
            if isinstance(gr, dict) and gr.get("status") == "active":
                try:
                    if _parse_iso(str(gr["expires_at"])) <= now:
                        gr["status"] = "expired"
                        dirty = True
                except Exception:
                    gr["status"] = "expired"
                    dirty = True

        if dirty:
            self._write_unlocked(raw)
        return raw

    def _read_readonly(self, now: datetime) -> dict[str, Any]:
        if not self.state_file.is_file():
            return self._default_state(now)
        with self._locked():
            return self._read_unlocked(now)

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        tmp = self.directory / f".holostage-state.tmp.{os.getpid()}.{secrets.token_hex(6)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(
                fd,
                json.dumps(state, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"),
            )
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
        os.replace(str(tmp), str(self.state_file))

    @staticmethod
    def _conjure_in_state_unlocked(
        state: dict[str, Any],
        card_kind: StageCardKind,
        *,
        ts: datetime,
        ttl_minutes: int = 15,
    ) -> None:
        clamped = max(1, min(1440, int(ttl_minutes)))
        entry = {
            "card_kind": card_kind,
            "conjured_at": _iso(ts),
            "expires_at": _iso(ts + timedelta(minutes=clamped)),
        }
        existing = [
            dict(c)
            for c in (state.get("conjured_cards") or [])
            if isinstance(c, dict) and c.get("card_kind") != card_kind
        ]
        existing.insert(0, entry)
        state["conjured_cards"] = existing[: len(VALID_STAGE_CARD_KINDS)]
        state["active_card_focus"] = card_kind

    def conjure_card(
        self,
        card_kind: StageCardKind,
        *,
        ttl_minutes: int = 15,
        allow_private: bool = True,
        now: datetime | None = None,
    ) -> list[str]:
        if card_kind not in VALID_STAGE_CARD_KINDS:
            raise ValueError("Onbekend podiumkaarttype")
        if card_kind in PRIVATE_STAGE_CARD_KINDS and not allow_private:
            raise PermissionError("Privé-gezinskaart kan niet op een gedeeld of afgeschermd scherm worden getoond")
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            self._conjure_in_state_unlocked(state, card_kind, ts=ts, ttl_minutes=ttl_minutes)
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
            return [str(x["card_kind"]) for x in state["conjured_cards"]]

    def dismiss_conjured_card(
        self,
        card_kind: str | None = None,
        *,
        allow_private: bool = True,
        now: datetime | None = None,
    ) -> list[str]:
        ts = _utc_now(now)
        target = (card_kind or "all").strip()
        with self._locked():
            state = self._read_unlocked(ts)
            current = [dict(c) for c in (state.get("conjured_cards") or []) if isinstance(c, dict)]
            if target in ("", "all", "*"):
                kept = [
                    c
                    for c in current
                    if (c.get("card_kind") in PRIVATE_STAGE_CARD_KINDS and not allow_private)
                ]
                if allow_private:
                    state["spotlights"] = []
                else:
                    state["spotlights"] = [
                        s
                        for s in (state.get("spotlights") or [])
                        if isinstance(s, dict) and s.get("privacy_level") == "private_household"
                    ]
                state["conjured_cards"] = kept
                state["active_card_focus"] = str(kept[0]["card_kind"]) if kept else "none"
            else:
                if target in PRIVATE_STAGE_CARD_KINDS and not allow_private:
                    raise PermissionError("Privé-gezinskaart kan niet vanaf een gedeeld scherm worden gesloten")
                kept = [c for c in current if c.get("card_kind") != target]
                state["conjured_cards"] = kept
                if state.get("active_card_focus") == target:
                    state["active_card_focus"] = str(kept[0]["card_kind"]) if kept else "none"
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
            return [str(x["card_kind"]) for x in state["conjured_cards"]]

    def record_stage_utterance(
        self,
        *,
        eva_text: str,
        speaker_text: str = "",
        privacy_level: Literal["shared", "private_household"] = "shared",
        ttl_seconds: int = 45,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        spoken = _strip_markdown_for_speech(eva_text)[:900]
        if not spoken:
            return {}
        clean_user = re.sub(r"\s+", " ", (speaker_text or "")).strip()[:300]
        clamped_ttl = max(5, min(300, int(ttl_seconds)))
        utt = {
            "utterance_id": "utt_" + secrets.token_hex(6),
            "speaker_text": clean_user,
            "eva_text": spoken,
            "privacy_level": privacy_level,
            "created_at": _iso(ts),
            "expires_at": _iso(ts + timedelta(seconds=clamped_ttl)),
        }
        with self._locked():
            state = self._read_unlocked(ts)
            state["live_utterance"] = utt
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
        return utt

    # --- Shopping List Operations ---
    def add_shopping_item(
        self,
        *,
        label: str,
        quantity: str = "",
        added_by: str = "gezin",
        now: datetime | None = None,
    ) -> ShoppingListItem:
        ts = _utc_now(now)
        clean_label = _clean_text(label, field_name="Boodschap", max_len=120)
        clean_qty = re.sub(r"\s+", " ", (quantity or "")).strip()[:32]
        clean_by = re.sub(r"\s+", " ", (added_by or "gezin")).strip()[:48] or "gezin"
        norm_key = clean_label.casefold()

        with self._locked():
            state = self._read_unlocked(ts)
            items = [dict(x) for x in (state.get("shopping_list") or []) if isinstance(x, dict)]
            for existing in items:
                if str(existing.get("label") or "").casefold() == norm_key:
                    existing["checked"] = False
                    if clean_qty:
                        existing["quantity"] = clean_qty
                    existing["added_by"] = clean_by
                    existing["updated_at"] = _iso(ts)
                    validated = ShoppingListItem.model_validate(existing)
                    state["shopping_list"] = items
                    self._conjure_in_state_unlocked(state, "shopping_list", ts=ts)
                    state["updated_at"] = _iso(ts)
                    self._write_unlocked(state)
                    return validated

            if len(items) >= MAX_SHOPPING_ITEMS:
                # Evict oldest checked item first, else oldest item
                checked_idx = next((i for i, x in enumerate(items) if x.get("checked")), None)
                items.pop(checked_idx if checked_idx is not None else 0)

            item_id = "shop_" + hashlib.sha256(f"{norm_key}:{ts.timestamp()}".encode("utf-8")).hexdigest()[:10]
            created = ShoppingListItem(
                item_id=item_id,
                label=clean_label,
                quantity=clean_qty,
                checked=False,
                added_by=clean_by,
                updated_at=_iso(ts),
            )
            items.append(created.model_dump(mode="json"))
            state["shopping_list"] = items
            self._conjure_in_state_unlocked(state, "shopping_list", ts=ts)
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
            return created

    def toggle_or_remove_shopping_item(
        self,
        *,
        target: str,
        action: Literal["toggle", "remove", "clear_checked"],
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            items = [dict(x) for x in (state.get("shopping_list") or []) if isinstance(x, dict)]
            if action == "clear_checked":
                items = [x for x in items if not x.get("checked")]
            else:
                needle = (target or "").strip().casefold()
                if not needle:
                    raise ValueError("Kies een boodschap om bij te werken")
                matched = False
                next_items: list[dict[str, Any]] = []
                for x in items:
                    is_hit = (
                        str(x.get("item_id") or "").casefold() == needle
                        or str(x.get("label") or "").casefold() == needle
                    )
                    if is_hit:
                        matched = True
                        if action == "toggle":
                            x["checked"] = not bool(x.get("checked"))
                            x["updated_at"] = _iso(ts)
                            next_items.append(ShoppingListItem.model_validate(x).model_dump(mode="json"))
                    else:
                        next_items.append(x)
                if not matched:
                    raise ValueError("Boodschap niet gevonden op de lijst")
                items = next_items
            state["shopping_list"] = items
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
            return items

    def apply_spoken_stage_side_effects(
        self,
        text: str,
        *,
        speaker_name: str = "gezin",
        allow_private: bool = True,
        now: datetime | None = None,
    ) -> list[str]:
        """Fast (<1ms) deterministic stage card update & conjure-on-demand when asked to show/hide cards."""
        if not isinstance(text, str) or not text.strip():
            return []
        sample = text[:360]
        lowered = sample.casefold()
        ts = _utc_now(now)
        effects: list[str] = []

        # 1. Dismiss / clear conjured cards ("haal maar weg", "haal de kaarten maar weg", "sluit de kaarten", "alleen eva")
        if "boodschap" not in lowered and (
            any(
                phrase in lowered
                for phrase in (
                    "haal maar weg",
                    "haal weg",
                    "sluit kaart",
                    "sluit de kaart",
                    "verberg kaart",
                    "verberg het overzicht",
                    "maak het scherm leeg",
                    "alleen eva",
                )
            )
            or re.search(r"\b(?:haal|doe|verberg|sluit)\b[\w\s]{0,36}\b(?:weg|kaart|kaarten|overzicht)\b", lowered)
        ):
            self.dismiss_conjured_card("all", allow_private=allow_private, now=ts)
            return ["~ kaarten verborgen"]

        # 2. Shopping list mutations & conjuring
        if "boodschap" in lowered:
            add_match = _ADD_SHOPPING_RE.search(sample)
            if add_match:
                raw_items = re.split(r"\s*(?:,|\ben\b)\s*", add_match.group(1))
                for part in raw_items[:8]:
                    cleaned = re.sub(
                        r"^(?:een|wat|nog|ook|graag)\s+", "", part.strip(), flags=re.IGNORECASE
                    ).strip(" .!?")
                    if 1 < len(cleaned) <= 80:
                        item = self.add_shopping_item(label=cleaned, added_by=speaker_name, now=ts)
                        effects.append(f"+ {item.label}")
            rem_match = _REMOVE_SHOPPING_RE.search(sample)
            if rem_match:
                target_txt = rem_match.group(1).strip(" .!?")
                try:
                    self.toggle_or_remove_shopping_item(target=target_txt, action="remove", now=ts)
                    self.conjure_card("shopping_list", now=ts)
                    effects.append(f"- {target_txt}")
                except ValueError:
                    pass
            if not effects and any(
                kw in lowered for kw in ("laat", "toon", "zien", "tover", "staat er", "welke", "lees")
            ):
                self.conjure_card("shopping_list", now=ts)
                effects.append("✨ boodschappenlijst")

        # 3. Conjure household day / cooking schedule on demand (only when private access is allowed)
        if allow_private and any(
            kw in lowered for kw in ("kookdag", "wie kookt", "gezinsdag", "gezinsagenda")
        ):
            self.conjure_card("household_today", allow_private=True, now=ts)
            effects.append("✨ gezinsdag")
        elif allow_private and ("planning" in lowered or "agenda" in lowered) and any(
            kw in lowered for kw in ("laat", "toon", "zien", "tover", "scherm")
        ):
            self.conjure_card("household_today", allow_private=True, now=ts)
            effects.append("✨ gezinsdag")

        # 4. Conjure home / rooms overview on demand
        if any(
            kw in lowered
            for kw in ("huisoverzicht", "kamers zien", "toon kamers", "laat het huis zien", "cast zien")
        ):
            self.conjure_card("home_overview", now=ts)
            effects.append("✨ huisoverzicht")

        # 5. Conjure visitors review on demand (private)
        if allow_private and any(
            kw in lowered for kw in ("bezoekers zien", "toon bezoekers", "wie stond er aan de deur")
        ):
            self.conjure_card("visitors_review", allow_private=True, now=ts)
            effects.append("✨ bezoekers")

        return effects

    def trigger_stage_animation(
        self,
        *,
        effect: str = "aurora_reveal",
        label_nl: str = "",
        privacy_level: Literal["shared", "private_household"] = "shared",
        ttl_seconds: int = 24,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        valid_effects = {
            "aurora_reveal",
            "celebration",
            "cinema_spotlight",
            "pulse_energy",
            "wave_greeting",
        }
        clean_effect = effect if effect in valid_effects else "aurora_reveal"
        clean_label = re.sub(r"\s+", " ", (label_nl or "")).strip()[:96]
        clamped_ttl = max(6, min(120, int(ttl_seconds)))
        anim = {
            "animation_id": "anim_" + secrets.token_hex(6),
            "effect": clean_effect,
            "label_nl": clean_label,
            "privacy_level": privacy_level,
            "created_at": _iso(ts),
            "expires_at": _iso(ts + timedelta(seconds=clamped_ttl)),
        }
        with self._locked():
            state = self._read_unlocked(ts)
            state["stage_animation"] = anim
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
        return anim

    # --- Spotlight Cards ("Op het scherm toveren") ---
    def summon_spotlight_card(
        self,
        *,
        title: str,
        body_nl: str,
        items: list[str] | None = None,
        theme: str = "spotlight",
        privacy_level: Literal["shared", "private_household"] = "shared",
        ttl_minutes: int = 60,
        now: datetime | None = None,
    ) -> StageSpotlightCard:
        ts = _utc_now(now)
        clean_title = _clean_text(title, field_name="Kaarttitel", max_len=96)
        clean_body = _clean_text(body_nl, field_name="Kaartinhoud", max_len=800)
        clean_items = [_clean_text(str(x), field_name="Kaartregel", max_len=140) for x in (items or [])[:16] if str(x).strip()]
        clean_theme = theme if theme in {"spotlight", "leaderboard", "climate", "celebration", "aurora"} else "spotlight"
        clamped_ttl = max(1, min(1440, int(ttl_minutes)))
        card_id = "card_" + secrets.token_hex(5)
        card = StageSpotlightCard(
            card_id=card_id,
            title=clean_title,
            body_nl=clean_body,
            items=clean_items,
            theme=clean_theme,
            privacy_level=privacy_level,
            created_at=_iso(ts),
            expires_at=_iso(ts + timedelta(minutes=clamped_ttl)),
        )
        with self._locked():
            state = self._read_unlocked(ts)
            cards = [c for c in (state.get("spotlights") or []) if isinstance(c, dict)]
            cards.insert(0, card.model_dump(mode="json"))
            state["spotlights"] = cards[:MAX_SPOTLIGHT_CARDS]
            self._conjure_in_state_unlocked(state, "custom_spotlight", ts=ts, ttl_minutes=clamped_ttl)
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
        return card

    def dismiss_spotlight_card(
        self, card_id: str, *, allow_private: bool = True, now: datetime | None = None
    ) -> None:
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            cards = [c for c in (state.get("spotlights") or []) if isinstance(c, dict)]
            for c in cards:
                if c.get("card_id") == card_id and c.get("privacy_level") == "private_household" and not allow_private:
                    raise PermissionError(
                        "Privé-gezinskaart kan niet vanaf een gedeeld of afgeschermd scherm worden gesloten"
                    )
            state["spotlights"] = [c for c in cards if c.get("card_id") != card_id]
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)

    # --- Scoped Stage Device Pairing Tokens (Chromecast / UniFi Screen / Eva Pocket) ---
    def issue_device_token(
        self,
        *,
        label: str,
        form_factor: StageFormFactor,
        room_id: str = "woonkamer",
        allow_private_cards_when_alone: bool = False,
        allow_owner_voice: bool = False,
        ttl_hours: int = 720,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        clean_label = _clean_text(label, field_name="Schermnaam", max_len=80)
        clean_room = re.sub(r"[^a-z0-9_-]+", "_", (room_id or "woonkamer").lower()).strip("_")[:40] or "woonkamer"
        clamped_ttl = max(1, min(2160, int(ttl_hours)))
        eff_allow_priv = bool(allow_private_cards_when_alone and form_factor != "tv_cast")
        eff_owner_voice = bool(allow_owner_voice and form_factor == "pocket" and eff_allow_priv)

        raw_token = "stg_" + secrets.token_urlsafe(32)
        token_sha256 = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        grant_id = "stg_" + token_sha256[:12]
        created_iso = _iso(ts)
        expires_iso = _iso(ts + timedelta(hours=clamped_ttl))

        record = {
            "grant_id": grant_id,
            "token_sha256": token_sha256,
            "label": clean_label,
            "form_factor": form_factor,
            "room_id": clean_room,
            "allow_private_cards_when_alone": eff_allow_priv,
            "allow_owner_voice": eff_owner_voice,
            "status": "active",
            "created_at": created_iso,
            "expires_at": expires_iso,
        }
        with self._locked():
            state = self._read_unlocked(ts)
            grants = [g for g in (state.get("device_grants") or []) if isinstance(g, dict)]
            if form_factor == "tv_cast":
                for g in grants:
                    if (
                        g.get("status") == "active"
                        and g.get("form_factor") == "tv_cast"
                        and g.get("label") == clean_label
                    ):
                        g["status"] = "revoked"
            active_grants = [g for g in grants if g.get("status") == "active"]
            if len(active_grants) >= MAX_DEVICE_GRANTS:
                raise ValueError(f"Maximaal {MAX_DEVICE_GRANTS} actieve schermkoppelingen toegestaan")
            grants.insert(0, record)
            state["device_grants"] = grants[: MAX_DEVICE_GRANTS * 2]
            state["updated_at"] = created_iso
            self._write_unlocked(state)

        return {
            "grant": StageDeviceGrantSummary(
                grant_id=grant_id,
                label=clean_label,
                form_factor=form_factor,
                room_id=clean_room,
                allow_private_cards_when_alone=eff_allow_priv,
                allow_owner_voice=eff_owner_voice,
                status="active",
                created_at=created_iso,
                expires_at=expires_iso,
            ).model_dump(mode="json"),
            "raw_token": raw_token,
            "pair_path": f"/internal/v1/dashboard/holostage?mode={form_factor}#{raw_token}",
        }

    def authenticate_device_token(
        self, raw_token: str | None, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        if not isinstance(raw_token, str) or not STAGE_TOKEN_PATTERN.match(raw_token):
            return None
        ts = _utc_now(now)
        candidate_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        if not self.state_file.is_file():
            return None
        with self._locked():
            state = self._read_unlocked(ts)
            for gr in state.get("device_grants") or []:
                if not isinstance(gr, dict) or gr.get("status") != "active":
                    continue
                stored_hash = str(gr.get("token_sha256") or "")
                if hmac.compare_digest(stored_hash, candidate_hash):
                    ff = gr.get("form_factor")
                    allow_priv = bool(gr.get("allow_private_cards_when_alone", False) and ff != "tv_cast")
                    return {
                        "grant_id": gr["grant_id"],
                        "label": gr["label"],
                        "form_factor": ff,
                        "room_id": gr["room_id"],
                        "allow_private_cards_when_alone": allow_priv,
                        "allow_owner_voice": bool(
                            gr.get("allow_owner_voice", False) and ff == "pocket" and allow_priv
                        ),
                        "expires_at": gr["expires_at"],
                    }
        return None

    @staticmethod
    def validate_lan_stage_origin(origin: str) -> str:
        clean = (origin or "").strip().rstrip("/")
        if not 10 <= len(clean) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in clean):
            raise ValueError("Ongeldige LAN HoloStage-origin")
        parts = urlsplit(clean)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError("LAN HoloStage-origin moet http(s)://<lan-ip>:<poort> zijn")
        if not is_safe_lan_ipv4(parts.hostname, allow_docker_bridge=False):
            raise ValueError("LAN HoloStage-origin vereist een veilig lokaal RFC1918 IPv4-adres")
        if parts.port is not None and not (1024 <= parts.port <= 65535):
            raise ValueError("LAN HoloStage-poort moet tussen 1024 en 65535 liggen")
        return clean

    def set_lan_stage_origin(self, origin: str, *, now: datetime | None = None) -> str:
        clean = self.validate_lan_stage_origin(origin)
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            state["lan_stage_origin"] = clean
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
        return clean

    def get_lan_stage_origin(self, *, now: datetime | None = None) -> str:
        env_raw = os.environ.get("EVA_HOLOSTAGE_LAN_ORIGIN", "").strip()
        if env_raw:
            try:
                return self.validate_lan_stage_origin(env_raw)
            except ValueError:
                pass
        ts = _utc_now(now)
        raw = self._read_readonly(ts)
        stored = str(raw.get("lan_stage_origin") or "").strip()
        if stored:
            try:
                return self.validate_lan_stage_origin(stored)
            except ValueError:
                pass
        file_origin = self.directory / "holostage_lan_origin.txt"
        if file_origin.is_file():
            try:
                return self.validate_lan_stage_origin(file_origin.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                return ""
        return ""

    @staticmethod
    def validate_public_stage_origin(origin: str) -> str:
        clean = (origin or "").strip().rstrip("/")
        if not 12 <= len(clean) <= 160 or any(ord(c) < 33 or ord(c) > 126 for c in clean):
            raise ValueError("Ongeldige publieke HTTPS HoloStage-origin")
        parts = urlsplit(clean)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https"
            or not host
            or host in {"localhost", "127.0.0.1", "::1"}
            or "." not in host
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Publieke HoloStage-origin moet https://<domein> zijn")
        if parts.port is not None and parts.port not in (443, 8443, 10000):
            raise ValueError("Publieke HTTPS HoloStage-poort moet 443, 8443 of 10000 zijn")
        return clean

    def set_public_stage_origin(self, origin: str, *, now: datetime | None = None) -> str:
        clean = self.validate_public_stage_origin(origin)
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            state["public_stage_origin"] = clean
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)
        return clean

    def get_public_stage_origin(self, *, now: datetime | None = None) -> str:
        env_raw = os.environ.get("EVA_HOLOSTAGE_PUBLIC_ORIGIN", "").strip()
        if env_raw:
            try:
                return self.validate_public_stage_origin(env_raw)
            except ValueError:
                pass
        file_origin = self.directory / "holostage_public_origin.txt"
        if file_origin.is_file():
            try:
                return self.validate_public_stage_origin(file_origin.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass
        ts = _utc_now(now)
        raw = self._read_readonly(ts)
        stored = str(raw.get("public_stage_origin") or "").strip()
        if stored:
            try:
                return self.validate_public_stage_origin(stored)
            except ValueError:
                return ""
        return ""

    def revoke_device_grant(self, grant_id: str, *, now: datetime | None = None) -> None:
        ts = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(ts)
            found = False
            for gr in state.get("device_grants") or []:
                if isinstance(gr, dict) and gr.get("grant_id") == grant_id:
                    gr["status"] = "revoked"
                    found = True
            if not found:
                raise ValueError("Schermkoppeling niet gevonden")
            state["updated_at"] = _iso(ts)
            self._write_unlocked(state)

    # --- Ephemeral In-Memory Camera Co-Pilot Context (Never Written to Disk) ---
    def _visual_store_key(self) -> str:
        return str(self.directory.resolve(strict=False))

    def record_ephemeral_visual_context(
        self,
        *,
        summary_nl: str,
        source_label: str = "Eva Pocket Camera",
        image_sha256: str = "",
        ttl_seconds: int = 90,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        if self.biometrics.is_privacy_shield_active(now=ts)[0]:
            self.clear_ephemeral_visual_context()
            raise PermissionError("Privéschild is actief: camerawaarneming wordt niet aangenomen")
        clean_summary = _clean_text(summary_nl, field_name="Visuele waarneming", max_len=950)
        clean_source = re.sub(r"\s+", " ", (source_label or "Eva Pocket Camera")).strip()[:64] or "Eva Pocket Camera"
        clamped_ttl = max(10, min(180, int(ttl_seconds)))
        entry = {
            "observation_id": "vis_" + secrets.token_hex(6),
            "summary_nl": clean_summary,
            "source_label": clean_source,
            "image_sha256": (image_sha256 or "")[:64],
            "captured_at": _iso(ts),
            "expires_at": _iso(ts + timedelta(seconds=clamped_ttl)),
            "_captured_epoch": ts.timestamp(),
            "_expires_epoch": ts.timestamp() + clamped_ttl,
        }
        key = self._visual_store_key()
        with self._SHARED_VISUAL_LOCK:
            self._SHARED_VISUAL_CONTEXT[key] = entry
            self._SHARED_VISUAL_CONTEXT.move_to_end(key)
            while len(self._SHARED_VISUAL_CONTEXT) > 16:
                self._SHARED_VISUAL_CONTEXT.popitem(last=False)
        return self._public_visual_entry(entry, ts)

    @staticmethod
    def _public_visual_entry(entry: dict[str, Any], ts: datetime) -> dict[str, Any]:
        rem = max(0, int(round(float(entry.get("_expires_epoch", 0.0)) - ts.timestamp())))
        age = max(0, int(round(ts.timestamp() - float(entry.get("_captured_epoch", ts.timestamp())))))
        return {
            "observation_id": str(entry["observation_id"]),
            "summary_nl": str(entry["summary_nl"]),
            "source_label": str(entry["source_label"]),
            "image_sha256": str(entry.get("image_sha256") or ""),
            "captured_at": str(entry["captured_at"]),
            "expires_at": str(entry["expires_at"]),
            "age_seconds": age,
            "remaining_seconds": rem,
        }

    def get_ephemeral_visual_context(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        ts = _utc_now(now)
        if self.biometrics.is_privacy_shield_active(now=ts)[0]:
            self.clear_ephemeral_visual_context()
            return None
        key = self._visual_store_key()
        with self._SHARED_VISUAL_LOCK:
            entry = self._SHARED_VISUAL_CONTEXT.get(key)
            if entry is None:
                return None
            if float(entry.get("_expires_epoch", 0.0)) <= ts.timestamp():
                self._SHARED_VISUAL_CONTEXT.pop(key, None)
                return None
            if ts.timestamp() < float(entry.get("_captured_epoch", 0.0)) - 2.0:
                return None
            return self._public_visual_entry(entry, ts)

    def clear_ephemeral_visual_context(self) -> bool:
        key = self._visual_store_key()
        with self._SHARED_VISUAL_LOCK:
            return self._SHARED_VISUAL_CONTEXT.pop(key, None) is not None

    # --- Full Stage Payload with Strict Server-Side Privacy Redaction ---
    def build_stage_state(
        self,
        *,
        viewer_kind: Literal["owner", "private_stage", "ambient_stage"] = "owner",
        viewer_label: str = "Gezinshoofd Cockpit",
        form_factor: StageFormFactor = "cockpit",
        allow_owner_voice: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = _utc_now(now)
        raw = self._read_readonly(ts)
        bio_snap = self.biometrics.snapshot(now=ts)
        shield_active = bool(bio_snap["privacy_shield_active"])
        if shield_active:
            self.clear_ephemeral_visual_context()
        allow_private = (viewer_kind in ("owner", "private_stage")) and not shield_active
        vis_ctx = self.get_ephemeral_visual_context(now=ts) if allow_private else None

        hh_status = self.household_settings.status(now=ts)
        eva_profile = hh_status.get("eva_profile") or {}
        hp_status = self.proprioception.status(now=ts)

        weekday_nl = _DUTCH_WEEKDAYS[ts.weekday()]
        is_lola_cook_day = weekday_nl in ("maandag", "dinsdag")

        redacted_count = 0
        if allow_private:
            household_today: dict[str, Any] | None = {
                "weekday_nl": weekday_nl,
                "lola_cooking_today": is_lola_cook_day,
                "headline_nl": (
                    f"Vandaag ({weekday_nl}): Lola kookt vanavond!"
                    if is_lola_cook_day
                    else f"Vandaag ({weekday_nl}): gewone gezinsdag voor Jaap & Lola."
                ),
                "notes": list(raw.get("household_notes") or []),
            }
            visitors_card = list(bio_snap.get("visitors_review") or [])
        else:
            household_today = None
            visitors_card = []
            redacted_count += 2

        visible_spotlights: list[dict[str, Any]] = []
        for sp in raw.get("spotlights") or []:
            if not isinstance(sp, dict):
                continue
            if sp.get("privacy_level") == "private_household" and not allow_private:
                redacted_count += 1
                continue
            visible_spotlights.append(sp)

        visible_conjured: list[str] = []
        for cj in raw.get("conjured_cards") or []:
            if not isinstance(cj, dict):
                continue
            kind = str(cj.get("card_kind") or "")
            if kind not in VALID_STAGE_CARD_KINDS:
                continue
            if kind in PRIVATE_STAGE_CARD_KINDS and not allow_private:
                continue
            if kind == "custom_spotlight" and not visible_spotlights:
                continue
            visible_conjured.append(kind)
        if visible_spotlights and "custom_spotlight" not in visible_conjured:
            visible_conjured.insert(0, "custom_spotlight")

        raw_utt = raw.get("live_utterance")
        visible_utterance: dict[str, Any] | None = None
        if isinstance(raw_utt, dict):
            if raw_utt.get("privacy_level") == "private_household" and not allow_private:
                visible_utterance = None
            else:
                visible_utterance = {
                    "utterance_id": str(raw_utt.get("utterance_id") or ""),
                    "speaker_text": str(raw_utt.get("speaker_text") or ""),
                    "eva_text": str(raw_utt.get("eva_text") or ""),
                    "created_at": str(raw_utt.get("created_at") or ""),
                }

        # Summarize rooms & Cast devices without exposing sensitive LAN metadata to ambient viewers
        rooms_out = []
        raw_rooms = hp_status.get("rooms") or hp_status.get("spatial_presence", {}).get("rooms") or []
        for rm in raw_rooms[:8]:
            if isinstance(rm, dict):
                rooms_out.append(
                    {
                        "room_id": rm.get("room_id", "algemeen"),
                        "occupancy_probability": rm.get("occupancy_probability", 0.0),
                        "motion_energy": rm.get("motion_energy", 0.0),
                    }
                )

        cast_targets = []
        if viewer_kind == "owner":
            for dev in hp_status.get("devices") or []:
                if not isinstance(dev, dict):
                    continue
                dev_port = dev.get("approved_port") or dev.get("port")
                ports = list(dev.get("open_ports") or [])
                if isinstance(dev_port, int):
                    ports.append(dev_port)
                proto_blob = " ".join(
                    [
                        str(dev.get("kind") or ""),
                        str(dev.get("protocol_family") or ""),
                        " ".join(str(x) for x in (dev.get("protocols") or [])),
                    ]
                ).lower()
                if 8009 in ports or "cast" in proto_blob:
                    cast_targets.append(
                        {
                            "device_id": dev.get("device_id"),
                            "label": dev.get("display_name") or dev.get("label") or dev.get("device_id"),
                            "room_id": dev.get("room_id"),
                            "consent_status": dev.get("consent_status"),
                        }
                    )

        grants_summary = []
        if viewer_kind == "owner":
            for gr in (raw.get("device_grants") or [])[:MAX_DEVICE_GRANTS]:
                if isinstance(gr, dict):
                    grants_summary.append(
                        StageDeviceGrantSummary(
                            grant_id=str(gr["grant_id"]),
                            label=str(gr["label"]),
                            form_factor=gr["form_factor"],
                            room_id=str(gr["room_id"]),
                            allow_private_cards_when_alone=bool(gr.get("allow_private_cards_when_alone", False)),
                            allow_owner_voice=bool(gr.get("allow_owner_voice", False)),
                            status=gr["status"],
                            created_at=str(gr["created_at"]),
                            expires_at=str(gr["expires_at"]),
                        ).model_dump(mode="json")
                    )

        return {
            "contract": "EvaHoloStageState/v1",
            "observed_at": _iso(ts),
            "viewer": {
                "kind": viewer_kind,
                "label": viewer_label,
                "form_factor": form_factor,
                "private_cards_visible": allow_private,
                "can_use_camera": (viewer_kind == "owner" or allow_owner_voice) and form_factor != "tv_cast",
                "redacted_cards_count": redacted_count,
            },
            "visual_copilot": (
                {
                    "active": True,
                    "observation_id": vis_ctx["observation_id"],
                    "source_label": vis_ctx["source_label"],
                    "expires_at": vis_ctx["expires_at"],
                    "remaining_seconds": vis_ctx["remaining_seconds"],
                }
                if vis_ctx is not None and (viewer_kind == "owner" or allow_owner_voice)
                else {"active": False, "observation_id": None, "source_label": None, "expires_at": None, "remaining_seconds": 0}
            ),
            "eva": {
                "display_name": str(eva_profile.get("display_name") or "Eva"),
                "persona_summary": str(eva_profile.get("persona_summary") or ""),
                "glb_asset_url": "/internal/v1/dashboard/holostage/assets/eva.glb",
            },
            "privacy_shield": {
                "active": shield_active,
                "reason": bio_snap["privacy_shield_reason"],
                "gaze_to_talk_active": bio_snap["gaze_to_talk_active"],
                "household_candidate": bio_snap["household_candidate"] if allow_private else None,
            },
            "live_utterance": visible_utterance,
            "stage_animation": (
                raw.get("stage_animation")
                if isinstance(raw.get("stage_animation"), dict)
                and (
                    raw["stage_animation"].get("privacy_level") != "private_household"
                    or allow_private
                )
                else None
            ),
            "cards": {
                "active_focus": str(visible_conjured[0] if visible_conjured else "none"),
                "conjured_cards": visible_conjured,
                "shopping_list": list(raw.get("shopping_list") or []),
                "household_today": household_today,
                "home_overview": {
                    "rooms": rooms_out,
                    "cast_targets": cast_targets,
                },
                "visitors_review": visitors_card,
                "spotlights": visible_spotlights,
            },
            "biometrics_admin": bio_snap["speaker_biometrics"] if viewer_kind == "owner" else None,
            "device_grants": grants_summary if viewer_kind == "owner" else [],
            "lan_stage_origin": self.get_lan_stage_origin(now=ts) if viewer_kind == "owner" else None,
            "public_stage_origin": self.get_public_stage_origin(now=ts) if viewer_kind == "owner" else None,
        }


__all__ = [
    "HoloStageManager",
    "PRIVATE_STAGE_CARD_KINDS",
    "STAGE_TOKEN_PATTERN",
    "SentenceBoundaryChunker",
    "VALID_STAGE_CARD_KINDS",
]
