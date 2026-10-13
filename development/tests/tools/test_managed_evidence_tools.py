from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from urllib import parse, request
from uuid import UUID

import pytest


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "src/tools"


def _load(name: str):
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"managed_tool_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lab36_fixture(root: Path) -> Path:
    benchmark_path = root / "development/benchmark/eva-agent-benchmark-v1.json"
    evidence_root = root / "development/evidence/living-core"
    benchmark_path.parent.mkdir(parents=True)
    evidence_root.mkdir(parents=True)
    benchmark = {
        "contract": "LivedAgentBenchmark/v1",
        "benchmark_id": "LAB-36-fixture",
        "cases": [
            {"case_id": f"C{index:02d}", "track": "core_live"} for index in range(15)
        ],
    }
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")
    receipt_path = evidence_root / "fixture.json"
    receipt_path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    binding = {
        "path": "development/evidence/living-core/fixture.json",
        "sha256": _sha256(receipt_path),
    }
    index = {
        "contract": "EvaLab36CoreLiveEvidenceIndex/v1",
        "benchmark_sha256": _sha256(benchmark_path),
        "cases": [
            {
                "case_id": f"C{case_index:02d}",
                "critical_failures": [],
                "evidence": [binding],
                "semantic_gates": "passed",
                "status": "passed_live_b",
            }
            for case_index in range(15)
        ],
    }
    index_path = evidence_root / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return index_path.relative_to(root)


def test_lab36_checker_binds_catalog_and_receipt_bytes(tmp_path: Path) -> None:
    module = _load("check_lab36_core_live_evidence")
    index_path = _lab36_fixture(tmp_path)

    result = module.check(tmp_path, index_path)

    assert result["case_count"] == 15
    assert result["evidence_bindings"] == 15
    receipt = tmp_path / "development/evidence/living-core/fixture.json"
    receipt.write_text('{"status":"changed"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="evidence hash differs"):
        module.check(tmp_path, index_path)


def test_living_candidate_loader_rejects_changed_source_binding(
    tmp_path: Path,
) -> None:
    module = _load("check_living_b_candidate")
    source = tmp_path / "src/eva/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    evidence = tmp_path / "development/evidence/candidate.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "source_files": {
                    "src/eva/example.py": _sha256(source),
                }
            }
        ),
        encoding="utf-8",
    )

    value, evidence_hash = module._load_evidence(
        tmp_path, "development/evidence/candidate.json"
    )

    assert value["source_files"]["src/eva/example.py"] == _sha256(source)
    assert evidence_hash == _sha256(evidence)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="candidate source changed"):
        module._load_evidence(tmp_path, "development/evidence/candidate.json")


def test_paid_fulfillment_contract_server_is_loopback_and_records_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_stub = ModuleType("eva.bootstrap.runtime")
    runtime_stub.PostgresHindsightMemoryRuntime = type(  # type: ignore[attr-defined]
        "PostgresHindsightMemoryRuntime", (), {}
    )
    monkeypatch.setitem(sys.modules, "eva.bootstrap.runtime", runtime_stub)
    module = _load("live_paid_fulfillment_gate")
    plan = SimpleNamespace(
        operation_id="operation:test:001",
        price_eur_cents=125,
        release_sha256="a" * 64,
        sha256="b" * 64,
    )
    with module.StripeContractServer() as provider:
        provider.plan = plan
        base_url = f"http://127.0.0.1:{provider.server.server_port}"
        with request.urlopen(f"{base_url}/v1/account", timeout=2) as response:
            assert json.loads(response.read()) == {"id": module.ACCOUNT}
        payload = parse.urlencode({"type": "payment_link"}).encode("ascii")
        provider_request = request.Request(
            f"{base_url}/v1/payment_links",
            data=payload,
            method="POST",
            headers={"Idempotency-Key": plan.operation_id},
        )
        with request.urlopen(provider_request, timeout=2) as response:
            assert response.status == 201

    assert provider.posts == [
        {"type": ["payment_link"], "_idempotency_key": [plan.operation_id]}
    ]


def test_hindsight_migration_cleans_direct_user_and_batches_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load("migrate_hindsight_provenance")
    documents = [
        {
            "id": f"document-{index}",
            "original_text": (
                "Gebruiker (directe bron): Het is koud.\n"
                "Eva (gesprekscontext, geen gebruikersfeit): Ik regel het."
            ),
            "retain_params": {
                "context": "fixture",
                "event_date": "2026-08-31T12:00:00Z",
                "metadata": {"trust": "direct_user"},
                "observation_scopes": [],
            },
            "tags": [],
        }
        for index in range(2)
    ]
    posts: list[tuple[str, dict[str, object]]] = []
    waits: list[tuple[str, str, str]] = []
    monkeypatch.setattr(module, "_source_documents", lambda _url: documents)

    def fake_request(
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert method == "POST"
        assert payload is not None
        posts.append((url, payload))
        operation_id = payload["operation_id"]
        assert isinstance(operation_id, str)
        UUID(operation_id)
        return {"success": True, "operation_id": operation_id}

    monkeypatch.setattr(module, "_json_request", fake_request)
    monkeypatch.setattr(
        module,
        "_wait_for_operation",
        lambda base, bank, operation: waits.append((base, bank, operation)),
    )

    result = module.migrate(
        "http://source.invalid/export",
        "http://127.0.0.1:9999/",
        "fixture-bank",
        batch_size=1,
    )

    assert result == {
        "documents": 2,
        "direct_user_documents": 2,
        "authority_documents": 0,
        "assistant_text_retained_as_direct_user": 0,
    }
    assert len(posts) == 2
    assert len(waits) == 2
    retained = [post[1]["items"][0]["content"] for post in posts]  # type: ignore[index]
    assert retained == ["Gebruiker (directe bron): Het is koud."] * 2
