from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "deploy/release/check_design.py"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "eva2_release_design_checker_negative", CHECKER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _copy_inputs(destination: Path) -> None:
    for directory in ("docs", "design", "transition"):
        relative = Path("development/archive/history/root") / directory
        shutil.copytree(REPO_ROOT / relative, destination / relative)
    shutil.copytree(REPO_ROOT / "development/design", destination / "development/design")
    shutil.copytree(REPO_ROOT / "deploy/release", destination / "deploy/release")
    shutil.copy2(REPO_ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(
        REPO_ROOT / "deploy/requirements.lock", destination / "deploy/requirements.lock"
    )


class FailClosedDesignGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = _checker()

    def test_hash_lock_rejects_unhashed_requirement(self) -> None:
        pins, hashes, problems = self.checker.parse_lock("example==1.0\n")

        self.assertEqual(pins, {"example": "1.0"})
        self.assertEqual(hashes, {"example": set()})
        self.assertIn("missing sha256 hash for example", problems)

    def test_platform_gate_rejects_fictional_support_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/platform-lock.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["tier1_targets"][0]["validation_state"] = "validated"
            value["tier1_targets"][0]["support_status"] = "tier1"
            value["tier1_targets"][0]["validation_receipt_sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.platform_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "tier1_claim_inflation", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_mutable_image_authority(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/oci-image-slots.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["baseSlots"][0]["authorityReference"] = (
                "docker.io/library/python:latest"
            )
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "oci_index_digest", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_unbound_gate_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/gate-toolchain.lock.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["requiredDistributions"]["pytest"] = "999.0.0"
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "gate_toolchain_contract", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_stale_policy_that_blocks_installed_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/revocation-policy.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["staleListPolicy"]["blocks"].append("installed_image_restart")
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "revocation_policy", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_mandatory_revocation_multisignature(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/signature-policy.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["scopePolicies"][-1]["minimumValidSignatures"] = 2
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "signature_policy", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_trust_root_schema_that_requires_multisignature(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/formats/eva-trust-root-v1.schema.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["properties"]["threshold"]["minimum"] = 2
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "trust_root_schema_threshold",
                {item["code"] for item in result["errors"]},
            )

    def test_supply_gate_rejects_revocation_schema_that_requires_multisignature(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/formats/eva-revocation-list-v1.schema.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["properties"]["signatures"]["minItems"] = 2
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "revocation_schema_threshold",
                {item["code"] for item in result["errors"]},
            )

    def test_supply_gate_rejects_missing_postgresql_final_slot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/oci-image-slots.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["finalImageSlots"] = [
                slot for slot in value["finalImageSlots"] if slot["id"] != "postgresql"
            ]
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "oci_final_slot_set", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_duplicate_final_slot_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/oci-image-slots.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            postgresql = next(
                slot for slot in value["finalImageSlots"] if slot["id"] == "postgresql"
            )
            postgresql["id"] = "living_core"
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "oci_final_slot_set", {item["code"] for item in result["errors"]}
            )

    def test_supply_gate_rejects_platform_slot_set_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _copy_inputs(root)
            path = root / "deploy/release/platform-lock.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["oci_images"][0]["component"] = "unexpected_component"
            path.write_text(json.dumps(value), encoding="utf-8")

            result = self.checker.supply_gate(root)

            self.assertEqual(result["status"], "failed")
            codes = {item["code"] for item in result["errors"]}
            self.assertIn("oci_platform_slot_set", codes)
            self.assertIn("oci_final_slot_set", codes)


if __name__ == "__main__":
    unittest.main()
