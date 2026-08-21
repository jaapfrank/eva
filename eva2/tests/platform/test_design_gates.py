from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "eva2/release/check_design.py"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "eva2_release_design_checker", CHECKER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DesignGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = _checker()

    def test_platform_design_gate_is_green_without_runtime_claims(self) -> None:
        result = self.checker.platform_gate(REPO_ROOT)

        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["claimScope"], "design_only")
        self.assertFalse(result["runtimeEvidence"])
        self.assertFalse(result["installerClaim"])
        self.assertFalse(result["imageExecutionClaim"])
        self.assertFalse(result["platformSupportClaim"])

    def test_supply_design_gate_is_green_without_artifact_claims(self) -> None:
        result = self.checker.supply_gate(REPO_ROOT)

        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["claimScope"], "design_only")
        self.assertTrue(result["baseManifestMetadataSelected"])
        self.assertFalse(result["baseBytesExecuted"])
        self.assertFalse(result["finalImagesBuilt"])
        self.assertFalse(result["installerClaim"])
        self.assertFalse(result["runtimeSupportClaim"])

    def test_gate_results_are_byte_deterministic(self) -> None:
        first = self.checker.run_gate("ALL", REPO_ROOT)
        second = self.checker.run_gate("ALL", REPO_ROOT)

        def encode(value: object) -> str:
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )

        self.assertEqual(encode(first), encode(second))

    def test_revocation_policy_keeps_last_known_good_offline_recovery_available(
        self,
    ) -> None:
        release = REPO_ROOT / "eva2/release"
        revocation = self.checker.load_json(release / "revocation-policy.json")
        signature = self.checker.load_json(release / "signature-policy.json")
        trust = self.checker.load_json(release / "trust-root-slot.json")
        trust_schema = self.checker.load_json(
            release / "formats/eva-trust-root-v1.schema.json"
        )
        revocation_schema = self.checker.load_json(
            release / "formats/eva-revocation-list-v1.schema.json"
        )

        self.assertEqual(
            revocation["staleListPolicy"]["blocks"],
            ["new_install", "update", "activation"],
        )
        self.assertEqual(
            revocation["staleListPolicy"]["allowsLastKnownGood"],
            [
                "installed_image_restart",
                "rollback_to_previously_verified_installed_image",
            ],
        )
        self.assertTrue(revocation["staleListPolicy"]["knownRevocationAlwaysBlocks"])
        self.assertFalse(
            revocation["staleListPolicy"]["networkRefreshRequiredForAllowedOperation"]
        )
        self.assertEqual(
            revocation["minimumSupportedReleaseEnforcedFor"],
            ["new_install", "update", "activation"],
        )
        self.assertEqual(revocation["minimumValidRootSignatures"], 1)
        self.assertEqual(
            next(
                row["minimumValidSignatures"]
                for row in signature["scopePolicies"]
                if row["scope"] == "revocation_list"
            ),
            1,
        )
        self.assertEqual(signature["multisignaturePolicy"]["minimumThreshold"], 1)
        self.assertTrue(
            signature["multisignaturePolicy"]["additionalValidSignaturesAllowed"]
        )
        self.assertEqual(trust["minimumOfflineRootThreshold"], 1)
        self.assertEqual(trust_schema["properties"]["threshold"]["minimum"], 1)
        self.assertEqual(trust_schema["properties"]["keys"]["minItems"], 1)
        self.assertEqual(revocation_schema["properties"]["signatures"]["minItems"], 1)

    def test_final_image_slots_match_platform_and_assignments_exactly(self) -> None:
        release = REPO_ROOT / "eva2/release"
        oci = self.checker.load_json(release / "oci-image-slots.json")
        platform = self.checker.load_json(release / "platform-lock.json")

        final_ids = [slot["id"] for slot in oci["finalImageSlots"]]
        assignment_ids = set(oci["componentBaseAssignments"])
        platform_ids = {slot["component"] for slot in platform["oci_images"]}
        self.assertEqual(len(final_ids), len(set(final_ids)))
        self.assertEqual(set(final_ids), self.checker.OCI_COMPONENT_IDS)
        self.assertEqual(set(final_ids), assignment_ids)
        self.assertEqual(set(final_ids), platform_ids)

    def test_every_release_format_is_closed_json_schema(self) -> None:
        paths = sorted((REPO_ROOT / "eva2/release/formats").glob("*.schema.json"))
        self.assertEqual(len(paths), 7)
        for path in paths:
            with self.subTest(path=path.name):
                value = self.checker.load_json(path)
                self.assertEqual(
                    value["$schema"], "https://json-schema.org/draft/2020-12/schema"
                )
                self.assertEqual(value["type"], "object")
                self.assertIs(value["additionalProperties"], False)
                self.assertTrue(value["required"])


if __name__ == "__main__":
    unittest.main()
