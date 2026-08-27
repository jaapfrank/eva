from __future__ import annotations

import pytest

from eva.application.conversation import load_current_identity_pack
from eva.application.identity_drafts import identity_presentation_diff
from eva.application.soul_markdown import SoulMarkdownError, soul_markdown_to_draft


def test_soul_markdown_creates_only_a_bound_staged_presentation_draft() -> None:
    base = load_current_identity_pack()
    draft = soul_markdown_to_draft(
        "# Eva\n\n## Naam\nEvi\n\n## Temperament\nwarm, nieuwsgierig\n\n## Humor\ndroog wanneer het helpt\n",
        draft_id="identity-draft:soul:001",
        base=base,
    )

    assert draft.state == "staged"
    assert draft.base_identity_id == base.identity_id
    assert [(item.field, item.after) for item in identity_presentation_diff(draft, base)] == [
        ("display_name", "Evi"),
        ("temperament", ["warm", "nieuwsgierig"]),
        ("humour", "droog wanneer het helpt"),
    ]
    assert base.display_name == "Eva"


def test_soul_markdown_can_propose_a_closed_voice_style() -> None:
    base = load_current_identity_pack()
    draft = soul_markdown_to_draft(
        "# Eva\n\n## Stem\nwarm, jong, vlot en natuurlijk\n",
        draft_id="identity-draft:soul:voice:001",
        base=base,
    )

    assert draft.presentation.voice_style is not None
    assert draft.presentation.voice_style.name == "warm_jonge_vlotte_huisgenoot"
    assert draft.presentation.voice_style.qualities == [
        "warm",
        "jong",
        "vlot en natuurlijk",
    ]
    assert "scherp" in draft.presentation.voice_style.forbidden_wording
    assert base.voice_style.name == "warm_practical_curious"


@pytest.mark.parametrize(
    "markdown",
    (
        "## Tools\nbrowser\n",
        "## Naam\nEvi\n## Naam\nEva\n",
        "## Naam\nEvi\nEen tweede regel\n",
        "# Eva\n",
        "## Voornaamwoorden\nzij, , haar\n",
        "## Naam\nEva\n",
    ),
)
def test_soul_markdown_rejects_anything_outside_small_presentation_scope(markdown: str) -> None:
    with pytest.raises(SoulMarkdownError):
        soul_markdown_to_draft(
            markdown,
            draft_id="identity-draft:soul:002",
            base=load_current_identity_pack(),
        )
