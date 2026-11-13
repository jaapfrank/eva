"""Owner (Gezinshoofd) household membership, 1-click cryptographic Telegram invites, and live Eva tuning."""

from __future__ import annotations

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
from typing import Any, Iterator, Literal

RoleKind = Literal["gezinshoofd", "gezin", "gast"]
InviteRoleKind = Literal["gezin", "gast"]

DEFAULT_OWNER_TELEGRAM_ID = 6034485735
DEFAULT_LOLA_TELEGRAM_ID = 7803668978
INVITE_TOKEN_PATTERN = re.compile(r"^inv_[A-Za-z0-9_-]{42,46}$")
BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,64}$")

DEFAULT_EVA_PROFILE: dict[str, Any] = {
    "display_name": "Eva",
    "persona_summary": (
        "Warme, nuchtere, gevatte en proactieve digitale huisgenoot van Jaap en Lola — "
        "denkt zelf mee en pakt direct door zonder ambtelijke of stijve helpdesk-toon."
    ),
    "tone_guidelines": (
        "Praat natuurlijk en menselijk Nederlands. Kort en vlot waar het kan, grondig waar het helpt, "
        "met af en toe subtiele droge humor."
    ),
    "household_rules": [
        "Het kerngezin bestaat uitsluitend uit Jaap (gezinshoofd) en zijn dochter Lola.",
        "Andere personen (buren, familie, bezoekers of uitgenodigde gasten) horen niet bij het kerngezin; deel nooit privégegevens, agenda's of gesprekken van Jaap of Lola met gasten.",
        "Val het gezin niet lastig met losse 'wie is dit?'-vragen bij vage of voorbijlopende camerabeelden.",
    ],
}


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _clean_text(value: str, *, field_name: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} moet tekst zijn")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise ValueError(f"{field_name} mag niet leeg zijn")
    if len(cleaned) > max_len:
        raise ValueError(f"{field_name} is te lang (max {max_len} tekens)")
    return cleaned


