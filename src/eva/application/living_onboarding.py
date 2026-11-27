"""Conversational Dutch onboarding & living preference/boundary parser for Eva's immersive stage.

Allows household members to shape Eva's name, family roster, character tone, household rules,
and guest/visitor privacy mode naturally in spoken or typed Dutch — without settings forms.
"""

from __future__ import annotations

import re
from typing import Any, Literal


LivingIntentKind = Literal[
    "none",
    "guest_mode_on",
    "guest_mode_off",
    "tone_preference",
    "household_rule",
    "conjure_card",
]

_STOP_NAMES = frozenset(
    {
        "eva",
        "goed",
        "prima",
        "klaar",
        "thuis",
        "hier",
        "blij",
        "benieuwd",
        "even",
        "echt",
        "ook",
        "nu",
        "wel",
        "niet",
        "gekoppeld",
        "wakker",
        "alleen",
    }
)

_OWNER_INTRO_RE = re.compile(
    r"\b(?:[Ii]k\s+ben|[Ii]k\s+heet|[Mm]ijn\s+naam\s+is|[Nn]oem\s+mij(?:\s+maar)?)\s+([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]{1,24})\b"
)
_EVA_RENAME_RE = re.compile(
    r"\b(?:noem\s+jezelf|jij\s+heet(?:\s+voortaan)?|ik\s+noem\s+je|jouw\s+naam\s+wordt|heet\s+voortaan)\s+([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]{1,24})\b",
    re.IGNORECASE,
)
_FAMILY_WITH_RE = re.compile(
    r"\b(?:woon(?:\s+hier)?\s+(?:samen\s+)?met|samen\s+met|mijn\s+(?:dochter|zoon|partner|vrouw|man|vriendin|vriend|kind|kinderen))\s+([A-ZÀ-ÖØ-Ýa-zà-öø-ÿ ,&-]{2,80})",
    re.IGNORECASE,
)
_NAME_TOKEN_RE = re.compile(r"\b([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]{1,24})\b")

_GUEST_ON_RE = re.compile(
    r"\b(?:we\s+krijgen\s+(?:zo\s+)?visite|er\s+(?:is|zijn|komen|komt)\s+(?:zo\s+)?(?:visite|gasten|vrienden|bezoek)|"
    r"vrienden\s+(?:over\s+de\s+vloer|op\s+bezoek)|gast(?:vrouw)?modus(?:\s+aan)?|borrelmodus(?:\s+aan)?|"
    r"zet\s+(?:de\s+)?gastmodus\s+aan|er\s+zijn\s+mensen\s+over\s+de\s+vloer)\b",
    re.IGNORECASE,
)
_GUEST_OFF_RE = re.compile(
    r"\b(?:(?:de\s+)?(?:visite|gasten|vrienden|het\s+bezoek)\s+(?:is|zijn)\s+(?:weer\s+)?weg|"
    r"we\s+zijn\s+weer\s+(?:alleen|onder\s+ons)|(?:gast(?:vrouw)?modus|borrelmodus)\s+uit|"
    r"zet\s+(?:de\s+)?gastmodus\s+uit)\b",
    re.IGNORECASE,
)

_CONJURE_RULES_RE = re.compile(
    r"\b(?:laat|toon|welke)\b[\w\s,'-]{0,36}\b(?:huisafspraken|huisregels|onze\s+afspraken|voorkeuren)\b",
    re.IGNORECASE,
)
_CONJURE_DEVICES_RE = re.compile(
    r"\b(?:wat\s+heb\s+je|welke\s+apparaten|laat|toon)\b[\w\s,'-]{0,40}\b(?:netwerk|apparaten\s+in\s+huis|gevonden)\b",
    re.IGNORECASE,
)
_CONJURE_FAMILY_RE = re.compile(
    r"\b(?:qr(?:-code)?\s+voor|koppel\s+(?:mijn\s+)?telefoon|welkomstkaart(?:je)?\s+voor|uitnodiging\s+voor)\b",
    re.IGNORECASE,
)

_TONE_CUES: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (
        re.compile(r"\b(?:droge(?:re)?\s+humor|lekker\s+droog|nuchter(?:der)?|lekker\s+direct)\b", re.IGNORECASE),
        "Gebruik droge, gevatte Nederlandse humor en kom direct tot de kern.",
        "nuchter_direct",
    ),
    (
        re.compile(r"\b(?:kort(?:er)?\s+van\s+stof|lekker\s+kort(?:af)?|bondig(?:er)?|geen\s+lange\s+verhalen)\b", re.IGNORECASE),
        "Houd antwoorden lekker kort, bondig en to-the-point zonder omhaal.",
        None,
    ),
    (
        re.compile(r"\b(?:iets\s+warmer|gezellig(?:er)?|warm\s+en\s+attent|zorgzamer)\b", re.IGNORECASE),
        "Spreek warm, huiselijk en attent, met oprechte aandacht voor het gezin.",
        "warm_huisgenoot",
    ),
    (
        re.compile(r"\b(?:speels(?:er)?|brutaler|eigenwijzer|creatiever|verras\s+ons)\b", re.IGNORECASE),
        "Wees speels, nieuwsgierig en af en toe licht eigenwijs met creatieve invalshoeken.",
        "creatief_nieuwsgierig",
    ),
    (
        re.compile(r"\b(?:rustig\s+op\s+de\s+achtergrond|minder\s+vaak\s+storen|alleen\s+als\s+ik\s+iets\s+vraag)\b", re.IGNORECASE),
        "Blijf rustig op de achtergrond en onderbreek alleen wanneer het echt nodig is.",
        "stille_regelaar",
    ),
)

