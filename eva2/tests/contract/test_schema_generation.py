from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from eva.contracts import CONTRACT_REGISTRY, schema_filename

EVA2_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = EVA2_ROOT / "src" / "eva" / "contracts"
SCHEMA_ROOT = EVA2_ROOT / "schemas" / "contracts"
GENERATOR = EVA2_ROOT / "tools" / "generate_contract_schemas.py"


def test_every_registered_contract_has_a_closed_deterministic_schema() -> None:
    for contract_name, model in CONTRACT_REGISTRY.items():
        schema = model.model_json_schema(mode="validation")
        assert schema["additionalProperties"] is False, contract_name
        assert schema["properties"]["contract"]["const"] == contract_name
        expected_version = int(contract_name.rsplit("/v", 1)[1])
        assert schema["properties"]["schema_version"]["const"] == expected_version
        assert {"contract", "schema_version"}.issubset(schema["required"])
        assert (SCHEMA_ROOT / schema_filename(contract_name)).is_file()


def test_registry_matches_the_sealed_catalogue_plus_acceptance_bundle() -> None:
    expected = {
        "AttentionWake/v1",
        "AttentionWake/v2",
        "CapabilityGrant/v1",
        "CapabilityManifest/v1",
        "ClaimEvidenceBinding/v1",
        "Commitment/v1",
        "ConnectorBinding/v1",
        "ConnectorCandidate/v1",
        "ConnectorEnvelope/v1",
        "ConnectorErrorResponse/v1",
        "ConnectorInitializeRequest/v1",
        "ConnectorInitializeSuccess/v1",
        "ConnectorInvokeRequest/v1",
        "ConnectorInvokeSuccess/v1",
        "ConnectorManifest/v1",
        "ConnectorProtocolFailure/v1",
        "ConnectorResult/v1",
        "ConsentGrant/v1",
        "ContextSelection/v1",
        "CredentialGrant/v1",
        "DelegatedWork/v1",
        "DelegatedWorkGrant/v1",
        "DelegatedWorkResult/v1",
        "DelegatedWorkUsage/v1",
        "DeliveryIntent/v1",
        "DeliveryOffer/v1",
        "DeliveryOffer/v2",
        "DeliveryReceipt/v1",
        "DeliveryReceipt/v2",
        "DocumentDraft/v1",
        "DocumentationSnapshot/v1",
        "EffectAttempt/v1",
        "EffectIntent/v1",
        "EffectReadback/v1",
        "EffectReceipt/v1",
        "EpisodeState/v1",
        "EvaAcceptanceEvidenceBundle/v1",
        "EvaPortableArchive/v1",
        "EvidenceEnvelope/v1",
        "EvidenceEnvelope/v2",
        "ExperienceSourceEvent/v1",
        "IdentityPack/v1",
        "IdentityPackDraft/v1",
        "ImprovementCandidate/v1",
        "IngressClaim/v1",
        "IngressOutcome/v1",
        "IngressRequest/v1",
        "InstallationManifest/v1",
        "InstallationRemap/v1",
        "McpReadOnlyToolImport/v1",
        "MemoryFact/v1",
        "MemoryRecall/v1",
        "ModelAttempt/v1",
        "ModelDecision/v1",
        "ObjectRef/v1",
        "ObjectStore/v1",
        "OutcomeView/v1",
        "PlannerRequest/v1",
        "PlatformLock/v1",
        "PlatformLock/v2",
        "PlatformLock/v3",
        "PolicyPack/v1",
        "PresenceContext/v1",
        "PresenceReply/v1",
        "PromotionDecision/v1",
        "ReadAttempt/v1",
        "ReadIntent/v1",
        "ReadObservation/v1",
        "RelationshipBinding/v1",
        "ReleaseManifest/v1",
        "ReplyCandidate/v1",
        "TurnEnvelope/v1",
        "ValidatedPlan/v1",
    }
    assert set(CONTRACT_REGISTRY) == expected


def test_schema_manifest_binds_every_generated_schema() -> None:
    manifest = json.loads((SCHEMA_ROOT / "manifest.json").read_bytes())
    assert manifest["contractCount"] == len(CONTRACT_REGISTRY)
    assert set(manifest["schemas"]) == set(CONTRACT_REGISTRY)
    for contract_name, expected_digest in manifest["schemas"].items():
        actual = hashlib.sha256(
            (SCHEMA_ROOT / schema_filename(contract_name)).read_bytes()
        ).hexdigest()
        assert actual == expected_digest


def test_generator_check_is_clean_and_offline() -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=EVA2_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "schemas current" in completed.stdout


def test_contract_modules_do_not_import_framework_or_io_packages() -> None:
    forbidden_roots = {
        "alembic",
        "dbos",
        "fastapi",
        "httpx",
        "psycopg",
        "pydantic_ai",
        "requests",
        "sqlalchemy",
        "urllib",
    }
    for path in CONTRACT_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])
        assert forbidden_roots.isdisjoint(imported), (
            f"{path.name}: {sorted(forbidden_roots & imported)}"
        )
