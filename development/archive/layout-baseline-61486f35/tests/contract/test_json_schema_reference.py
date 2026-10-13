"""Independent Draft 2020-12 checks: deliberately no eva or Pydantic import."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = EVA2_ROOT / "schemas" / "contracts"


def test_every_schema_is_consumed_and_round_tripped_without_eva_runtime() -> None:
    manifest = json.loads((SCHEMA_ROOT / "manifest.json").read_bytes())
    corpus = json.loads((SCHEMA_ROOT / "contract-corpus-v1.json").read_bytes())
    examples_by_contract: dict[str, list[dict[str, object]]] = {}
    for case in corpus["cases"]:
        if case["expect"] == "accept":
            examples_by_contract.setdefault(case["contract"], []).append(
                json.loads(case["wireUtf8"])
            )
    assert set(examples_by_contract) == set(manifest["schemas"])

    for contract_name, expected_sha256 in manifest["schemas"].items():
        path = SCHEMA_ROOT / f"{contract_name.replace('/', '-')}.schema.json"
        raw_schema = path.read_bytes()
        assert hashlib.sha256(raw_schema).hexdigest() == expected_sha256
        schema = json.loads(raw_schema)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for example in examples_by_contract[contract_name]:
            validator.validate(example)
            round_tripped = json.loads(
                json.dumps(
                    example, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
            validator.validate(round_tripped)


def test_valid_language_neutral_corpus_is_accepted_by_its_own_schema() -> None:
    corpus = json.loads((SCHEMA_ROOT / "contract-corpus-v1.json").read_bytes())
    assert corpus["authority"] == "language_neutral_validation_fixture_only"
    assert corpus["productClaim"] is False
    for case in corpus["cases"]:
        if case["expect"] != "accept":
            continue
        wire = case["wireUtf8"].encode("utf-8")
        payload = json.loads(wire)
        assert (
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            == wire
        )
        schema_path = SCHEMA_ROOT / f"{case['contract'].replace('/', '-')}.schema.json"
        Draft202012Validator(json.loads(schema_path.read_bytes())).validate(payload)


def test_schema_addressable_invalid_cases_are_rejected_without_eva_runtime() -> None:
    corpus = json.loads((SCHEMA_ROOT / "contract-corpus-v1.json").read_bytes())
    checked: list[str] = []
    for case in corpus["cases"]:
        if case.get("validationLayer") != "json_schema":
            continue
        payload = json.loads(case["wireUtf8"])
        contract_name = payload["contract"]
        schema_path = SCHEMA_ROOT / f"{contract_name.replace('/', '-')}.schema.json"
        errors = list(
            Draft202012Validator(json.loads(schema_path.read_bytes())).iter_errors(
                payload
            )
        )
        assert errors, case["caseId"]
        checked.append(case["caseId"])
    assert len(checked) >= 9


def test_non_schema_semantics_are_manifested_as_host_conformance() -> None:
    corpus = json.loads((SCHEMA_ROOT / "contract-corpus-v1.json").read_bytes())
    manifest = json.loads((SCHEMA_ROOT / "manifest.json").read_bytes())
    host_contracts = {
        json.loads(case["wireUtf8"])["contract"]
        for case in corpus["cases"]
        if case.get("validationLayer") == "host_conformance"
    }
    assert host_contracts
    assert host_contracts.issubset(manifest["hostConformanceRules"])

    primitive_cases = corpus["primitiveHostConformanceCases"]
    assert {case["primitive"] for case in primitive_cases} == set(
        manifest["primitiveHostConformanceRules"]
    )
    assert all(
        case["validationLayer"] == "host_conformance" and case["expect"] == "reject"
        for case in primitive_cases
    )
