from __future__ import annotations

import pytest

from tools.migrate_hindsight_provenance import trusted_source_text


def test_direct_user_source_drops_the_assistant_reply() -> None:
    source = (
        "Gebruiker (directe bron): Alleen stil waarschuwen.\n"
        "Eva (gesprekscontext, geen gebruikersfeit): Dan laat ik een lamp knipperen."
    )
    assert trusted_source_text(source, "direct_user") == (
        "Gebruiker (directe bron): Alleen stil waarschuwen."
    )


def test_legacy_transcript_keeps_only_user_turns() -> None:
    source = (
        "Jaap: Eerst met geluid.\n"
        "Eva: Begrepen.\n"
        "Jaap: Correctie: alleen stil.\n"
        "Eva: Dan zet ik een lamp aan."
    )
    assert trusted_source_text(source, "direct_user") == (
        "Gebruiker (directe bron): Eerst met geluid.\n\nCorrectie: alleen stil."
    )


def test_unknown_direct_user_format_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown source format"):
        trusted_source_text("Eva zegt dat de gebruiker iets wil.", "direct_user")


def test_non_user_authority_document_is_preserved() -> None:
    assert trusted_source_text("Uitvoering is feitelijk bevestigd.", "host_policy") == (
        "Uitvoering is feitelijk bevestigd."
    )
