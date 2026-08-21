from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPOSITORY_ROOT / "eva2/tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_4d_freeze as freeze  # noqa: E402


class FourDPreCodeFreezeTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        paths = set(freeze.EXPECTED_IMMUTABLE_FILES)
        paths.update(
            {
                freeze.PROFILE_PATH,
                freeze.BINDING_PATH,
                freeze.LOCK_4D_PATH,
                freeze.PYPROJECT_PATH,
                freeze.CHECKER_PATH,
                freeze.TEST_PATH,
            }
        )
        for relative in sorted(paths):
            source = REPOSITORY_ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return temporary, root

    @staticmethod
    def _load(root: Path, relative: str) -> dict[str, Any]:
        value = json.loads((root / relative).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssertionError(f"fixture root is not an object: {relative}")
        return value

    @staticmethod
    def _write(root: Path, relative: str, value: dict[str, Any]) -> None:
        (root / relative).write_bytes(freeze.canonical_json_bytes(value, final_lf=True))

    def _mutated_profile(
        self, mutate: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        profile = self._load(REPOSITORY_ROOT, freeze.PROFILE_PATH)
        mutate(profile)
        return profile

    def _assert_profile_mutation_fails(
        self, mutate: Callable[[dict[str, Any]], None], expected: str
    ) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        profile = self._load(root, freeze.PROFILE_PATH)
        mutate(profile)
        self._write(root, freeze.PROFILE_PATH, profile)
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"], receipt)
        self.assertTrue(
            any(expected in error for error in receipt["errors"]), receipt["errors"]
        )

    def test_repository_freeze_passes_and_reports_all_six_freeze_hashes(self) -> None:
        receipt = freeze.verify(REPOSITORY_ROOT)
        self.assertEqual("passed", receipt["status"], receipt["errors"])
        self.assertEqual(
            {
                freeze.PROFILE_PATH,
                freeze.BINDING_PATH,
                freeze.LOCK_4D_PATH,
                freeze.PYPROJECT_PATH,
                freeze.CHECKER_PATH,
                freeze.TEST_PATH,
            },
            set(receipt["verifiedHashes"]),
        )
        self.assertFalse(receipt["networkUsed"])
        self.assertFalse(receipt["databaseUsed"])
        self.assertFalse(receipt["serviceStarted"])
        self.assertFalse(receipt["dependenciesInstalled"])
        self.assertFalse(receipt["productRuntimeImported"])
        self.assertFalse(receipt["runtimeCodingAllowed"])
        self.assertEqual(
            [
                "eva2/design/vertical/noor-4d-schema-export-freeze-v1.json",
                "eva2/design/vertical/noor-4d-connector-provider-fixtures-v1.json",
                "eva2/design/vertical/noor-4d-model-vectors-v1.json",
                "eva2/design/vertical/noor-4d-concurrency-crash-schedules-v1.json",
            ],
            receipt["missingPreRuntimeArtifacts"],
        )

    def test_duplicate_profile_key_fails_before_semantic_checks(self) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        path = root / freeze.PROFILE_PATH
        original = path.read_text(encoding="utf-8")
        needle = '  "contract": "Eva2Noor4DExecutionProfile/v1",\n'
        self.assertEqual(1, original.count(needle))
        path.write_text(original.replace(needle, needle + needle), encoding="utf-8")
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"])
        self.assertTrue(
            any("duplicate JSON key: contract" in error for error in receipt["errors"]),
            receipt["errors"],
        )

    def test_runtime_coding_stays_false_while_required_artifacts_are_missing(
        self,
    ) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        profile = self._load(root, freeze.PROFILE_PATH)
        profile["preRuntimeRequiredArtifacts"]["runtimeCodingAllowed"] = True
        self._write(root, freeze.PROFILE_PATH, profile)
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"])
        self.assertFalse(receipt["runtimeCodingAllowed"])
        self.assertEqual(4, len(receipt["missingPreRuntimeArtifacts"]))

    def test_authority_case_crash_and_document_mutations_fail_closed(self) -> None:
        mutations: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
            (
                "authority",
                lambda profile: profile["authorityBindings"]["verticalContract"].update(
                    {"sha256": "0" * 64}
                ),
                "vertical contract binding is not exact",
            ),
            (
                "case",
                lambda profile: profile["caseOracles"].pop(),
                "case oracle order or set differs",
            ),
            (
                "crash",
                lambda profile: profile["crashOracles"].pop(3),
                "crash oracle points/order differ",
            ),
            (
                "document",
                lambda profile: profile["documentFixture"].update(
                    {
                        "text": profile["documentFixture"]["text"].replace(
                            "15.00", "14.00"
                        )
                    }
                ),
                "document text, length, hash or limit is not exact",
            ),
        ]
        for label, mutate, expected in mutations:
            with self.subTest(label=label):
                self._assert_profile_mutation_fails(mutate, expected)

    def test_pack_policy_and_missing_dependency_mutations_fail_closed(self) -> None:
        def identity(profile: dict[str, Any]) -> None:
            profile["sealedPackFixtures"]["identityPacks"][0]["payload"]["voice_style"][
                "qualities"
            ][0] = "generic"

        def policy(profile: dict[str, Any]) -> None:
            profile["sealedPackFixtures"]["policyPack"]["payload"]["budgets"][
                "maximum_connector_calls"
            ] = 25

        def blocker(profile: dict[str, Any]) -> None:
            profile["preRuntimeRequiredArtifacts"]["artifacts"][1]["status"] = "sealed"

        def model_blocker(profile: dict[str, Any]) -> None:
            profile["preRuntimeRequiredArtifacts"]["artifacts"][2]["expectedSha256"] = (
                "0" * 64
            )

        for label, mutate, expected in (
            ("identity", identity, "content-addressed IdentityPack/v1 is not exact"),
            ("policy", policy, "content-addressed PolicyPack/v1 is not exact"),
            (
                "blocking dependency",
                blocker,
                "pre-runtime artifact is not exact and missing: connector_provider_fixtures",
            ),
            (
                "model blocking dependency",
                model_blocker,
                "pre-runtime artifact is not exact and missing: deterministic_model_vectors",
            ),
        ):
            with self.subTest(label=label):
                self._assert_profile_mutation_fails(mutate, expected)

    def test_sealed_pack_signatures_verify_with_maintained_ed25519(self) -> None:
        profile = self._load(REPOSITORY_ROOT, freeze.PROFILE_PATH)
        packs = profile["sealedPackFixtures"]
        raw_public_key = freeze._decode_unpadded_base64url(  # noqa: SLF001
            packs["fixtureSigningKey"]["rawPublicKeyBase64url"]
        )
        self.assertIsNotNone(raw_public_key)
        verifier = Ed25519PublicKey.from_public_bytes(raw_public_key)

        for row in packs["identityPacks"]:
            payload = dict(row["payload"])
            signature = freeze._decode_unpadded_base64url(  # noqa: SLF001
                payload.pop("signature")
            )
            self.assertIsNotNone(signature)
            verifier.verify(signature, freeze.canonical_json_bytes(payload))

        policy_row = packs["policyPack"]
        policy = dict(policy_row["payload"])
        policy.pop("release_signature_sha256")
        signature = freeze._decode_unpadded_base64url(  # noqa: SLF001
            policy_row["detachedSignatureBase64url"]
        )
        self.assertIsNotNone(signature)
        verifier.verify(signature, freeze.canonical_json_bytes(policy))

    def test_xchacha_known_answers_encrypt_and_decrypt_with_pynacl_1_6_2(self) -> None:
        try:
            import nacl
            from nacl.secret import Aead
        except ImportError:
            self.skipTest("requires the sealed tranche-4d PyNaCl environment")
        self.assertEqual("1.6.2", nacl.__version__)
        profile = self._load(REPOSITORY_ROOT, freeze.PROFILE_PATH)
        encrypted = profile["encryptedObjectFormat"]
        keys = {
            row["identity"]: bytes.fromhex(row["keyMaterial"])
            for row in encrypted["keyBinding"]["fourDFixtureKeyRecords"]
        }
        plaintext = profile["documentFixture"]["text"].encode("utf-8")
        for vector in encrypted["knownAnswerVectors"]["vectors"]:
            aad = vector["canonicalAadJson"].encode("utf-8")
            header = b"EVA2XO01" + struct.pack(">I", len(aad)) + aad
            nonce = bytes.fromhex(vector["nonceHex"])
            ciphertext_and_tag = bytes.fromhex(
                vector["ciphertextHex"] + vector["tagHex"]
            )
            message = Aead(keys[vector["identity"]]).encrypt(
                plaintext,
                aad=header,
                nonce=nonce,
            )
            self.assertEqual(nonce + ciphertext_and_tag, bytes(message))
            self.assertEqual(
                plaintext,
                Aead(keys[vector["identity"]]).decrypt(bytes(message), aad=header),
            )
            self.assertEqual(
                bytes.fromhex(vector["completeEnvelopeHex"]),
                header + bytes(message),
            )

    def test_ack_and_encrypted_object_format_mutations_fail_closed(self) -> None:
        for label, mutate, expected in (
            (
                "ACK format",
                lambda profile: profile["ingressOutcomeAckBinding"].update(
                    {"signatureFormat": "DER"}
                ),
                "ACK-key binding is not exact",
            ),
            (
                "object magic",
                lambda profile: profile["encryptedObjectFormat"].update(
                    {"magicAscii": "EVA2XO02"}
                ),
                "encrypted object primitive, framing or bounds are not exact",
            ),
            (
                "CAS addressing",
                lambda profile: profile["encryptedObjectFormat"][
                    "physicalContentAddressing"
                ].update({"physicalCasKey": "logical object_id"}),
                "physical object storage is not exact envelope-content-addressed CAS",
            ),
            (
                "fixture key algorithm",
                lambda profile: profile["encryptedObjectFormat"]["keyBinding"][
                    "fourDFixtureKeyRecords"
                ][0].update({"algorithm": "AES-256-GCM"}),
                "4D fixture-only key record is not exact for Eva",
            ),
            (
                "AAD wrong installation scope",
                lambda profile: profile["encryptedObjectFormat"]["knownAnswerVectors"][
                    "vectors"
                ][0].update({"installationId": "18597c5d-591a-58bd-9a76-543d24e7e282"}),
                "XCha known-answer vector or AAD/ObjectRef scope is not exact for Eva",
            ),
            (
                "ObjectStore wrong key authority",
                lambda profile: profile["encryptedObjectFormat"][
                    "objectStoreActivationRecords"
                ]["records"][1]["payload"].update(
                    {"key_authority_ref": "fixture:key-authority:noor-object:eva:001"}
                ),
                "ObjectStore/v1 activation record is not exact for Mira",
            ),
        ):
            with self.subTest(label=label):
                self._assert_profile_mutation_fails(mutate, expected)

    def test_vertical_contract_byte_change_fails_immutable_binding(self) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        path = root / freeze.VERTICAL_PATH
        path.write_bytes(path.read_bytes() + b" ")
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"])
        self.assertIn(
            f"immutable input digest mismatch: {freeze.VERTICAL_PATH}",
            receipt["errors"],
        )

    def test_lock_and_pyproject_mutations_fail_dependency_freeze(self) -> None:
        for label, relative, before, after, expected in (
            (
                "patched DBOS wheel hash",
                freeze.LOCK_4D_PATH,
                "e20ca9a038eb2ae195b40484f3fa43c34e3f4c48797cabe6e5e387f01875b464",
                "f20ca9a038eb2ae195b40484f3fa43c34e3f4c48797cabe6e5e387f01875b464",
                "4D lock digest is not exact",
            ),
            (
                "PyNaCl optional dependency",
                freeze.PYPROJECT_PATH,
                '  "PyNaCl==1.6.2",\n',
                "",
                "pyproject digest is not the sealed 4D adoption",
            ),
        ):
            with self.subTest(label=label):
                temporary, root = self._fixture()
                self.addCleanup(temporary.cleanup)
                path = root / relative
                source = path.read_text(encoding="utf-8")
                self.assertEqual(1, source.count(before))
                path.write_text(source.replace(before, after), encoding="utf-8")
                receipt = freeze.verify(root)
                self.assertEqual("failed", receipt["status"], receipt)
                self.assertTrue(
                    any(expected in error for error in receipt["errors"]),
                    receipt["errors"],
                )


if __name__ == "__main__":
    unittest.main()