_RULE_TRIGGERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:eva[,:\s]+)?(?:nieuwe\s+)?(?:huisafspraak|huisregel|afspraak)\s*[:\-]?\s*(.+)$", re.IGNORECASE),
    re.compile(
        r"^(?:eva[,:\s]+)?((?:na|vóór|voor)\s+(?:\d{1,2}(?::\d{2})?(?:\s*uur)?|negenen|tienen|achten|zevenen)\b.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:eva[,:\s]+)?((?:doordeweeks|in\s+het\s+weekend|'s\s+ochtends|'s\s+avonds|op\s+(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag))\b.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:eva[,:\s]+)?(?:onthoud(?:\s+als\s+afspraak|\s+goed)?\s+dat|ik\s+wil\s+graag\s+dat\s+je|graag\s+voortaan|wil\s+je\s+voortaan)\s+(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:eva[,:\s]+)?([^\n.!?]*\b(?:voortaan|niet\s+meer\s+storen|alleen\s+storen\s+als|direct\s+met\s+[A-ZÀ-ÖØ-Ýa-zà-öø-ÿ]+\s+(?:afstemmen|overleggen|regelen))\b[^\n]*)$",
        re.IGNORECASE,
    ),
)


def _normalize_sentence(raw: str, max_len: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", raw).strip(" \t\r\n.,;:-")
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def parse_awakening_utterance(
    text: str,
    *,
    current_owner: str = "Jaap",
    current_eva_name: str = "Eva",
) -> dict[str, Any]:
    """Extract owner name, household members, Eva's name, and style wishes from natural speech."""
    sample = (text or "").strip()
    result: dict[str, Any] = {
        "owner_name": None,
        "family_members": [],
        "eva_name": None,
        "preset_hint": None,
        "tone_guideline": None,
    }
    if not sample:
        return result

    m_eva = _EVA_RENAME_RE.search(sample)
    if m_eva:
        cand_eva = m_eva.group(1).strip().capitalize()
        if cand_eva.lower() not in {"maar", "gewoon", "lekker", "even"}:
            result["eva_name"] = cand_eva

    m_owner = _OWNER_INTRO_RE.search(sample)
    if m_owner:
        cand_owner = m_owner.group(1).strip()
        if cand_owner.lower() not in _STOP_NAMES and cand_owner.lower() != (result["eva_name"] or current_eva_name).lower():
            result["owner_name"] = cand_owner

    eff_owner = (result["owner_name"] or current_owner).lower()
    eff_eva = (result["eva_name"] or current_eva_name).lower()

    fam_found: list[str] = []
    for m_fam in _FAMILY_WITH_RE.finditer(sample):
        tail = m_fam.group(1)
        # Cut off at next clause boundary
        tail = re.split(r"(?:[.!?;]|\ben\s+ik\b|\bhaar\b|\bzijn\b|\bdie\b)", tail, maxsplit=1)[0]
        for tok in _NAME_TOKEN_RE.findall(tail):
            low = tok.lower()
            if (
                low not in _STOP_NAMES
                and low not in {"mijn", "onze", "dochter", "zoon", "partner", "vrouw", "man", "kind", "samen"}
                and low != eff_owner
                and low != eff_eva
                and tok not in fam_found
            ):
                fam_found.append(tok)
    result["family_members"] = fam_found[:8]

    for pattern, tone_line, preset_id in _TONE_CUES:
        if pattern.search(sample):
            result["tone_guideline"] = tone_line
            if preset_id:
                result["preset_hint"] = preset_id
            break

    return result


def detect_living_intent(text: str) -> dict[str, Any]:
    """Detect immediate actionable living intents (guest mode, rules, tone shifts, or card conjuring)."""
    sample = re.sub(r"\s+", " ", (text or "").strip())
    if not sample:
        return {"kind": "none"}

    if _GUEST_OFF_RE.search(sample):
        return {
            "kind": "guest_mode_off",
            "title": "Privé Gezinsmodus hersteld",
            "summary": "Visite-modus is uitgeschakeld; privékaarten van het gezin zijn weer zichtbaar.",
        }

    if _GUEST_ON_RE.search(sample):
        return {
            "kind": "guest_mode_on",
            "title": "Gastvrije Modus & Privéschild actief",
            "summary": "Privégegevens van het gezin zijn direct afgeschermd; Eva doet gezellig mee als gastvrouw.",
        }

    if _CONJURE_RULES_RE.search(sample):
        return {"kind": "conjure_card", "card": "rules"}
    if _CONJURE_DEVICES_RE.search(sample):
        return {"kind": "conjure_card", "card": "devices"}
    if _CONJURE_FAMILY_RE.search(sample):
        return {"kind": "conjure_card", "card": "family"}

    # Avoid treating pure questions ("Kun je ...?") as permanent household rules unless "voortaan" is present
    is_question = sample.endswith("?") and "voortaan" not in sample.lower()
    if not is_question:
        for pat in _RULE_TRIGGERS:
            m = pat.match(sample)
            if m:
                rule_txt = _normalize_sentence(m.group(1))
                if len(rule_txt) >= 8:
                    return {
                        "kind": "household_rule",
                        "rule_text": rule_txt,
                        "title": "Nieuwe huisafspraak vastgelegd",
                        "summary": rule_txt,
                    }

        for pattern, tone_line, preset_id in _TONE_CUES:
            if pattern.search(sample):
                return {
                    "kind": "tone_preference",
                    "tone_guideline": tone_line,
                    "preset_hint": preset_id,
                    "title": "Spreekstijl & karakter bijgesteld",
                    "summary": tone_line,
                }

    return {"kind": "none"}


__all__ = [
    "LivingIntentKind",
    "detect_living_intent",
    "parse_awakening_utterance",
]
