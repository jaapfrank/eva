"""Formatting equivalence never supplies meaning, authority or old effect proof."""
import hashlib
import pytest
import sqlalchemy as sa

from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.providers.home_assistant_command_cache import _surface_digest
from eva.adapters.postgres.operational_timeline import operational_events
from tests.postgres.test_home_assistant_command_reuse import database, rig  # noqa: F401


@pytest.mark.parametrize("variant", [
    "zet tuin uit", "ZET TUIN UIT", "Zet   tuin uit", "zet\ttuin\nuit",
])
def test_case_and_ascii_whitespace_use_fresh_execution_with_original_source(rig, variant):
    source = rig.turn("Zet tuin uit")
    rig.respond(source)
    current = rig.turn(variant)
    rig.journal.admit(current)
    candidate = rig.cache.lookup(current)
    assert candidate is not None and candidate.source_turn == source
    assert candidate.metadata["input_sha256"] == hashlib.sha256(source.text.encode()).hexdigest()
    assert candidate.metadata["surface_input_sha256"] == _surface_digest(variant)
    reply = rig.respond(current)
    assert reply.provider == "eva:verified-reuse" and reply.model_usage.model_attempts == 0
    assert len(rig.model.calls) == 1 and len(rig.effects) == 2 and len(rig.reads) == 2
    with rig.cache._engine.connect() as connection:
        threads = connection.execute(sa.select(operational_events.c.thread_id).where(
            operational_events.c.event_type == "TOOL_REQUEST_ADMITTED",
            operational_events.c.payload["data"]["source_message_id"].astext.in_(
                (source.message_id, current.message_id)))).scalars().all()
    assert len(threads) == len(set(threads)) == 2


def test_canonical_ingress_and_target_accents_remain_required(rig):
    rig.states[0]["attributes"]["friendly_name"] = "Café"
    with pytest.raises(ValueError, match="NFC normalized"):
        rig.respond(rig.turn("Zet cafe\u0301 uit"))
    assert not rig.effects
    rig.respond(rig.turn("Zet café uit"))
    assert rig.respond(rig.turn("ZET café UIT")).provider == "eva:verified-reuse"
    current = rig.turn("zet cafe uit")
    rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects) == 2


@pytest.mark.parametrize("variant", [
    "Zet tuin uit.", "Zet tuin uit!", "Zet tuin uit?", "Zet tuin niet uit",
    "Zet uit tuin", "Zet die uit", "Zet tuin aan", "Zet tuin uit als ik weg ben",
    "Zet\u00a0tuin uit", "Zet\u2003tuin uit", "Zet tuin uit en vertel het weer",
])
def test_meaning_punctuation_and_unicode_spaces_remain_cache_misses(rig, variant):
    rig.respond(rig.turn("Zet tuin uit"))
    current = rig.turn(variant)
    rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects) == 1


def test_full_unicode_casefold_expansions_are_not_equivalent(rig):
    rig.respond(rig.turn("Zet straße uit"))
    current = rig.turn("Zet strasse uit")
    rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None


def test_legacy_without_surface_scheme_is_exact_only_even_after_index_reseed(rig, monkeypatch):
    original = rig.cache.admission_metadata

    def legacy(*args):
        metadata = original(*args)
        return {k: v for k, v in metadata.items() if not k.startswith("surface_")}

    monkeypatch.setattr(rig.cache, "admission_metadata", legacy)
    rig.respond(rig.turn())
    rig.cache._index.clear()
    rig.cache._seeded.clear()
    exact = rig.turn()
    rig.journal.admit(exact)
    assert rig.cache.lookup(exact) is not None
    # This non-executed diagnostic ingress would otherwise invalidate context.
    assert rig.cache._known(rig.turn("ZET DE LAMP LIGHT.YARD UIT")) is False


def test_surface_source_remains_usable_after_disposable_index_reseed(rig):
    source = rig.turn("Zet tuin uit")
    rig.respond(source)
    rig.cache._index.clear()
    rig.cache._seeded.clear()
    assert rig.respond(rig.turn("ZET  TUIN UIT")).provider == "eva:verified-reuse"
    assert len(rig.model.calls) == 1 and len(rig.effects) == 2


@pytest.mark.parametrize("field,value", [
    ("surface_scheme", "unknown-scheme"), ("surface_input_sha256", "0" * 64),
])
def test_invalid_surface_binding_cannot_reuse_a_variant(rig, monkeypatch, field, value):
    original = rig.cache.admission_metadata
    monkeypatch.setattr(rig.cache, "admission_metadata",
                        lambda *args: {**original(*args), field: value})
    rig.respond(rig.turn("Zet tuin uit"))
    current = rig.turn("ZET TUIN UIT")
    rig.journal.admit(current)
    assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects) == 1


@pytest.mark.parametrize("change", ["erasure", "revocation", "context", "device"])
def test_surface_reuse_keeps_existing_live_boundaries(rig, change):
    source = rig.turn("Zet tuin uit")
    rig.respond(source)
    if change == "erasure":
        rig.journal.erase_message_content(principal_id=source.principal_id,
            message_id=source.message_id, operation_sha256="e" * 64, erased_at=rig.now)
    elif change == "revocation":
        rig.revoked[0] = True
    elif change == "context":
        rig.journal.admit(rig.turn("Nee, ik bedoelde die andere lamp"))
    else:
        rig.device.binding = "b" * 64
    current = rig.turn("ZET   TUIN UIT")
    rig.journal.admit(current)
    if change == "revocation":
        with pytest.raises(ConversationConflict, match="grant revoked"):
            rig.port.reuse_verified_action(current)
    else:
        assert rig.port.reuse_verified_action(current) is None
    assert len(rig.effects) == 1


def test_different_current_learned_interpretations_cannot_use_newest_wins(rig, monkeypatch):
    rig.respond(rig.turn("Zet tuin uit"))
    rig.model.choose = lambda *args: ConversationActionCall("call:conflicting-source",
        "set_home_assistant_light", {"target": "light.yard", "state": "on",
        "finish_without_reply": True, "reusable_command": True})
    source = rig.turn("ZET TUIN UIT")
    # The fixture model deliberately supplies contradictory qualified evidence.
    with monkeypatch.context() as patch:
        patch.setattr(rig.cache, "lookup", lambda turn: None)
        rig.respond(source)
    current = rig.turn("zet tuin uit")
    rig.journal.admit(current)
    assert rig.cache.lookup(current) is None
    assert len(rig.effects) == 2
