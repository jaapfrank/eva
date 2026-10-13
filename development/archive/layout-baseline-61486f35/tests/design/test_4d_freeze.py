from __future__ import annotations

import base64
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


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPOSITORY_ROOT / "tools"
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
                *freeze.ARTIFACT_PATHS,
            }
        )
        paths.add(freeze.CONTRACT_MANIFEST_PATH)
        paths.update(
            str(path.relative_to(REPOSITORY_ROOT))
            for path in (REPOSITORY_ROOT / "schemas/contracts").glob(
                "*.schema.json"
            )
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

    def _assert_coordinated_artifact_mutation_fails(
        self,
        relative: str,
        mutate: Callable[[dict[str, Any]], None],
        expected: str,
    ) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        artifact = self._load(root, relative)
        mutate(artifact)
        self._write(root, relative, artifact)
        profile = self._load(root, freeze.PROFILE_PATH)
        row = next(
            item
            for item in profile["preRuntimeRequiredArtifacts"]["artifacts"]
            if item["path"] == relative
        )
        row["expectedSha256"] = freeze.sha256_file(root / relative)
        self._write(root, freeze.PROFILE_PATH, profile)
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"], receipt)
        self.assertFalse(receipt["runtimeCodingAllowed"])
        self.assertTrue(
            any(expected in error for error in receipt["errors"]), receipt["errors"]
        )

    def test_repository_freeze_passes_and_reports_all_ten_freeze_hashes(self) -> None:
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
                *freeze.ARTIFACT_PATHS,
            },
            set(receipt["verifiedHashes"]),
        )
        self.assertFalse(receipt["networkUsed"])
        self.assertFalse(receipt["databaseUsed"])
        self.assertFalse(receipt["serviceStarted"])
        self.assertFalse(receipt["dependenciesInstalled"])
        self.assertFalse(receipt["productRuntimeImported"])
        self.assertTrue(receipt["runtimeCodingAllowed"])
        self.assertEqual([], receipt["missingPreRuntimeArtifacts"])

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

    def test_runtime_coding_fails_closed_when_a_required_artifact_is_missing(
        self,
    ) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        (root / freeze.SCHEMA_ARTIFACT_PATH).unlink()
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"])
        self.assertFalse(receipt["runtimeCodingAllowed"])
        self.assertEqual(
            [freeze.SCHEMA_ARTIFACT_PATH], receipt["missingPreRuntimeArtifacts"]
        )

    def test_duplicate_artifact_key_fails_before_semantic_checks(self) -> None:
        temporary, root = self._fixture()
        self.addCleanup(temporary.cleanup)
        path = root / freeze.MODEL_ARTIFACT_PATH
        original = path.read_text(encoding="utf-8")
        needle = '"contract":"Eva24DModelVectors/v1",'
        self.assertEqual(1, original.count(needle))
        duplicate = (
            '"contract":"Eva24DModelVectors/v1","contract":"Eva24DModelVectors/v1",'
        )
        path.write_text(original.replace(needle, duplicate), encoding="utf-8")
        receipt = freeze.verify(root)
        self.assertEqual("failed", receipt["status"])
        self.assertTrue(
            any("duplicate JSON key: contract" in error for error in receipt["errors"]),
            receipt["errors"],
        )

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
            profile["preRuntimeRequiredArtifacts"]["artifacts"][1]["status"] = (
                "missing_blocking_dependency_not_implemented"
            )

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
                "pre-runtime artifact binding is not exact: connector_provider_fixtures",
            ),
            (
                "model blocking dependency",
                model_blocker,
                "pre-runtime artifact binding is not exact: deterministic_model_vectors",
            ),
        ):
            with self.subTest(label=label):
                self._assert_profile_mutation_fails(mutate, expected)

    def test_schema_connector_and_model_semantic_mutations_fail_after_rehash(
        self,
    ) -> None:
        def schema(artifact: dict[str, Any]) -> None:
            artifact["schemaFamily"]["canonicalTableCount"] = 38

        def schema_admission(artifact: dict[str, Any]) -> None:
            artifact["migrationFreeze"]["initialAdmissionPrecondition"][
                "admissionDecision"
            ]["admitIffEveryCountEqualsZero"] = False

        def schema_initial_generation_bound(artifact: dict[str, Any]) -> None:
            row = next(
                item
                for item in artifact["portableExport"][
                    "generatedPortableRecordSchemas"
                ]["schemas"]
                if item["table"] == "eva_core.episodes"
            )
            decoded = json.loads(base64.b64decode(row["canonicalBytesBase64"]))
            decoded["properties"]["row"]["properties"]["current_generation"][
                "minimum"
            ] = 2
            raw = freeze.canonical_json_bytes(decoded)
            row["canonicalBytesBase64"] = base64.b64encode(raw).decode("ascii")
            row["canonicalByteLength"] = len(raw)
            row["record_schema_sha256"] = freeze.sha256_bytes(raw)

        def schema_automatic_index_manifest(artifact: dict[str, Any]) -> None:
            artifact["databaseConventions"]["automaticForeignKeyIndexManifest"][
                "manifestCore"
            ]["entries"][0]["selectedName"] = "ix_wrong"

        def schema_role_grant_manifest(artifact: dict[str, Any]) -> None:
            artifact["databaseConventions"]["roleTableColumnGrantManifest"]["entries"][
                0
            ]["updateColumns"].pop()

        def schema_collation_manifest(artifact: dict[str, Any]) -> None:
            artifact["databaseConventions"]["collationManifest"]["entries"][0][
                "physicalAction"
            ] = "alter_preserved_4b"

        def schema_object_store_projection(artifact: dict[str, Any]) -> None:
            installations = next(
                row
                for row in artifact["schemaFamily"]["tables"]
                if row["table"] == "eva_core.installations"
            )
            object_store = next(
                row
                for row in installations["payloadContracts"]
                if row["column"] == "object_store_payload"
            )
            object_store["projectionAuthority"]["objectStoreId"]["sealedEvaUuid"] = (
                "00000000-0000-0000-0000-000000000000"
            )

        def schema_principal_binding_selection(artifact: dict[str, Any]) -> None:
            principal_bindings = next(
                row
                for row in artifact["schemaFamily"]["tables"]
                if row["table"] == "eva_core.principal_bindings"
            )
            principal_bindings["export"]["selectionAuthority"]["operator"] = (
                "direct_reference_only"
            )

        def schema_relationship_reference_validation(
            artifact: dict[str, Any],
        ) -> None:
            relationships = next(
                row
                for row in artifact["schemaFamily"]["tables"]
                if row["table"] == "eva_core.relationships"
            )
            relationships["referenceValidations"][0] = (
                "resolve channel binding evidence without installation validation"
            )

        def connector(artifact: dict[str, Any]) -> None:
            artifact["scenarios"]["c13ReofferEva"]["expected"][
                "outcomeProviderMutations"
            ] = 2

        def connector_ingress(artifact: dict[str, Any]) -> None:
            artifact["hostEvidence"]["outcomeAckVectors"]["evaSuccess"]["ingress"][
                "payload"
            ]["turn_envelope_sha256"] = "0" * 64

        def connector_c10(artifact: dict[str, Any]) -> None:
            artifact["hostEvidence"]["c10ReadbackRepeatAuthority"][
                "preCrashAttemptReference"
            ]["responseBytesDurablyAdoptable"] = True

        def connector_binding_set_order(artifact: dict[str, Any]) -> None:
            operations = artifact["concreteBindings"][0]["payload"][
                "confirmed_operations"
            ]
            self.assertEqual(2, len(operations))
            operations.reverse()

        def model(artifact: dict[str, Any]) -> None:
            artifact["fixtureAdapterProfile"]["programSha256"] = "0" * 64

        def model_connector_crosswalk(artifact: dict[str, Any]) -> None:
            artifact["happyPathVectors"][1]["request"]["model_input"][
                "presence_context"
            ]["effect_receipts"][0]["receipt_id"] = "fixture:receipt:wrong"

        for label, relative, mutate, expected in (
            (
                "schema",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema,
                "does not freeze exactly 39 canonical tables",
            ),
            (
                "schema empty-only admission",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_admission,
                "schema 4d_0003 admission is not exact empty-only authority",
            ),
            (
                "schema initial-generation primitive bounds",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_initial_generation_bound,
                "portable episode primitive bounds reject an admitted initial generation",
            ),
            (
                "schema automatic FK index manifest",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_automatic_index_manifest,
                "automatic foreign-key index authority is not exact or PostgreSQL-safe",
            ),
            (
                "schema role grant manifest",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_role_grant_manifest,
                "schema role/table/column grant authority is not exact",
            ),
            (
                "schema collation manifest",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_collation_manifest,
                "schema explicit-C collation authority is not exact",
            ),
            (
                "schema ObjectStore projection",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_object_store_projection,
                "schema ObjectStore scalar/payload projection authority is not exact",
            ),
            (
                "schema principal-binding selection",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_principal_binding_selection,
                "schema principal-binding export selection authority is not exact",
            ),
            (
                "schema relationship reference validation",
                freeze.SCHEMA_ARTIFACT_PATH,
                schema_relationship_reference_validation,
                "schema relationship reference-validation authority is not exact",
            ),
            (
                "connector",
                freeze.CONNECTOR_ARTIFACT_PATH,
                connector,
                "connector C13 exact re-offer surface is not exact for Eva",
            ),
            (
                "connector ingress",
                freeze.CONNECTOR_ARTIFACT_PATH,
                connector_ingress,
                "connector ACK vector evaSuccess ingress-to-TurnEnvelope mapping is not exact",
            ),
            (
                "connector C10 repeat read",
                freeze.CONNECTOR_ARTIFACT_PATH,
                connector_c10,
                "connector C10 durable ordinal-2 read authority is not exact",
            ),
            (
                "connector binding set order",
                freeze.CONNECTOR_ARTIFACT_PATH,
                connector_binding_set_order,
                "connector concrete binding and verification set arrays are not exact "
                "UTF-8-canonical mirrors",
            ),
            (
                "model",
                freeze.MODEL_ARTIFACT_PATH,
                model,
                "model manifest/program/schema cross-binding is not exact",
            ),
            (
                "model connector crosswalk",
                freeze.MODEL_ARTIFACT_PATH,
                model_connector_crosswalk,
                "model document PresenceContext differs from connector durable authority",
            ),
        ):
            with self.subTest(label=label):
                self._assert_coordinated_artifact_mutation_fails(
                    relative, mutate, expected
                )

    def test_schedule_semantic_mutations_fail_after_coordinated_rehash(self) -> None:
        def runtime_inference(artifact: dict[str, Any]) -> None:
            artifact["portableSnapshotAuthority"]["runtimeInferenceAllowed"] = True

        def record_set(artifact: dict[str, Any]) -> None:
            snapshot = next(
                row
                for row in artifact["portableSnapshotAuthority"]["precomputedSnapshots"]
                if row["snapshot_id"] == "terminal"
            )
            record_set_row = next(
                row
                for row in snapshot["record_sets"]
                if row["table"] == "eva_core.episodes"
            )
            record_set_row["record_fact_ids"] = []

        def c13_reference(artifact: dict[str, Any]) -> None:
            schedule = next(
                row
                for row in artifact["crashSchedules"]
                if row["crashId"] == "C13-after-outcome-accept-before-receipt"
            )
            schedule["reofferAuthority"]["ordinal_2_effect_attempt_fact_id"] = (
                "effect-attempt/outcome:missing"
            )

        def correction_object_manifest(artifact: dict[str, Any]) -> None:
            snapshot = next(
                row
                for row in artifact["portableSnapshotAuthority"]["precomputedSnapshots"]
                if row["snapshot_id"] == "terminal.correction"
            )
            snapshot["object_manifest_records"][0]["object_ref_row_sha256"] = "0" * 64

        def harness_nonce_reuse(artifact: dict[str, Any]) -> None:
            artifact["harnessEntropyInjectionAuthority"]["nonce_injection"][
                "maximum_consumptions_per_isolated_scenario_run"
            ] = 2

        def harness_trace_not_durable(artifact: dict[str, Any]) -> None:
            artifact["harnessEntropyInjectionAuthority"][
                "durable_consumption_trace_gate"
            ]["missing_duplicate_or_unflushed_trace_action"] = "continue"

        def harness_production_import(artifact: dict[str, Any]) -> None:
            artifact["harnessEntropyInjectionAuthority"][
                "forbidden_activation_and_shortcuts"
            ]["production_import_allowed"] = True

        def harness_path_manifest(artifact: dict[str, Any]) -> None:
            artifact["harnessEntropyInjectionAuthority"]["schedule_path_bindings"][
                "selected_ciphertext_occurrence_paths"
            ].pop()

        def logical_relational_uuid_crosswalk(artifact: dict[str, Any]) -> None:
            artifact["portableSnapshotAuthority"]["fixtureFactCatalog"][
                "logical_relational_id_crosswalk"
            ][0]["portable_uuid"] = "00000000-0000-0000-0000-000000000000"

        def revision_cas_not_incremented(artifact: dict[str, Any]) -> None:
            fact = next(
                row
                for row in artifact["portableSnapshotAuthority"]["fixtureFactCatalog"][
                    "facts"
                ]
                if row["fact_id"] == "model-attempt/plan-responded"
            )
            self.assertEqual(3, fact["portable_record"]["row"]["revision"])
            fact["portable_record"]["row"]["revision"] = 2

        def successful_observed_read_not_revision_two(
            artifact: dict[str, Any],
        ) -> None:
            fact = next(
                row
                for row in artifact["portableSnapshotAuthority"]["fixtureFactCatalog"][
                    "facts"
                ]
                if row["fact_id"] == "read-attempt/mail-search"
            )
            self.assertEqual(2, fact["portable_record"]["row"]["revision"])
            fact["portable_record"]["row"]["revision"] = 1

        for label, mutate, expected in (
            (
                "runtime inference",
                runtime_inference,
                "no-runtime-inference seal",
            ),
            (
                "record set",
                record_set,
                "31 snapshot deltas",
            ),
            (
                "C13 fact reference",
                c13_reference,
                "fact or snapshot references",
            ),
            (
                "correction object manifest",
                correction_object_manifest,
                "31 snapshot deltas",
            ),
            (
                "harness nonce reuse",
                harness_nonce_reuse,
                "one-use or harness-only",
            ),
            (
                "harness trace not durable",
                harness_trace_not_durable,
                "durable trace, uniqueness or ordering gate",
            ),
            (
                "harness production import",
                harness_production_import,
                "production boundary or no-shortcut rule",
            ),
            (
                "harness path manifest",
                harness_path_manifest,
                "exhaustive path bindings",
            ),
            (
                "logical-relational UUID crosswalk",
                logical_relational_uuid_crosswalk,
                "390-fact catalog, wrappers or portable schema validation",
            ),
            (
                "non-incrementing revision CAS",
                revision_cas_not_incremented,
                "exact ordered +1 revisionCas chains",
            ),
            (
                "successful observed read without dispatch fence",
                successful_observed_read_not_revision_two,
                "seven happy successful read attempts are not exact revision-2 "
                "observed fences",
            ),
        ):
            with self.subTest(label=label):
                self._assert_coordinated_artifact_mutation_fails(
                    freeze.SCHEDULE_ARTIFACT_PATH, mutate, expected
                )

    def test_profile_harness_entropy_exception_mutations_fail_closed(self) -> None:
        def authority(profile: dict[str, Any]) -> None:
            profile["preRuntimeRequiredArtifacts"]["harnessEntropyInjectionException"][
                "scheduleBinding"
            ]["authoritySha256"] = "0" * 64

        def more_than_one_use(profile: dict[str, Any]) -> None:
            profile["preRuntimeRequiredArtifacts"]["harnessEntropyInjectionException"][
                "testBootstrap"
            ]["maximumConsumptionsPerIsolatedScenarioRun"] = 2

        def production_import(profile: dict[str, Any]) -> None:
            profile["preRuntimeRequiredArtifacts"]["harnessEntropyInjectionException"][
                "normalAndProductionBoundary"
            ]["productionBootstrapMayImportOrConfigureSource"] = True

        for label, mutate in (
            ("authority hash", authority),
            ("second consumption", more_than_one_use),
            ("production import", production_import),
        ):
            with self.subTest(label=label):
                self._assert_profile_mutation_fails(
                    mutate,
                    "profile harness entropy exception is not the sole exact adoption authority",
                )

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
                "CAS ordered install step",
                lambda profile: profile["encryptedObjectFormat"][
                    "atomicCreateAndReadback"
                ]["orderedSteps"].__setitem__(
                    3,
                    "derive a truncated path and overwrite it",
                ),
                "object create/readback ordered CAS steps are not exact",
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
