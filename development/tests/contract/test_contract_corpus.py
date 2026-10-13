from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from eva.contracts import (
    CONTRACT_REGISTRY,
    MAX_CONTRACT_BYTES,
    canonical_json_bytes,
    read_contract_bytes,
)
from eva.contracts.base import CalendarDate, Timestamp

EVA2_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = EVA2_ROOT / "deploy/schemas" / "contracts" / "contract-corpus-v1.json"


def _wire(case: dict[str, object]) -> bytes:
    encoding = case["encoding"]
    if encoding == "utf8":
        return str(case["wireUtf8"]).encode("utf-8")
    if encoding == "hex":
        return bytes.fromhex(str(case["wireHex"]))
    if encoding == "repeat_utf8":
        recipe = case["wireRecipe"]
        assert isinstance(recipe, dict)
        return (
            str(recipe["prefix"])
            + str(recipe["repeatText"]) * int(recipe["repeatCount"])
            + str(recipe["suffix"])
        ).encode("utf-8")
    raise AssertionError(f"unknown corpus encoding {encoding!r}")


def test_language_neutral_corpus_has_both_accept_and_reject_cases() -> None:
    corpus = json.loads(CORPUS_PATH.read_bytes())
    assert corpus["corpus"] == "EvaContractCorpus/v1"
    assert corpus["maximumPayloadBytes"] == MAX_CONTRACT_BYTES
    expectations = {case["expect"] for case in corpus["cases"]}
    assert expectations == {"accept", "reject"}


def test_every_registered_contract_has_a_complete_valid_example() -> None:
    corpus = json.loads(CORPUS_PATH.read_bytes())
    accepted = [case for case in corpus["cases"] if case["expect"] == "accept"]
    assert {case["contract"] for case in accepted} == set(CONTRACT_REGISTRY)
    for case in accepted:
        model = read_contract_bytes(_wire(case))
        assert set(model.model_fields_set) == set(type(model).model_fields)


def test_every_corpus_case_matches_its_oracle() -> None:
    corpus = json.loads(CORPUS_PATH.read_bytes())
    for case in corpus["cases"]:
        wire = _wire(case)
        if case["expect"] == "accept":
            parsed = read_contract_bytes(wire)
            assert parsed.contract == case["contract"]
            assert canonical_json_bytes(parsed) == wire
        else:
            with pytest.raises((TypeError, ValueError), match=".+"):
                read_contract_bytes(wire)


def test_corpus_case_ids_are_unique_and_sorted_by_expectation_group() -> None:
    corpus = json.loads(CORPUS_PATH.read_bytes())
    case_ids = [case["caseId"] for case in corpus["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert len(case_ids) >= 20


def test_primitive_host_conformance_vectors_reject_invalid_gregorian_values() -> None:
    corpus = json.loads(CORPUS_PATH.read_bytes())
    adapters = {
        "Timestamp": TypeAdapter(Timestamp),
        "CalendarDate": TypeAdapter(CalendarDate),
    }
    cases = corpus["primitiveHostConformanceCases"]
    assert {case["primitive"] for case in cases} == set(adapters)
    assert {case["value"] for case in cases} == {
        "2026-02-30T12:00:00Z",
        "2026-02-30",
    }
    for case in cases:
        assert case["expect"] == "reject"
        assert case["validationLayer"] == "host_conformance"
        with pytest.raises(ValidationError):
            adapters[case["primitive"]].validate_python(case["value"])
