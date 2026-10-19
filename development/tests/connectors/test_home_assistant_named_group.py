"""Provider-owned compound names and fresh group proof, with no external effects."""
import asyncio
from urllib.error import HTTPError

import pytest
from eva.adapters.connectors.home_assistant import HomeAssistantConfig, HomeAssistantToolAdapter
from eva.application.orchestration import ToolRequest, ToolDispatchResult


def row(name, label=None, state="off", members=None):
    attributes = {"friendly_name": label or name}
    if members is not None:
        attributes["entity_id"] = members
    return {"entity_id": "light." + name, "state": state, "attributes": attributes}


def adapter(monkeypatch, rows):
    tool = HomeAssistantToolAdapter(HomeAssistantConfig("http://ha.test", "synthetic"))
    monkeypatch.setattr(tool, "_light_catalog_sync", lambda: rows)
    return tool


@pytest.mark.parametrize("suffix", ["verlichting", "licht", "lichten", "lamp", "lampen"])
def test_compound_resolves_only_existing_unique_name(monkeypatch, suffix):
    tool = adapter(monkeypatch, [row("atelier")])
    result = asyncio.run(tool.resolve_light_targets("atelier" + suffix))
    assert [item["entity_id"] for item in result] == ["light.atelier"]


def test_literal_name_precedes_compound_and_ambiguous_fallback_rejects(monkeypatch):
    tool = adapter(monkeypatch, [row("atelier"), row("atelierlicht")])
    assert asyncio.run(tool.resolve_light_targets("atelierlicht"))[0]["entity_id"] == "light.atelierlicht"
    tool = adapter(monkeypatch, [row("a", "Atelier"), row("b", "Atelier")])
    with pytest.raises(ValueError, match="ambiguous"):
        asyncio.run(tool.resolve_light_targets("atelierverlichting"))


@pytest.mark.parametrize("target", ["verlichting atelier", "de verlichting in het atelier",
    "atelier verlichting", "de verlichting atelier"])
def test_separate_lighting_noun_resolves_same_existing_named_group(monkeypatch, target):
    tool = adapter(monkeypatch, [row("atelier"), row("atelier_wall", "Atelier wall")])
    assert [r["entity_id"] for r in asyncio.run(tool.resolve_light_targets(target))] == ["light.atelier"]


def test_literal_lighting_name_wins_over_simplified_name(monkeypatch):
    tool = adapter(monkeypatch, [row("atelier"), row("atelier_lighting", "Atelier verlichting")])
    assert asyncio.run(tool.resolve_light_targets("Atelier verlichting"))[0]["entity_id"] == "light.atelier_lighting"


def test_literal_generic_name_is_not_reinterpreted_as_all_lights(monkeypatch):
    tool = adapter(monkeypatch, [row("lighting", "Verlichting"), row("atelier")])
    assert asyncio.run(tool.resolve_light_targets("Verlichting"))[0]["entity_id"] == "light.lighting"
    with pytest.raises(ValueError):
        asyncio.run(adapter(monkeypatch, [row("atelier")]).resolve_light_targets("Verlichting"))


@pytest.mark.parametrize("rows", [[row("a", "Atelier"), row("b", "Atelier")],
    [row("atelier", state="unavailable")]])
def test_separate_lighting_noun_keeps_ambiguity_and_unavailability(monkeypatch, rows):
    with pytest.raises(ValueError):
        asyncio.run(adapter(monkeypatch, rows).resolve_light_targets("verlichting atelier"))


@pytest.mark.parametrize("rows", [[row("atelier_a")], [row("atelier", state="unavailable")], []])
def test_compound_does_not_guess_expand_or_skip_unavailable(monkeypatch, rows):
    with pytest.raises(ValueError):
        asyncio.run(adapter(monkeypatch, rows).resolve_light_targets("atelierverlichting"))


def command():
    return ToolRequest("thread", "operation", "checkpoint", "home_assistant.light.turn_off",
        {"domain": "light", "entity_id": "light.atelier", "service": "turn_off", "service_data": {}},
        {"entity_id": "light.atelier", "state": "off"})


@pytest.mark.parametrize("second,expected", [("off", "verified"), ("on", "pending"),
    ("unavailable", "conflict"), ("unknown", "conflict"), ("missing", "conflict"), ("nested", "conflict")])
def test_named_group_reads_actual_members_and_never_duplicates_effect(monkeypatch, second, expected):
    tool = adapter(monkeypatch, [])
    calls = []
    async def read(method, path, payload=None):
        assert method == "GET"
        calls.append(path)
        if path.endswith("light.atelier"):
            return row("atelier", members=["light.a", "light.b"])
        if path.endswith("light.a"):
            return row("a")
        if second == "missing":
            raise HTTPError(path, 404, "missing", {}, None)
        return row("b", state="off" if second == "nested" else second,
            members=["light.c"] if second == "nested" else None)
    monkeypatch.setattr(tool, "_request_json", read)
    result = asyncio.run(tool.observe(command(), ToolDispatchResult(True, None, {})))
    assert result.disposition == expected
    assert len(calls) == 3 and len(set(calls)) == 3
    assert result.evidence["group_state_confirmed"] is True
    assert result.evidence["unverified_member_ids"] == ([] if expected == "verified" else ["light.b"])
    if expected == "verified":
        assert result.observed == command().expected_proof
    else:
        assert result.evidence["group_verification"] == ("pending" if expected == "pending" else "partial")


@pytest.mark.parametrize("members", [[], "light.a", ["person.owner"], ["light.a"] * 2,
    ["light.atelier"], ["light.a"] * 33, [None]])
def test_invalid_membership_cannot_claim_success_or_read_private_domains(monkeypatch, members):
    tool = adapter(monkeypatch, [])
    calls = []
    async def read(method, path, payload=None):
        calls.append(path)
        return row("atelier", members=members)
    monkeypatch.setattr(tool, "_request_json", read)
    result = asyncio.run(tool.observe(command(), None))
    assert result.disposition == "conflict"
    assert result.evidence["group_verification"] == "invalid_membership"
    assert len(calls) == 1


def test_ordinary_light_proof_contract_unchanged(monkeypatch):
    tool = adapter(monkeypatch, [])
    async def read(*args): return row("atelier")
    monkeypatch.setattr(tool, "_request_json", read)
    result = asyncio.run(tool.observe(command(), None))
    assert result.disposition == "verified"
    assert result.observed == command().expected_proof
    assert "group_verification" not in result.evidence


def test_settling_group_observations_do_not_repeat_write(monkeypatch):
    from eva.adapters.connectors import home_assistant as module
    tool = adapter(monkeypatch, [])
    sent = []
    monkeypatch.setattr(module, "call_assist_effect", lambda **kwargs: sent.append(kwargs["entity_id"]) or {})
    current = "on"
    async def read(method, path, payload=None):
        if path.endswith("light.atelier"):
            return row("atelier", state=current, members=["light.a"])
        return row("a", state=current)
    monkeypatch.setattr(tool, "_request_json", read)
    dispatch = asyncio.run(tool.execute(command()))
    assert asyncio.run(tool.observe(command(), dispatch)).disposition == "pending"
    current = "off"
    assert asyncio.run(tool.observe(command(), dispatch)).disposition == "verified"
    assert sent == ["light.atelier"]
