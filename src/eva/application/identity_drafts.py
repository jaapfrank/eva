"""Pure D02 identity-draft validation and deterministic presentation diffs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack, IdentityPackDraft


DraftableIdentityField = Literal[
    "display_name",
    "pronouns",
    "language",
    "voice_style",
    "temperament",
    "humour",
    "channel_presentations",
]

_DRAFTABLE_FIELDS: tuple[DraftableIdentityField, ...] = (
    "display_name",
    "pronouns",
    "language",
    "voice_style",
    "temperament",
    "humour",
    "channel_presentations",
)


@dataclass(frozen=True, slots=True)
class IdentityPresentationDifference:
    """One deterministic, field-scoped difference from a bound source pack."""

    field: DraftableIdentityField
    before: JsonValue
    after: JsonValue


def validate_identity_draft_base(draft: IdentityPackDraft, base: IdentityPack) -> None:
    """Refuse a stale, cross-identity, or cross-version draft before any use."""

    if draft.base_identity_id != base.identity_id:
        raise ValueError("identity draft base identity differs")
    if draft.base_pack_version != base.pack_version:
        raise ValueError("identity draft base pack version differs")
    if draft.base_pack_sha256 != canonical_sha256(base):
        raise ValueError("identity draft base pack hash differs")


def identity_presentation_diff(
    draft: IdentityPackDraft, base: IdentityPack
) -> tuple[IdentityPresentationDifference, ...]:
    """Return canonical field differences without mutating or producing a pack."""

    validate_identity_draft_base(draft, base)
    source = base.model_dump(mode="json")
    patch = draft.presentation.model_dump(mode="json", exclude_none=True)
    differences: list[IdentityPresentationDifference] = []
    for field in _DRAFTABLE_FIELDS:
        if field not in patch or source[field] == patch[field]:
            continue
        differences.append(
            IdentityPresentationDifference(
                field=field,
                before=source[field],
                after=patch[field],
            )
        )
    return tuple(differences)


__all__ = [
    "DraftableIdentityField",
    "IdentityPresentationDifference",
    "identity_presentation_diff",
    "validate_identity_draft_base",
]
