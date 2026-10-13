"""Translate one deliberately small human ``SOUL.md`` into a staged identity draft.

The format is intentionally not a prompt file and cannot carry permissions,
tools, memories, policies, or a signature.  It is only a convenient way to
propose how Eva presents herself.  Callers still need to preview, sign, and
activate a resulting draft through their separate authority boundary.
"""

from __future__ import annotations

import hashlib

from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack, IdentityPackDraft
from eva.application.identity_drafts import identity_presentation_diff


MAX_SOUL_BYTES = 8 * 1024
MAX_SOUL_LINES = 80
_HEADINGS = {
    "naam": "display_name",
    "voornaamwoorden": "pronouns",
    "taal": "language",
    "humor": "humour",
    "temperament": "temperament",
    "stem": "voice_style",
}


class SoulMarkdownError(ValueError):
    """The supplied ``SOUL.md`` cannot become a safe staged identity draft."""


def soul_markdown_sha256(markdown: str) -> str:
    """Return a stable source reference without retaining the source text."""

    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def identity_to_soul_markdown(identity: IdentityPack) -> str:
    """Export only the small presentation subset accepted by this importer."""

    sections = (
        ("Naam", identity.display_name),
        ("Voornaamwoorden", ", ".join(identity.pronouns)),
        ("Taal", identity.language),
        ("Humor", identity.humour),
        ("Temperament", ", ".join(identity.temperament)),
        ("Stem", ", ".join(identity.voice_style.qualities)),
    )
    lines = [f"# {identity.display_name}"]
    for heading, value in sections:
        lines.extend(("", f"## {heading}", value))
    markdown = "\n".join((*lines, ""))
    if (
        len(markdown.encode("utf-8")) > MAX_SOUL_BYTES
        or len(markdown.splitlines()) > MAX_SOUL_LINES
    ):
        raise SoulMarkdownError("IdentityPack past niet in een kleine SOUL.md-export")
    return markdown


def _value_list(value: str, *, heading: str) -> list[str]:
    result = [item.strip() for item in value.split(",")]
    if not result or any(not item for item in result):
        raise SoulMarkdownError(f"SOUL.md {heading} bevat een lege waarde")
    return result


def _presentation(markdown: str, *, base: IdentityPack) -> dict[str, object | None]:
    if not isinstance(markdown, str):
        raise SoulMarkdownError("SOUL.md is geen tekst")
    if "\x00" in markdown or len(markdown.encode("utf-8")) > MAX_SOUL_BYTES:
        raise SoulMarkdownError("SOUL.md is te groot of ongeldig")
    lines = markdown.splitlines()
    if len(lines) > MAX_SOUL_LINES:
        raise SoulMarkdownError("SOUL.md heeft te veel regels")

    changes: dict[str, object] = {}
    current: str | None = None
    saw_title = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            if saw_title or changes or current is not None:
                raise SoulMarkdownError("SOUL.md titel staat niet aan het begin")
            saw_title = True
            continue
        if line.startswith("## "):
            heading = line[3:].strip().casefold()
            field = _HEADINGS.get(heading)
            if field is None:
                raise SoulMarkdownError("SOUL.md bevat een niet-toegestaan onderdeel")
            if field in changes:
                raise SoulMarkdownError("SOUL.md herhaalt een onderdeel")
            current = field
            continue
        if current is None or current in changes or line.startswith("#"):
            raise SoulMarkdownError("SOUL.md heeft geen geldige waarde per onderdeel")
        if current == "pronouns":
            changes[current] = _value_list(line, heading="voornaamwoorden")
        elif current in {"temperament", "voice_style"}:
            heading = "temperament" if current == "temperament" else "stem"
            changes[current] = _value_list(line, heading=heading)
        else:
            changes[current] = line

    if not changes:
        raise SoulMarkdownError("SOUL.md bevat geen toegestane identiteitwijziging")
    voice_style = None
    if "voice_style" in changes:
        qualities = changes["voice_style"]
        assert isinstance(qualities, list)
        voice_style = {
            **base.voice_style.model_dump(mode="json"),
            "name": "warm_jonge_vlotte_huisgenoot",
            "qualities": qualities,
            "forbidden_wording": [
                *base.voice_style.forbidden_wording,
                *([] if "scherp" in base.voice_style.forbidden_wording else ["scherp"]),
            ],
        }
    return {
        "display_name": changes.get("display_name"),
        "pronouns": changes.get("pronouns"),
        "language": changes.get("language"),
        "voice_style": voice_style,
        "temperament": changes.get("temperament"),
        "humour": changes.get("humour"),
        "channel_presentations": None,
    }


def soul_markdown_to_draft(
    markdown: str, *, draft_id: str, base: IdentityPack
) -> IdentityPackDraft:
    """Create a bound staged preview; this never writes or activates a pack."""

    try:
        draft = IdentityPackDraft.model_validate(
            {
                "contract": "IdentityPackDraft/v1",
                "schema_version": 1,
                "draft_id": draft_id,
                "base_identity_id": base.identity_id,
                "base_pack_version": base.pack_version,
                "base_pack_sha256": canonical_sha256(base),
                "parent_draft_sha256": None,
                "state": "staged",
                "presentation": _presentation(markdown, base=base),
            }
        )
        if not identity_presentation_diff(draft, base):
            raise SoulMarkdownError("SOUL.md verandert de huidige identiteit niet")
        return draft
    except (TypeError, ValueError) as error:
        if isinstance(error, SoulMarkdownError):
            raise
        raise SoulMarkdownError(
            "SOUL.md past niet bij een veilige identiteitswijziging"
        ) from error


__all__ = [
    "MAX_SOUL_BYTES",
    "SoulMarkdownError",
    "identity_to_soul_markdown",
    "soul_markdown_sha256",
    "soul_markdown_to_draft",
]