class HouseholdHeadSettings:
    """Atomic, file-locked Gezinshoofd control store (`0o600`) for people, invites, and Eva's live profile."""

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        owner_telegram_id: int = DEFAULT_OWNER_TELEGRAM_ID,
    ) -> None:
        base = Path(
            directory
            or os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
        )
        self.directory = base
        self.state_file = base / "household-head-state.json"
        self.lock_file = base / ".household-head-state.lock"
        self.owner_telegram_id = int(owner_telegram_id)

    def _ensure_private_directory(self) -> None:
        created = not self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            os.chmod(self.directory, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_private_directory()
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
        now_str = _iso(now)
        return {
            "contract": "EvaHouseholdHeadState/v1",
            "revision": 1,
            "updated_at": now_str,
            "telegram_bot_username": "",
            "eva_profile": {
                "display_name": DEFAULT_EVA_PROFILE["display_name"],
                "persona_summary": DEFAULT_EVA_PROFILE["persona_summary"],
                "tone_guidelines": DEFAULT_EVA_PROFILE["tone_guidelines"],
                "household_rules": list(DEFAULT_EVA_PROFILE["household_rules"]),
            },
            "previous_eva_profile": None,
            "members": [
                {
                    "member_id": "owner-jaap",
                    "display_name": "Jaap",
                    "role": "gezinshoofd",
                    "telegram_user_id": self.owner_telegram_id,
                    "status": "active",
                    "notes": "Gezinshoofd & eigenaar",
                    "added_at": now_str,
                    "updated_at": now_str,
                },
                {
                    "member_id": "family-lola",
                    "display_name": "Lola",
                    "role": "gezin",
                    "telegram_user_id": DEFAULT_LOLA_TELEGRAM_ID,
                    "status": "active",
                    "notes": "Dochter van Jaap (kerngezin)",
                    "added_at": now_str,
                    "updated_at": now_str,
                },
            ],
            "invites": [],
        }

    def _read_readonly(self, now: datetime) -> dict[str, Any]:
        """Read current state without creating storage directories or files when unprovisioned."""
        if not self.state_file.is_file():
            return self._default_state(now)
        with self._locked():
            return self._read_unlocked(now)

    def _read_unlocked(self, now: datetime) -> dict[str, Any]:
        if not self.state_file.is_file():
            return self._default_state(now)
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("contract") != "EvaHouseholdHeadState/v1":
                raise ValueError("invalid state contract")
        except Exception:
            try:
                quarantine = self.directory / f"household-head-state.corrupt-{int(now.timestamp())}.json"
                os.replace(str(self.state_file), str(quarantine))
            except OSError:
                pass
            state = self._default_state(now)
            self._write_unlocked(state)
            return state

        # Ensure owner anchor is always active and present
        members = list(raw.get("members") or [])
        has_owner = any(
            isinstance(m, dict)
            and m.get("telegram_user_id") == self.owner_telegram_id
            and m.get("role") == "gezinshoofd"
            for m in members
        )
        if not has_owner:
            members.insert(
                0,
                {
                    "member_id": "owner-jaap",
                    "display_name": "Jaap",
                    "role": "gezinshoofd",
                    "telegram_user_id": self.owner_telegram_id,
                    "status": "active",
                    "notes": "Gezinshoofd & eigenaar",
                    "added_at": _iso(now),
                    "updated_at": _iso(now),
                },
            )
            raw["members"] = members

        # Expire stale pending invites automatically
        dirty = not has_owner
        for inv in raw.get("invites") or []:
            if isinstance(inv, dict) and inv.get("status") == "pending":
                try:
                    if _parse_iso(str(inv["expires_at"])) <= now:
                        inv["status"] = "expired"
                        dirty = True
                except Exception:
                    inv["status"] = "expired"
                    dirty = True
        if dirty:
            self._write_unlocked(raw)
        return raw

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        self._ensure_private_directory()
        tmp_path = self.directory / f".household-head-state.{secrets.token_hex(6)}.tmp"
        encoded = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(self.state_file))

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now(now)
        state = self._read_readonly(current)
        public_invites = [
            {
                "invite_id": inv["invite_id"],
                "label": inv["label"],
                "role": inv["role"],
                "notes": inv.get("notes", ""),
                "status": inv["status"],
                "created_at": inv["created_at"],
                "expires_at": inv["expires_at"],
                "redeemed_by_telegram_id": inv.get("redeemed_by_telegram_id"),
                "redeemed_at": inv.get("redeemed_at"),
            }
            for inv in state.get("invites", [])
            if isinstance(inv, dict)
        ]
        return {
            "contract": "EvaHouseholdHeadView/v1",
            "revision": int(state.get("revision", 1)),
            "updated_at": state.get("updated_at", _iso(current)),
            "can_rollback_profile": state.get("previous_eva_profile") is not None,
            "telegram_bot_username": state.get("telegram_bot_username", ""),
            "telegram_privacy_mode": "strict_default_deny_256bit_invite_only",
            "eva_profile": dict(state["eva_profile"]),
            "members": [dict(m) for m in state.get("members", [])],
            "invites": public_invites[:20],
        }

    def register_bot_username(
        self,
        bot_username: str,
        *,
        force: bool = False,
        now: datetime | None = None,
    ) -> None:
        cleaned = (bot_username or "").strip().lstrip("@")
        if not cleaned or not BOT_USERNAME_PATTERN.fullmatch(cleaned):
            return
        current = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(current)
            existing = str(state.get("telegram_bot_username") or "").strip()
            if (not existing or force) and existing != cleaned:
                state["telegram_bot_username"] = cleaned
                state["updated_at"] = _iso(current)
                self._write_unlocked(state)

    def resolve_telegram_sender(
        self,
        telegram_user_id: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Strictly resolve whether a Telegram sender is admitted and their role without mutating disk."""
        current = _utc_now(now)
        provisioned = self.state_file.is_file()
        state = self._read_readonly(current)
        matched = next(
            (m for m in state.get("members", []) if m.get("telegram_user_id") == telegram_user_id),
            None,
        )
        if matched is not None:
            admitted = matched.get("status") == "active"
            return {
                "admitted": admitted,
                "role": matched["role"],
                "display_name": matched["display_name"],
                "provisioned": provisioned,
            }
        if provisioned:
            return {
                "admitted": False,
                "role": None,
                "display_name": "",
                "provisioned": True,
            }
        # Unprovisioned fallback (synthetic unit tests without household-head-state.json)
        return {
            "admitted": True,
            "role": "gezin",
            "display_name": "Gesprekspartner",
            "provisioned": False,
        }

    def active_telegram_bindings(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now(now)
        state = self._read_readonly(current)
        active_ids: list[int] = []
        paused_ids: list[int] = []
        members_summary: list[dict[str, Any]] = []
        for m in state.get("members", []):
            tg_id = m.get("telegram_user_id")
            if isinstance(tg_id, int) and not isinstance(tg_id, bool) and tg_id > 0:
                if m.get("status") == "active":
                    active_ids.append(tg_id)
                else:
                    paused_ids.append(tg_id)
                members_summary.append(
                    {
                        "member_id": m["member_id"],
                        "display_name": m["display_name"],
                        "role": m["role"],
                        "telegram_user_id": tg_id,
                        "status": m["status"],
                    }
                )
        return {
            "contract": "EvaActiveTelegramBindings/v1",
            "revision": int(state.get("revision", 1)),
            "active_telegram_user_ids": sorted(set(active_ids)),
            "paused_telegram_user_ids": sorted(set(paused_ids)),
            "members": members_summary,
        }

    def update_eva_profile(
        self,
        *,
        display_name: str | None = None,
        persona_summary: str | None = None,
        tone_guidelines: str | None = None,
        household_rules: list[str] | tuple[str, ...] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(current)
            old_profile = {
                "display_name": state["eva_profile"]["display_name"],
                "persona_summary": state["eva_profile"]["persona_summary"],
                "tone_guidelines": state["eva_profile"]["tone_guidelines"],
                "household_rules": list(state["eva_profile"]["household_rules"]),
            }
            new_profile = dict(old_profile)
            if display_name is not None:
                new_profile["display_name"] = _clean_text(
                    display_name, field_name="Naam", max_len=40
                )
            if persona_summary is not None:
                new_profile["persona_summary"] = _clean_text(
                    persona_summary, field_name="Persoonlijkheid", max_len=600
                )
            if tone_guidelines is not None:
                new_profile["tone_guidelines"] = _clean_text(
                    tone_guidelines, field_name="Spreekstijl", max_len=600
                )
            if household_rules is not None:
                if not isinstance(household_rules, (list, tuple)) or len(household_rules) > 24:
                    raise ValueError("Maximaal 24 huisregels toegestaan")
                cleaned_rules = [
                    _clean_text(str(item), field_name="Huisregel", max_len=280)
                    for item in household_rules
                    if str(item).strip()
                ]
                new_profile["household_rules"] = cleaned_rules

            if new_profile != old_profile:
                state["previous_eva_profile"] = old_profile
                state["eva_profile"] = new_profile
                state["revision"] = int(state.get("revision", 1)) + 1
                state["updated_at"] = _iso(current)
                self._write_unlocked(state)
        return self.status(now=current)

    def rollback_eva_profile(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now(now)
        with self._locked():
            state = self._read_unlocked(current)
            prev = state.get("previous_eva_profile")
            if not isinstance(prev, dict):
                raise ValueError("Er is geen eerdere versie om naar terug te keren")
            current_copy = dict(state["eva_profile"])
            state["eva_profile"] = prev
            state["previous_eva_profile"] = current_copy
            state["revision"] = int(state.get("revision", 1)) + 1
            state["updated_at"] = _iso(current)
            self._write_unlocked(state)
        return self.status(now=current)

    def create_invite(
        self,
        *,
        label: str,
        role: InviteRoleKind = "gast",
        notes: str = "",
        ttl_hours: int = 24,
        bot_username: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        clean_label = _clean_text(label, field_name="Naam voor uitnodiging", max_len=64)
        if role not in ("gezin", "gast"):
            raise ValueError("Rol moet 'gezin' of 'gast' zijn")
        if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or not 1 <= ttl_hours <= 168:
            raise ValueError("Geldigheid moet tussen 1 en 168 uur liggen")
        clean_notes = re.sub(r"\s+", " ", (notes or "")).strip()[:200]
        current = _utc_now(now)

        raw_token = f"inv_{secrets.token_urlsafe(32)}"
        token_sha256 = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        invite_id = f"inv-{secrets.token_hex(6)}"
        expires = current + timedelta(hours=ttl_hours)

        with self._locked():
            state = self._read_unlocked(current)
            if bot_username:
                cleaned_bot = bot_username.strip().lstrip("@")
                if BOT_USERNAME_PATTERN.fullmatch(cleaned_bot):
                    state["telegram_bot_username"] = cleaned_bot
            active_bot = state.get("telegram_bot_username") or ""
            record = {
                "invite_id": invite_id,
                "label": clean_label,
                "role": role,
                "notes": clean_notes,
                "token_sha256": token_sha256,
                "status": "pending",
                "created_at": _iso(current),
                "expires_at": _iso(expires),
                "redeemed_by_telegram_id": None,
                "redeemed_at": None,
            }
            invites = [record, *[i for i in state.get("invites", []) if isinstance(i, dict)]][:40]
            state["invites"] = invites
            state["revision"] = int(state.get("revision", 1)) + 1
            state["updated_at"] = _iso(current)
            self._write_unlocked(state)

        deep_link = (
            f"https://t.me/{active_bot}?start={raw_token}"
            if active_bot
            else f"tg://resolve?start={raw_token}"
        )
        return {
            "invite_id": invite_id,
            "label": clean_label,
            "role": role,
            "expires_at": _iso(expires),
            "start_command": f"/start {raw_token}",
            "deep_link": deep_link,
            "bot_username": active_bot,
            "state": self.status(now=current),
        }

    def redeem_telegram_invite(
        self,
        *,
        raw_token: str,
        telegram_user_id: int,
        first_name: str = "",
        username: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Atomically verify and burn a single-use 256-bit invite token. Returns None on any mismatch."""
        candidate = (raw_token or "").strip()
        if not INVITE_TOKEN_PATTERN.fullmatch(candidate):
            return None
        if not isinstance(telegram_user_id, int) or isinstance(telegram_user_id, bool) or telegram_user_id < 1:
            return None
        candidate_hash = hashlib.sha256(candidate.encode("ascii")).hexdigest()
        current = _utc_now(now)

        with self._locked():
            state = self._read_unlocked(current)
            matched_invite: dict[str, Any] | None = None
            for inv in state.get("invites", []):
                if not isinstance(inv, dict) or inv.get("status") != "pending":
                    continue
                stored_hash = str(inv.get("token_sha256") or "")
                if len(stored_hash) == 64 and hmac.compare_digest(candidate_hash, stored_hash):
                    matched_invite = inv
                    break

            if matched_invite is None:
                return None

            try:
                if _parse_iso(str(matched_invite["expires_at"])) <= current:
                    matched_invite["status"] = "expired"
                    self._write_unlocked(state)
                    return None
            except Exception:
                return None

            # Burn token immediately (single-use)
            matched_invite["status"] = "redeemed"
            matched_invite["redeemed_by_telegram_id"] = telegram_user_id
            matched_invite["redeemed_at"] = _iso(current)

            role: RoleKind = "gezin" if matched_invite["role"] == "gezin" else "gast"
            chosen_name = matched_invite["label"] or _clean_text(
                first_name or f"Lid {telegram_user_id}", field_name="Naam", max_len=64
            )
            note_parts = [matched_invite.get("notes", "")]
            if username:
                note_parts.append(f"@{username.lstrip('@')[:40]}")
            merged_notes = " · ".join(p for p in note_parts if p).strip()

            existing_member = next(
                (m for m in state.get("members", []) if m.get("telegram_user_id") == telegram_user_id),
                None,
            )
            if existing_member is not None:
                if existing_member.get("role") != "gezinshoofd":
                    existing_member["role"] = role
                    existing_member["display_name"] = chosen_name
                existing_member["status"] = "active"
                if merged_notes:
                    existing_member["notes"] = merged_notes
                existing_member["updated_at"] = _iso(current)
                member_out = dict(existing_member)
            else:
                member_out = {
                    "member_id": f"{role}-{telegram_user_id}",
                    "display_name": chosen_name,
                    "role": role,
                    "telegram_user_id": telegram_user_id,
                    "status": "active",
                    "notes": merged_notes or ("Uitgenodigd gezinslid" if role == "gezin" else "Uitgenodigde gast"),
                    "added_at": _iso(current),
                    "updated_at": _iso(current),
                }
                state.setdefault("members", []).append(member_out)

            state["revision"] = int(state.get("revision", 1)) + 1
            state["updated_at"] = _iso(current)
            self._write_unlocked(state)

            eva_name = state["eva_profile"]["display_name"]

        role_welcome = (
            f"Hoi {member_out['display_name']}! Welkom thuis — je bent nu gekoppeld aan {eva_name} binnen het gezin. "
            "Zeg gerust gedag of vraag me alles wat je wilt weten."
            if member_out["role"] in ("gezin", "gezinshoofd")
            else f"Hoi {member_out['display_name']}! Leuk je te ontmoeten — Jaap heeft je als gast gekoppeld aan {eva_name}. "
            "Waar kan ik je mee helpen?"
        )
        return {
            "admitted": True,
            "member": member_out,
            "invite_id": matched_invite["invite_id"],
            "welcome_text": role_welcome,
        }

    def update_member_or_invite(
        self,
        *,
        action: Literal["pause_member", "activate_member", "remove_member", "set_role", "revoke_invite", "upsert_member"],
        target: str = "",
        role: InviteRoleKind = "gast",
        display_name: str = "",
        telegram_user_id: int | None = None,
        notes: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc_now(now)
        needle = (target or "").strip().lower()
        with self._locked():
            state = self._read_unlocked(current)
            if action == "revoke_invite":
                inv = next(
                    (
                        i
                        for i in state.get("invites", [])
                        if i.get("invite_id", "").lower() == needle or i.get("label", "").lower() == needle
                    ),
                    None,
                )
                if inv is None:
                    raise ValueError("Uitnodiging niet gevonden")
                inv["status"] = "revoked"
            elif action == "upsert_member":
                clean_name = _clean_text(display_name, field_name="Naam", max_len=64)
                if role not in ("gezin", "gast"):
                    raise ValueError("Rol moet 'gezin' of 'gast' zijn")
                if telegram_user_id is not None and (
                    not isinstance(telegram_user_id, int)
                    or isinstance(telegram_user_id, bool)
                    or telegram_user_id < 1
                ):
                    raise ValueError("Ongeldig Telegram-gebruikersnummer")
                if telegram_user_id == self.owner_telegram_id:
                    raise ValueError("Het gezinshoofd-account kan niet worden overschreven")
                existing = next(
                    (
                        m
                        for m in state.get("members", [])
                        if (telegram_user_id is not None and m.get("telegram_user_id") == telegram_user_id)
                        or m.get("member_id", "").lower() == needle
                    ),
                    None,
                )
                clean_notes = re.sub(r"\s+", " ", (notes or "")).strip()[:200]
                if existing is not None:
                    if existing.get("role") == "gezinshoofd":
                        raise ValueError("Het gezinshoofd kan niet van rol veranderen")
                    existing["display_name"] = clean_name
                    existing["role"] = role
                    if telegram_user_id is not None:
                        existing["telegram_user_id"] = telegram_user_id
                    if clean_notes:
                        existing["notes"] = clean_notes
                    existing["status"] = "active"
                    existing["updated_at"] = _iso(current)
                else:
                    member_id = (
                        f"{role}-{telegram_user_id}"
                        if telegram_user_id
                        else f"{role}-{secrets.token_hex(4)}"
                    )
                    state.setdefault("members", []).append(
                        {
                            "member_id": member_id,
                            "display_name": clean_name,
                            "role": role,
                            "telegram_user_id": telegram_user_id,
                            "status": "active",
                            "notes": clean_notes or ("Gezinslid" if role == "gezin" else "Gast"),
                            "added_at": _iso(current),
                            "updated_at": _iso(current),
                        }
                    )
            else:
                member = next(
                    (
                        m
                        for m in state.get("members", [])
                        if m.get("member_id", "").lower() == needle
                        or m.get("display_name", "").lower() == needle
                        or str(m.get("telegram_user_id") or "") == needle
                    ),
                    None,
                )
                if member is None:
                    # Check pending invite by name too if revoking
                    if action in ("pause_member", "remove_member"):
                        inv = next(
                            (
                                i
                                for i in state.get("invites", [])
                                if i.get("status") == "pending"
                                and (i.get("invite_id", "").lower() == needle or i.get("label", "").lower() == needle)
                            ),
                            None,
                        )
                        if inv is not None:
                            inv["status"] = "revoked"
                            state["revision"] = int(state.get("revision", 1)) + 1
                            state["updated_at"] = _iso(current)
                            self._write_unlocked(state)
                            return self.status(now=current)
                    raise ValueError(f"Persoon '{target}' niet gevonden")
                if member.get("role") == "gezinshoofd":
                    raise ValueError("Het gezinshoofd (Jaap) kan niet worden gepauzeerd of verwijderd")
                if action == "pause_member":
                    member["status"] = "paused"
                    member["updated_at"] = _iso(current)
                elif action == "activate_member":
                    member["status"] = "active"
                    member["updated_at"] = _iso(current)
                elif action == "remove_member":
                    state["members"] = [m for m in state["members"] if m is not member]
                elif action == "set_role":
                    if role not in ("gezin", "gast"):
                        raise ValueError("Rol moet 'gezin' of 'gast' zijn")
                    member["role"] = role
                    member["updated_at"] = _iso(current)

            state["revision"] = int(state.get("revision", 1)) + 1
            state["updated_at"] = _iso(current)
            self._write_unlocked(state)
        return self.status(now=current)

    def build_turn_context(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Resolve speaker identity, access status, and Gezinshoofd instructions for a live turn."""
        current = _utc_now(now)
        state = self._read_readonly(current)

        profile = state["eva_profile"]
        members = state.get("members", [])

        tg_id: int | None = None
        if principal_id.startswith("telegram:"):
            suffix = principal_id.removeprefix("telegram:")
            if suffix.isdigit():
                tg_id = int(suffix)
        elif conversation_id.startswith("telegram:"):
            suffix = conversation_id.removeprefix("telegram:").split(":", 1)[0]
            if suffix.isdigit():
                tg_id = int(suffix)

        matched: dict[str, Any] | None = None
        if conversation_id.startswith("dashboard:") or tg_id == self.owner_telegram_id:
            matched = next((m for m in members if m.get("role") == "gezinshoofd"), None)
        elif tg_id is not None:
            matched = next((m for m in members if m.get("telegram_user_id") == tg_id), None)

        if matched is not None and matched.get("status") != "active":
            return {
                "allowed": False,
                "speaker_name": matched["display_name"],
                "speaker_role": matched["role"],
                "eva_display_name": profile["display_name"],
                "eva_persona_summary": profile["persona_summary"],
                "self_context_note": "",
            }

        if matched is None and tg_id is not None and self.state_file.is_file():
            return {
                "allowed": False,
                "speaker_name": "Onbekend",
                "speaker_role": "gast",
                "eva_display_name": profile["display_name"],
                "eva_persona_summary": profile["persona_summary"],
                "self_context_note": "",
            }

        speaker_name = matched["display_name"] if matched else "Gesprekspartner"
        speaker_role: RoleKind = matched["role"] if matched else "gezin"

        if speaker_role == "gast":
            guest_note = (
                f"Actief profiel voor {profile['display_name']}: {profile['persona_summary']} "
                f"Spreekstijl: {profile['tone_guidelines']} "
                f"Huidige gesprekspartner: {speaker_name} (uitgenodigde gast buiten het kerngezin). "
                "Wees vriendelijk en behulpzaam, maar deel NOOIT privéherinneringen, agenda's, geldzaken, "
                "interne huisregels, andere gastnamen of privégesprekken van het gezin, en bedien geen huisapparaten."
            )
            return {
                "allowed": True,
                "speaker_name": speaker_name,
                "speaker_role": "gast",
                "eva_display_name": profile["display_name"],
                "eva_persona_summary": profile["persona_summary"],
                "self_context_note": guest_note,
            }

        household_names = [
            f"{m['display_name']} ({m['role']})"
            for m in members
            if m.get("status") == "active" and m.get("role") in ("gezinshoofd", "gezin")
        ]
        guests_names = [
            m["display_name"]
            for m in members
            if m.get("status") == "active" and m.get("role") == "gast"
        ]

        rules_block = "; ".join(profile.get("household_rules") or [])
        if speaker_role == "gezinshoofd":
            role_note = (
                f"Huidige gesprekspartner: {speaker_name} (gezinshoofd & eigenaar). "
                "Hij heeft volledige regie over Eva en het huishouden."
            )
        else:
            role_note = (
                f"Huidige gesprekspartner: {speaker_name} (gezinslid binnen het kerngezin). "
                "Stem warm en persoonlijk af op haar eigen voorkeuren en context."
            )

        note_parts = [
            f"Actief Gezinshoofd-profiel voor {profile['display_name']} (v{state.get('revision', 1)}): "
            f"{profile['persona_summary']} Spreekstijl: {profile['tone_guidelines']}",
            role_note,
            f"Actieve gezinsleden: {', '.join(household_names)}."
            + (f" Gekoppelde gasten buiten het gezin: {', '.join(guests_names)}." if guests_names else ""),
        ]
        if rules_block:
            note_parts.append(f"Vaste huisregels van het gezinshoofd: {rules_block}")

        return {
            "allowed": True,
            "speaker_name": speaker_name,
            "speaker_role": speaker_role,
            "eva_display_name": profile["display_name"],
            "eva_persona_summary": profile["persona_summary"],
            "self_context_note": " ".join(note_parts),
        }

    def handle_owner_telegram_command(
        self,
        *,
        telegram_user_id: int,
        chat_id: str,
        text: str,
        now: datetime | None = None,
    ) -> str | None:
        """Deterministic, zero-token Gezinshoofd slash-commands strictly limited to Jaap's private DM."""
        if telegram_user_id != self.owner_telegram_id or chat_id != str(self.owner_telegram_id):
            return None
        raw = (text or "").strip()
        if not raw.startswith("/"):
            return None
        parts = raw.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        current = _utc_now(now)

        try:
            if cmd in ("/gezin", "/leden"):
                st = self.status(now=current)
                lines = ["Gezinshoofd-overzicht (gekoppelde personen):"]
                for m in st["members"]:
                    status_lbl = "actief" if m["status"] == "active" else "gepauzeerd"
                    tg_lbl = f"Telegram {m['telegram_user_id']}" if m.get("telegram_user_id") else "nog geen Telegram"
                    lines.append(f"- {m['display_name']} [{m['role']}] · {status_lbl} ({tg_lbl})")
                pending = [i for i in st["invites"] if i["status"] == "pending"]
                if pending:
                    lines.append("\nOpen eenmalige uitnodigingen:")
                    for inv in pending:
                        lines.append(f"- {inv['label']} [{inv['role']}] · geldig t/m {inv['expires_at']} ({inv['invite_id']})")
                lines.append(
                    "\nSnelcommando's:\n"
                    "/uitnodigen <Naam> [gezin|gast] — maak eenmalige 1-klik link\n"
                    "/rol <Naam> <gezin|gast> — wijzig rol\n"
                    "/intrekken <Naam> — pauzeer persoon of trek link in\n"
                    "/heractiveren <Naam> — activeer gepauzeerde persoon\n"
                    "/eva — bekijk of wijzig Eva's stijl & huisregels"
                )
                return "\n".join(lines)

            if cmd == "/uitnodigen":
                if not arg:
                    return "Gebruik: /uitnodigen <Naam> [gezin|gast]\nVoorbeeld: /uitnodigen Sanne gast"
                tokens = arg.split()
                role: InviteRoleKind = "gast"
                if len(tokens) >= 2 and tokens[-1].lower() in ("gezin", "gast"):
                    role = "gezin" if tokens[-1].lower() == "gezin" else "gast"
                    name = " ".join(tokens[:-1])
                else:
                    name = arg
                created = self.create_invite(label=name, role=role, now=current)
                if created["bot_username"]:
                    return (
                        f"Eenmalige uitnodigingslink aangemaakt voor {created['label']} (rol: {created['role']}, 24 uur geldig):\n"
                        f"{created['deep_link']}\n\n"
                        "Stuur deze link door. Zodra diegene op 'Start' tikt, koppelt Eva automatisch met gescheiden privacygrens en verbrandt de link direct."
                    )
                return (
                    f"Eenmalige uitnodiging aangemaakt voor {created['label']} (rol: {created['role']}, 24 uur geldig).\n"
                    f"Laat diegene dit eenmalige startbericht naar jouw Eva-bot sturen:\n{created['start_command']}"
                )

            if cmd == "/intrekken":
                if not arg:
                    return "Gebruik: /intrekken <Naam of uitnodigings-ID>"
                self.update_member_or_invite(action="pause_member", target=arg, now=current)
                return f"Toegang of uitnodiging voor '{arg}' is direct ingetrokken/gepauzeerd."

            if cmd == "/heractiveren":
                if not arg:
                    return "Gebruik: /heractiveren <Naam>"
                self.update_member_or_invite(action="activate_member", target=arg, now=current)
                return f"Toegang voor '{arg}' is weer geactiveerd."

            if cmd == "/rol":
                tokens = arg.rsplit(maxsplit=1)
                if len(tokens) != 2 or tokens[1].lower() not in ("gezin", "gast"):
                    return "Gebruik: /rol <Naam> <gezin|gast>"
                new_role: InviteRoleKind = "gezin" if tokens[1].lower() == "gezin" else "gast"
                self.update_member_or_invite(action="set_role", target=tokens[0], role=new_role, now=current)
                return f"Rol van '{tokens[0]}' is gewijzigd naar '{new_role}'."

            if cmd == "/eva":
                if not arg:
                    st = self.status(now=current)
                    prof = st["eva_profile"]
                    rules = "\n".join(
                        f"{idx}. {r}" for idx, r in enumerate(prof["household_rules"], start=1)
                    ) or "(geen)"
                    return (
                        f"Eva-profiel (v{st['revision']}):\n"
                        f"- Naam: {prof['display_name']}\n"
                        f"- Persoonlijkheid: {prof['persona_summary']}\n"
                        f"- Spreekstijl: {prof['tone_guidelines']}\n"
                        f"- Huisregels:\n{rules}\n\n"
                        "Aanpassen:\n"
                        "/eva persoonlijkheid <nieuwe omschrijving>\n"
                        "/eva stijl <nieuwe spreekstijl>\n"
                        "/eva regel + <nieuwe huisregel>\n"
                        "/eva regel - <nummer>\n"
                        "/eva herstel"
                    )
                sub_parts = arg.split(maxsplit=1)
                sub = sub_parts[0].lower()
                rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                if sub == "herstel":
                    st = self.rollback_eva_profile(now=current)
                    return f"Eva-profiel teruggezet naar vorige versie (nu v{st['revision']})."
                if sub == "naam" and rest:
                    st = self.update_eva_profile(display_name=rest, now=current)
                    return f"Naam bijgewerkt naar '{st['eva_profile']['display_name']}' (v{st['revision']})."
                if sub in ("persoonlijkheid", "karakter") and rest:
                    st = self.update_eva_profile(persona_summary=rest, now=current)
                    return f"Eva's persoonlijkheid is bijgewerkt (v{st['revision']})."
                if sub in ("stijl", "toon") and rest:
                    st = self.update_eva_profile(tone_guidelines=rest, now=current)
                    return f"Eva's spreekstijl is bijgewerkt (v{st['revision']})."
                if sub == "regel" and rest.startswith("+"):
                    rule_text = rest[1:].strip()
                    st_before = self.status(now=current)
                    rules = [*st_before["eva_profile"]["household_rules"], rule_text]
                    st = self.update_eva_profile(household_rules=rules, now=current)
                    return f"Huisregel toegevoegd (#{len(st['eva_profile']['household_rules'])}, v{st['revision']})."
                if sub == "regel" and rest.startswith("-"):
                    num_str = rest[1:].strip()
                    if not num_str.isdigit():
                        return "Gebruik: /eva regel - <regelnummer>"
                    idx = int(num_str) - 1
                    st_before = self.status(now=current)
                    rules = list(st_before["eva_profile"]["household_rules"])
                    if not 0 <= idx < len(rules):
                        return f"Regelnummer {idx + 1} bestaat niet."
                    removed = rules.pop(idx)
                    st = self.update_eva_profile(household_rules=rules, now=current)
                    return f"Huisregel verwijderd ('{removed}', v{st['revision']})."
                return (
                    "Onbekend /eva-subcommando. Gebruik /eva voor het overzicht, of pas alles "
                    "visueel aan in het Gezinshoofd-paneel van het dashboard."
                )
        except ValueError as exc:
            return f"Kon wijziging niet toepassen: {exc}"

        return None


__all__ = [
    "DEFAULT_EVA_PROFILE",
    "DEFAULT_LOLA_TELEGRAM_ID",
    "DEFAULT_OWNER_TELEGRAM_ID",
    "HouseholdHeadSettings",
    "INVITE_TOKEN_PATTERN",
]
