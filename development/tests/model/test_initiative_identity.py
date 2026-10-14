"""Model-free identity composition at the ordinary initiative provider boundary."""
from datetime import UTC, datetime

import pytest

from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.conversation import IdentityProfile
from eva.application.sensory import SensoryEvent
from tests.model.test_conversation_model import _memory


@pytest.mark.parametrize("pack_present", [True, False])
def test_initiative_receives_same_identity_as_conversation(monkeypatch, tmp_path, pack_present):
    pack = load_current_identity_pack() if pack_present else None
    identity = IdentityProfile(pack.display_name if pack is not None else "Noor", "A synthetic private identity.",
        ("Keep evidence distinct from inference.",), 80, pack)
    model = PydanticConversationModel(auth_file=tmp_path / "never-read.json")
    captured = []

    class CapturedWithoutDispatch(Exception):
        pass

    def capture(body, parser, **kwargs):
        captured.append(body)
        assert kwargs == {"autonomous": True, "include_usage": True}
        raise CapturedWithoutDispatch

    monkeypatch.setattr(model, "_request_using", capture)
    event = SensoryEvent("initiative-review", "fixture-review", "private_opportunity_review",
        datetime(2026, 10, 14, 12, tzinfo=UTC), {})
    with pytest.raises(CapturedWithoutDispatch):
        model.evaluate_sensory_event(event, identity, _memory())
    instructions = captured[0]["instructions"]
    assert f"Je bent {identity.name}. A synthetic private identity." in instructions
    assert "Vaste principes:\n- Keep evidence distinct from inference." in instructions
    assert instructions.count("Actief procedureel IdentityPack:") == 1
    if pack is not None:
        assert f"IdentityPack: {pack.identity_id} v{pack.pack_version}" in instructions
        assert "- Initiatief: " + "; ".join(pack.initiative_preferences) in instructions
        assert "- Waarden: " + ", ".join(pack.values) in instructions
    else:
        assert "Geen versieerbaar IdentityPack geladen." in instructions
    block = model._identity_instructions(identity)
    assert block in instructions
    assert block in model._instructions(identity, _memory(), ())
    assert "forceer geen idee" in instructions
    assert "Het verruimt geen rechten" in instructions
    assert len(captured[0]["tools"]) == 1
    assert not (tmp_path / "never-read.json").exists()
