#!/usr/bin/env python3
"""Fail-closed, stdlib-only checker for the Eva 2 tranche 4D pre-code freeze.

The checker performs no import of Eva runtime code or third-party package, and
does no network, database, service, dependency resolution, build or install.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import struct
import sys
import tomllib
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence


PROFILE_PATH = "design/vertical/noor-4d-execution-profile-v1.json"
BINDING_PATH = "release/4d-dependency-source-binding.json"
LOCK_4D_PATH = "requirements-4d.lock"
PYPROJECT_PATH = "pyproject.toml"
CHECKER_PATH = "tools/check_4d_freeze.py"
TEST_PATH = "tests/design/test_4d_freeze.py"
CONTRACT_MANIFEST_PATH = "schemas/contracts/manifest.json"
SCHEMA_ARTIFACT_PATH = "design/vertical/noor-4d-schema-export-freeze-v1.json"
CONNECTOR_ARTIFACT_PATH = (
    "design/vertical/noor-4d-connector-provider-fixtures-v1.json"
)
MODEL_ARTIFACT_PATH = "design/vertical/noor-4d-model-vectors-v1.json"
SCHEDULE_ARTIFACT_PATH = (
    "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
ARTIFACT_PATHS = (
    SCHEMA_ARTIFACT_PATH,
    CONNECTOR_ARTIFACT_PATH,
    MODEL_ARTIFACT_PATH,
    SCHEDULE_ARTIFACT_PATH,
)
EXPECTED_PROFILE_SEMANTIC_CORE_SHA256 = (
    "a2d83cc5846a667834081b667b17a50fa7c1552dd367649d6d03b30d2fdcd020"
)
EXPECTED_ARTIFACT_SHA256 = {
    SCHEMA_ARTIFACT_PATH: (
        "f70eb25013ff84e464e4ef2a448b2e63fe23a8c11d91d4d57a54d3e431dc9d38"
    ),
    CONNECTOR_ARTIFACT_PATH: (
        "7655b181889d95f1140aea114be4934724931814898b4cee134151f57c2009c6"
    ),
    MODEL_ARTIFACT_PATH: (
        "e0a9fca6aed7f5146dd0255ba9ae4cdee4fa15dc274398e535cab68c8e28f593"
    ),
    SCHEDULE_ARTIFACT_PATH: (
        "f492d746779ec5276478893ff22720c84926912371d19175dd7508a18734b91d"
    ),
}
EXPECTED_ARTIFACT_CONTRACTS = {
    SCHEMA_ARTIFACT_PATH: "Eva24DSchemaExportFreeze/v1",
    CONNECTOR_ARTIFACT_PATH: "Eva24DConnectorProviderFixtures/v1",
    MODEL_ARTIFACT_PATH: "Eva24DModelVectors/v1",
    SCHEDULE_ARTIFACT_PATH: "Eva24DConcurrencyCrashSchedules/v1",
}
PRE_RUNTIME_GATE_RULE = (
    "runtimeCodingAllowed is true if and only if all four listed artifacts have "
    "status sealed_verified, their exact non-null expectedSha256, an exact "
    "contract and byte hash, the unchanged source-profile semantic core and "
    "all fail-closed internal and cross-artifact validations passing, and the "
    "current profile binds the sole exact isolated-4D harness entropy exception; "
    "any failure resets it to false"
)
EXPECTED_HARNESS_ENTROPY_AUTHORITY_SHA256 = (
    "38c4bb987abe5652cc223e4dacb4fe0b440495127bec62174cca7978d844f310"
)
EXPECTED_HARNESS_ENTROPY_BINDING_MANIFEST_SHA256 = (
    "2302e0400910a2888de9417742dd08c72398b0b65db1cd96f4abaf49e7e93359"
)
EXPECTED_HARNESS_NONCE_HEX = "000102030405060708090a0b0c0d0e0f1011121314151617"
EXPECTED_HARNESS_NONCE_SHA256 = (
    "1d64add2a6388367c9bc2d1f1b384b069a6ef382cdaaa89771dd103e28613a25"
)
EXPECTED_EVA_FIXTURE_KEY_RECORD_SHA256 = (
    "81179b3b4bfa235ad5d2f94d2b285a2f7cd39f37c30516e4e19a42f298d565d9"
)
EXPECTED_EVA_KAT_VECTOR_SHA256 = (
    "cb0f3531275c241bd06d9e3b19d70e7e43a4a59e2bce0d8d0e509aa56ee9d5a3"
)
EXPECTED_SCHEMA_PORTABLE_MANIFEST_SHA256 = (
    "de2e302c8d260707563a8c16af1a6dfc2a3932507013dddb79d7780a3e36dc68"
)
EXPECTED_SCHEMA_OBJECT_MANIFEST_RECORD_SHA256 = (
    "1e951976fa1021893bb5bc3812a98997560b7fcf636d0e69573e4444ec4930a8"
)
EXPECTED_SCHEMA_READER_MANIFEST_SHA256 = (
    "5b26d3b51a556b4b214b4d2d1cd664fb5532064d5eb0c7c7db70e833c320e876"
)
EXPECTED_AUTOMATIC_FK_INDEX_MANIFEST_SHA256 = (
    "72c91fe2d7a1ebf24d193e5a1b5ddb2b1b90ad42f67b8198e3c87b6a876c4ad4"
)
EXPECTED_ROLE_GRANT_MANIFEST_SHA256 = (
    "5bfff57653ccf0ff48915bc95cec367a02798a282ead7c35f6fbd6d7ab299cb6"
)
EXPECTED_COLLATION_MANIFEST_SHA256 = (
    "ac88723a502956223d2e14b42b35b0b709123586871118687784ff4b5ce43ac7"
)
EXPECTED_OBJECT_STORE_NAMESPACE_UUID = "276dbeb6-d012-5519-9ba9-4cb279c94dd4"
EXPECTED_SNAPSHOT_FAMILIES = [
    "empty",
    "admitted",
    "planReserved",
    "planCommitted",
    "calendarPrepared",
    "calendarReceipt",
    "calendarReadInFlight",
    "calendarProven",
    "documentResponseInFlight",
    "planAmbiguousWaiting",
    "replyResponseInFlight",
    "confirmationInFlight",
    "confirmationProven",
    "outcomeInFlight",
    "outcomeAcknowledged",
    "terminal",
    "partialDocumentTerminal",
    "fallbackTerminal",
]
EXPECTED_SNAPSHOT_INSTANCES = [
    *EXPECTED_SNAPSHOT_FAMILIES,
    "planReserved.provider429Open",
    "terminal.provider429",
    "calendarPrepared.connector429Open",
    "terminal.connector429",
    "calendarPrepared.c07AmbiguousBeforeCommit",
    "terminal.c07",
    "calendarPrepared.c08AmbiguousAfterCommit",
    "terminal.c08",
    "terminal.c10",
    "terminal.c11",
    "terminal.c13",
    "planCommitted.correctionGen1",
    "terminal.correction",
]


def _expected_profile_harness_entropy_exception() -> dict[str, Any]:
    return {
        "contract": "Eva4DProfileHarnessEntropyException/v1",
        "status": "sealed_profile_authority",
        "authorityPrecedence": (
            "This current-profile adoption authority is the sole isolated-4D "
            "exception to encryptedObjectFormat.encryptionProcedure and nonceRule; "
            "the schedule artifact cannot grant, override or widen this exception."
        ),
        "scheduleBinding": {
            "path": SCHEDULE_ARTIFACT_PATH,
            "wholeFileSha256": EXPECTED_ARTIFACT_SHA256[SCHEDULE_ARTIFACT_PATH],
            "authoritySelector": "harnessEntropyInjectionAuthority",
            "authoritySha256": EXPECTED_HARNESS_ENTROPY_AUTHORITY_SHA256,
            "bindingManifestSha256": (EXPECTED_HARNESS_ENTROPY_BINDING_MANIFEST_SHA256),
        },
        "testBootstrap": {
            "onlyAllowedBootstrap": "isolated_noor_4d_schedule_harness",
            "mayInjectExactOneUseNonce": True,
            "injectionInterface": (
                "one-shot in-memory callback passed explicitly to the object-"
                "encryption fixture adapter nonce argument"
            ),
            "identity": "Eva",
            "publicFixtureKeyRecordCanonicalSha256": (
                EXPECTED_EVA_FIXTURE_KEY_RECORD_SHA256
            ),
            "knownAnswerVectorCanonicalSha256": EXPECTED_EVA_KAT_VECTOR_SHA256,
            "objectId": "8fa3f97c-dc66-52f2-840c-d441b55f6a60",
            "nonceHex": EXPECTED_HARNESS_NONCE_HEX,
            "nonceByteLength": 24,
            "nonceSha256": EXPECTED_HARNESS_NONCE_SHA256,
            "maximumConsumptionsPerIsolatedScenarioRun": 1,
            "consumptionOrdinal": 1,
            "durableConsumptionTraceRequiredBeforeCandidateAdoption": True,
        },
        "normalAndProductionBoundary": {
            "defaultBootstrapMayImportOrRegisterSource": False,
            "productionBootstrapMayImportOrConfigureSource": False,
            "environmentFileOrServiceActivationAllowed": False,
            "normalEncryptCall": (
                "nacl.secret.Aead(key).encrypt(plaintext, aad=header) without a "
                "nonce so PyNaCl/libsodium supplies fresh random bytes"
            ),
            "deterministicProductionNonceAllowed": False,
            "productionEntropyOrEncryptionProven": False,
        },
        "forbiddenShortcuts": {
            "preseedCasEnvelopeOrObjectRefAllowed": False,
            "copyKnownAnswerEnvelopeAllowed": False,
            "foundSameWithoutThisRunDurableConsumptionTraceAllowed": False,
        },
    }


VERTICAL_PATH = (
    "design/evidence/eva2-first-vertical-contract-"
    "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json"
)

EXPECTED_IMMUTABLE_FILES = {
    VERTICAL_PATH: "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1",
    "backup/history/root/design/fixtures/noor-client-ack-key-v1.json": "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93",
    "backup/history/root/design/evidence/eva2-step3-design-freeze-cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33.json": "cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33",
    "backup/history/root/docs/adr-0001-eva2-living-core.md": "73b53e392b8e3b65d1241bff46bb92144960a2620dbd45f3c9d3526d9f07910d",
    "backup/history/root/docs/eva2-build-map-core-contracts.md": "877a0d25b637ef0f3bbfe3286ab40b5c40d7a0e50dbc3bf4af14504426bf0d63",
    "backup/history/root/docs/eva2-build-map-platform.md": "244b2f2e397d4ce11e245d573b266dee443f77b85d0bd456d7e4b9cd66fa98f1",
    "backup/history/root/docs/eva2-build-map-presence-evidence.md": "69d391ba4e3b3363c4d1d3cb6471c1510b68e974f5d8e61088356b6020328ed8",
    "requirements.lock": "89555d14acd8bceb8786dfa8539af29672899e8259e6a2b5352e27ef9219e8dc",
    "release/dependency-roots.in": "f6645e2710a3263c1d989caac095783abc30d9fe2bfc6a6fc85446b2a21334f4",
    "release/python-wheel-coverage.json": "9edd29074bd3967d2a3f1b7865f2d1f851c79b16169e3a03f3904fa3d9a0b64e",
    "release/license-inventory.json": "57bf2654176f170e75e91079faaea44e414ae1a7e380be413ae125ed76b94f86",
    "evidence/4a/4a-index-sha256-b7870f121c8da63b39d577bfbc9c8f3b91150d3ccef6a9484440d9d50ebfcc6f.json": "b7870f121c8da63b39d577bfbc9c8f3b91150d3ccef6a9484440d9d50ebfcc6f",
    "evidence/4b/postgresql-core-gate.json": "67952e3cba0729b4026078957913472668fd5842aae58194d6beac16ef837b76",
    "evidence/4c/postgresql-durability-gate.json": "8e89ca955a556f66316cc0480c0966f1bcaa217ee70f45eb532e7035be86f156",
    "vendor/dbos-2.30.0-eva1/manifest.json": "17f2089bd2c416a858c30109812f6c236ba32bf0c8e30ba9926e3ea009f2f939",
    "vendor/dbos-2.30.0-eva1/0001-bounded-sync-datasource.patch": "85c43d878eb5264fa344e8ea65471013251f26483ab0dcc39944185f061d77dc",
    "tools/build_dbos_4c_wheel.py": "284b0f8ca88691eb9a2fb4841608bbfa88b9d6d5afbf56a0db91a5cc91b8ed49",
}

EXPECTED_PYPROJECT_SHA256 = (
    "bf552110352035faf1533c23c33915e5ab85445de7e156c044ee4f8356310789"
)
EXPECTED_4D_LOCK_SHA256 = (
    "b05efcbc41cf8dce64177c82e15430c510ef4f12e30242a4e5ea75d803c1f708"
)
EXPECTED_DOCUMENT_TEXT = (
    "Bevestiging afspraak Noor BV\n\n"
    "De afspraak met Noor BV staat gepland op woensdag 26 augustus 2026 "
    "van 15.00 tot 15.30 uur.\n"
)
EXPECTED_DOCUMENT_SHA256 = (
    "c40d2ea8bb612e2fe7843e2585c7ea122a39227ec6848f847b3e09a3002dfe68"
)
EXPECTED_VISIBLE_TEXTS = {
    "evaSuccess": (
        "Geregeld. De afspraak met Noor BV staat op woensdag 26 augustus 2026 "
        "van 15.00 tot 15.30 uur in de agenda. Ik heb de bevestiging gemaakt en "
        "verstuurd naar planning@noorbv.example."
    ),
    "miraSuccess": (
        "Afgerond. De afspraak met Noor BV staat op woensdag 26 augustus 2026 "
        "van 15.00 tot 15.30 uur. De bevestiging is gemaakt en verstuurd naar "
        "planning@noorbv.example."
    ),
    "requestConflict": (
        "Ik kan dit niet als hetzelfde verzoek behandelen, omdat de inhoud is "
        "veranderd. Stuur het opnieuw als een nieuw verzoek."
    ),
    "documentRejected": (
        "De afspraak met Noor BV staat op woensdag 26 augustus 2026 van 15.00 "
        "tot 15.30 uur in de agenda. De bevestiging kon ik niet maken, dus ik "
        "heb niets naar Noor verstuurd."
    ),
    "confirmationRejected": (
        "De afspraak met Noor BV staat op woensdag 26 augustus 2026 van 15.00 "
        "tot 15.30 uur in de agenda. De bevestiging is gemaakt, maar niet "
        "verstuurd naar planning@noorbv.example."
    ),
    "erpQuestion": (
        "Ik vind twee klanten die Noor BV heten: Noor BV in Utrecht en Noor BV "
        "in Groningen. Welke bedoel je?"
    ),
}
EXPECTED_CALLS = [
    ("mail.message.search", "business_read", "fixture:binding:mail:001"),
    ("mail.message.read", "business_read", "fixture:binding:mail:001"),
    ("erp.customer.lookup", "business_read", "fixture:binding:erp:001"),
    (
        "calendar.availability.read",
        "business_read",
        "fixture:binding:calendar:001",
    ),
    ("calendar.event.create", "effect", "fixture:binding:calendar:001"),
    ("effect.readback", "provider_readback", "fixture:binding:calendar:001"),
    ("document.create", "effect", "fixture:binding:document:001"),
    ("effect.readback", "provider_readback", "fixture:binding:document:001"),
    (
        "message.deliver",
        "effect",
        "fixture:binding:confirmation-delivery:001",
    ),
    (
        "effect.readback",
        "provider_readback",
        "fixture:binding:confirmation-delivery:001",
    ),
    (
        "channel.result.deliver",
        "effect",
        "fixture:binding:outcome-delivery:001",
    ),
]
EXPECTED_4D_CASES = {
    "duplicate-overlap",
    "request-hash-conflict",
    "correction-before-first-effect",
    "ambiguous-calendar-commit",
    "provider-rate-limit",
    "connector-rate-limit",
    "document-definitive-reject",
    "confirmation-delivery-definitive-reject",
    "outcome-delivery-definitive-reject",
    "erp-ambiguous-match",
    "poisoned-mail",
    "identity-pack-swap",
}
EXPECTED_4E_CASES = {
    "framework-independent-restore",
    "update-with-waiting-episode",
}
EXPECTED_UUID_VECTOR_IDS = {
    "fixture:installation:001",
    "fixture:installation:mira:001",
    "fixture:tenant:owner:001",
    "fixture:principal:owner:001",
    "fixture:conversation:noor:001",
    "fixture:http-request:noor:001",
    "fixture:key:noor-client-ack:001",
    "mail:noor:017",
    "erp:customer:204",
    "erp:customer:319",
    "fixture:http-request:noor-correction:001",
    "fixture:http-request:noor-erp-ambiguity:001",
    "fixture:object:noor-confirmation:001",
    "fixture:object:noor-confirmation:mira:001",
    "fixture:keyref:noor-object:001",
    "fixture:keyref:noor-object:mira:001",
    "fixture:binding:mail:001",
    "fixture:binding:erp:001",
    "fixture:binding:calendar:001",
    "fixture:binding:document:001",
    "fixture:binding:confirmation-delivery:001",
    "fixture:binding:outcome-delivery:001",
    "fixture:worker:mail:001",
    "fixture:worker:erp:001",
    "fixture:worker:calendar:001",
    "fixture:worker:document:001",
    "fixture:worker:channel:001",
}
EXPECTED_IDENTITY_PACKS = {
    "Eva": {
        "fixtureId": "fixture:identity-pack:eva:001",
        "identityId": "identity:eva:fixture",
        "canonicalSha256": "025984c019714d7107803258959fcbddd94d0700acaef15beeac26f05a645798",
        "sourceSha256": "fcd5142bd8e7f7a28c50b49284ef684e77f86e56264a3c18e5026db95afe04bd",
        "signingInputSha256": "b27d336017fe0918fcb1325cb1679e299349e7082b61bfad252c6ce11b2d704a",
        "signature": "dhyqMea4a-mUmKyNOFvlmtfpEnl9lZWK3S-8GfV3VXx51UZIvyuaLzqEYgGgKG-zdb1N6U2QOA5nBgh4IjQ_BA",
        "voiceQualities": [
            "warm",
            "practical",
            "curious",
            "concise",
            "slightly_stubborn",
        ],
    },
    "Mira": {
        "fixtureId": "fixture:identity-pack:mira:001",
        "identityId": "identity:mira:fixture",
        "canonicalSha256": "b01cf6ca59f0b30291a1532195acd67d9cff47f58b05cd687d01544413d54168",
        "sourceSha256": "38c18628a57e188839825b88a19ea15e3e6ae0bcb6d3c79538094f2b2c715211",
        "signingInputSha256": "fa079f8d4e0ecf22e73160dfb22f7dfd5e5f9c99b24f33cccb578764b7a7570e",
        "signature": "dke28Srj_iXTiYhJxJTGbBu6DYee6GbjNz5ErSjzFz2p4My953G0vHMW12jbIcuN9QeXm4PR1qWYS1snDMZrAw",
        "voiceQualities": [
            "calm",
            "observant",
            "precise",
            "concise",
            "gently_dry",
        ],
    },
}
EXPECTED_POLICY_PACK_SHA256 = (
    "858d19fa815fb15b6f78cdd755081264dbfeea9af01f4e488ece73fe0d381bcb"
)
EXPECTED_POLICY_SOURCE_SHA256 = (
    "0babef904ae9b623e5211fd4b75bc7e17825b63e2a982d9ed5619fce4212e2d3"
)
EXPECTED_POLICY_SIGNING_INPUT_SHA256 = (
    "5636294774e848829965e05d8cc5def08e2177b136510c2ada52b794427b4ed5"
)
EXPECTED_POLICY_SIGNATURE = "CKQx52fPuXCFyS-FJNmhAd8M7yfSwxyCmXcX9jG9_KMbDS7hZcRtzXkFOPPGX4mcwcPs7p1Whyz9ZF34IQwGAQ"
EXPECTED_POLICY_SIGNATURE_SHA256 = (
    "38e4f7019193c45ffdbdc1e15f92c0d4be8e14962a87f1b24a25f2ab4eb89677"
)
EXPECTED_XCHACHA_KATS = {
    "Eva": {
        "installationId": "0a44a85c-4bbb-5330-8803-60fab86e5a94",
        "tenantId": "66a4aad5-0550-5211-8b36-398eabe5b7be",
        "objectId": "8fa3f97c-dc66-52f2-840c-d441b55f6a60",
        "keyRef": "6cb79e1a-220a-5780-b544-e2b4dcf1f19e",
        "keyAuthorityRef": "fixture:key-authority:noor-object:eva:001",
        "keyMaterial": "bdc1d386d3f3b313d2478bc3e8a86640fbf3411f489c473b5d61ecbc97b40060",
        "keySha256": "3a4ddf9c103f569ad4fd37b8e668aa8319f640664c69d48d7e29f249f31e2183",
        "nonceHex": "000102030405060708090a0b0c0d0e0f1011121314151617",
        "aadSha256": "9ce11387f626c40bff7932ce4e023dbf8fa602d1352102a91ebe86e216f9ee01",
        "headerSha256": "4f363f8e6dbe4a8ca0e8d6f3d5e563a32bc24b361a48ad6ed0fb38f70af0ebe3",
        "ciphertextHex": "d719d5188a6e58717324e210bd0b1023474d7189b85261272c3378f29a1c8020e8956a83241e20bc9689104f24884687cce4d83dc75a2f1e6b6bc1792db5aa96bb74c36498cf90109939209b6f0548061c2539857ce2ba44d01d9f5691fa04170bd0e8f08ac87f8acf7e6262dd4c865e08a64144133dda322695",
        "tagHex": "583ffb4b94984bcf17f109b4c80ac394",
        "ciphertextAndTagSha256": "d474a98564654334a2f074d8de8bb920a2e88f4adc90d861556ade30aab33ca2",
        "envelopeSha256": "49e346abbafcd71855b96f72418b844624426aca5c3540f697098a189495731c",
        "objectRefSha256": "8cd60e7fe295a2fdc1c7be48ecc84f2a41d8e7083880b53596105cc9fc7d202e",
        "objectStoreSha256": "5d510f169c16c2e9efafd39962dab01e9a79bc7a69094af7499431b5a38fc9fb",
    },
    "Mira": {
        "installationId": "18597c5d-591a-58bd-9a76-543d24e7e282",
        "tenantId": "66a4aad5-0550-5211-8b36-398eabe5b7be",
        "objectId": "43534156-7b9a-584d-9b58-fab145a48a17",
        "keyRef": "ce02a8ab-9585-530d-bd6c-ff85f9791e2d",
        "keyAuthorityRef": "fixture:key-authority:noor-object:mira:001",
        "keyMaterial": "85aa18769017e71032f403e0c2e884f415784bca63a926c9d05de90e877f222e",
        "keySha256": "b742f85e3f9cac14dae148aa580537d87fd86d5ab463cb0a46ef58e2b76907bd",
        "nonceHex": "18191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f",
        "aadSha256": "0857afb74f25ebc6b646890f307fa2c3e87a6c0eec8243b8ee7cdbffa9026f93",
        "headerSha256": "803641829437bbbe0828b98d85c5c28270caac1fdc7312fc207f63eb68e0a092",
        "ciphertextHex": "78132f353c90a03e154827170e8cec3515269f34fc139288d349290eed3753f08b1f57840f87f4bcbcf180e0146ebc246a230fe71936f2a8d09d9ca36b3af822f00f299b581d6b17831d7d0ffa31273b210ef49e3c67a0d721367985dce60c27def956c7a47bdb32ce52a1f8c44e66b373c53d6e013030bc55f4",
        "tagHex": "a30168b9fd50c885b3629b3618791d13",
        "ciphertextAndTagSha256": "4b57d2316385f9be1ea0a4839ffb16b64801bb05322a7f1fde6a652a9b10c65f",
        "envelopeSha256": "2a3b5863756bf2090ecf341693bff56037ea0b8ab80938ebae794fb5a7b76ead",
        "objectRefSha256": "d73e8a58929335d746107d90ca97e3f24ae07ebfcd049cfd8286e3479bdde1e4",
        "objectStoreSha256": "368249100c039e0906e52ca5b54f690a1f7fa4ea65c71641efc64b452fdea717",
    },
}
EXPECTED_PYNACL_WHEELS = {
    "linux_amd64": (
        "pynacl-1.6.2-cp38-abi3-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl",
        "8a66d6fb6ae7661c58995f9c6435bda2b1e68b54b598a6a10247bfcdadac996c",
    ),
    "linux_arm64": (
        "pynacl-1.6.2-cp38-abi3-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl",
        "46065496ab748469cdd999246d17e301b2c24ae2fdf739132e580a0e94c94a87",
    ),
    "windows_x86_64": (
        "pynacl-1.6.2-cp38-abi3-win_amd64.whl",
        "62985f233210dee6548c223301b6c25440852e13d59a8b81490203c3227c5ba0",
    ),
    "macos_universal2": (
        "pynacl-1.6.2-cp38-abi3-macosx_10_10_universal2.whl",
        "c949ea47e4206af7c8f604b8278093b674f7c79ed0d4719cc836902bf4517465",
    ),
}
EXPECTED_TARGET_MANIFESTS = {
    "amd64": "0e877383ffdef9d148a8d60729c91c2461685a31f001ee19787cfd96a4e56930",
    "arm64": "5241b78f1acd86aa16a52c077f0ec553ee13eaa4cda6f4f131295dffd16b5a29",
}

LOCK_HEADER = """#
# Standalone Eva 2 tranche 4D dependency lock for CPython 3.12.14.
# It is derived from the immutable 4A baseline requirements.lock by replacing
# stock DBOS 2.30.0 with the bound Eva wheel 2.30.0+eva.1 and adding
# PyNaCl 1.6.2. It contains no include, index, URL, sdist or fallback directive.
# Consume only with the no-index, only-binary, require-hashes wheelhouse
# procedure sealed in release/4d-dependency-source-binding.json.
#
"""
DBOS_4D_BLOCK = """dbos==2.30.0+eva.1 \\
    --hash=sha256:e20ca9a038eb2ae195b40484f3fa43c34e3f4c48797cabe6e5e387f01875b464
    # via eva2-living-core[tranche-4d]
"""
PYNACL_BLOCK = """pynacl==1.6.2 \\
    --hash=sha256:46065496ab748469cdd999246d17e301b2c24ae2fdf739132e580a0e94c94a87 \\
    --hash=sha256:62985f233210dee6548c223301b6c25440852e13d59a8b81490203c3227c5ba0 \\
    --hash=sha256:8a66d6fb6ae7661c58995f9c6435bda2b1e68b54b598a6a10247bfcdadac996c \\
    --hash=sha256:c949ea47e4206af7c8f604b8278093b674f7c79ed0d4719cc836902bf4517465
    # via eva2-living-core[tranche-4d]
"""


class FreezeError(ValueError):
    """A deterministic input error that must fail the freeze closed."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any, *, final_lf: bool = False) -> bytes:
    result = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return result + (b"\n" if final_lf else b"")


def _all_strings_nfc(value: Any) -> bool:
    if isinstance(value, str):
        return value == unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return all(_all_strings_nfc(item) for item in value)
    if isinstance(value, dict):
        return all(
            _all_strings_nfc(key) and _all_strings_nfc(item)
            for key, item in value.items()
        )
    return True


def _decode_unpadded_base64url(value: Any) -> bytes | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        return None
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return raw if encoded == value else None


def _reject_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreezeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_float(value: str) -> Any:
    raise FreezeError(f"floating-point JSON value is forbidden: {value}")


def _reject_constant(value: str) -> Any:
    raise FreezeError(f"non-finite JSON value is forbidden: {value}")


def load_json_exact(path: Path, *, allow_floats: bool = False) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise FreezeError("UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FreezeError("JSON is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_float=float if allow_floats else _reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise FreezeError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise FreezeError("JSON root must be an object")
    return value


def _path(root: Path, relative: str) -> Path:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FreezeError(f"missing file: {relative}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise FreezeError(f"not a regular repository file: {relative}")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise FreezeError(f"symlinked freeze input: {relative}")
    return resolved


def _need(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _exact_keys(
    errors: list[str], value: Any, expected: Iterable[str], label: str
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    if set(value) != set(expected):
        errors.append(f"{label} fields are not exact")
        return False
    return True


def derive_4d_lock(baseline: str) -> str:
    first = baseline.find("alembic==1.19.1")
    if first < 0:
        raise FreezeError("baseline lock has no exact first requirement")
    body = baseline[first:]
    dbos_pattern = re.compile(
        r"dbos==2\.30\.0 \\\n"
        r"(?:    --hash=sha256:[0-9a-f]{64}(?: \\\n|\n)){2}"
        r"    # via -r release/dependency-roots\.in\n"
    )
    body, replacements = dbos_pattern.subn(DBOS_4D_BLOCK, body)
    if replacements != 1:
        raise FreezeError("baseline DBOS block is not exact")
    marker = "pydantic==2.12.5"
    if body.count(marker) != 1:
        raise FreezeError("baseline PyNaCl insertion marker is not exact")
    body = body.replace(marker, PYNACL_BLOCK + marker)
    return LOCK_HEADER + body


def _check_pre_runtime_required_artifacts(
    dependency_gate: dict[str, Any], errors: list[str]
) -> None:
    _exact_keys(
        errors,
        dependency_gate,
        {
            "runtimeCodingAllowed",
            "gateRule",
            "artifacts",
            "harnessEntropyInjectionException",
        },
        "pre-runtime required artifacts",
    )
    expected = [
        (
            "schema_export_mapping",
            SCHEMA_ARTIFACT_PATH,
            "Eva24DSchemaExportFreeze/v1",
            ("table/column", "JSONL", "DBOS-free generic-reader"),
        ),
        (
            "connector_provider_fixtures",
            CONNECTOR_ARTIFACT_PATH,
            "Eva24DConnectorProviderFixtures/v1",
            (
                "five workers and six bindings",
                "poisoned-mail and ambiguous-provider bytes",
                "ExperienceSourceEvent/PresenceContext",
            ),
        ),
        (
            "deterministic_model_vectors",
            MODEL_ARTIFACT_PATH,
            "Eva24DModelVectors/v1",
            ("PlannerRequest/model_input", "provider-429", "zero-hidden-retry"),
        ),
        (
            "concurrency_crash_schedules",
            SCHEDULE_ARTIFACT_PATH,
            "Eva24DConcurrencyCrashSchedules/v1",
            (
                "duplicate-overlap barrier",
                "fifteen crash",
                "terminal replay",
                "harness-only entropy",
            ),
        ),
    ]
    rows = dependency_gate.get("artifacts", [])
    _need(
        errors,
        dependency_gate.get("runtimeCodingAllowed") is True
        and dependency_gate.get("gateRule") == PRE_RUNTIME_GATE_RULE
        and len(rows) == 4,
        "runtime coding gate is not exact for the four sealed design artifacts",
    )
    _need(
        errors,
        dependency_gate.get("harnessEntropyInjectionException")
        == _expected_profile_harness_entropy_exception(),
        "profile harness entropy exception is not the sole exact adoption authority",
    )
    for row, (artifact_id, path, contract, terms) in zip(rows, expected):
        if not isinstance(row, dict):
            errors.append("pre-runtime artifact row must be an object")
            continue
        _need(
            errors,
            set(row)
            == {
                "artifactId",
                "path",
                "contract",
                "expectedSha256",
                "status",
                "requiredContents",
            }
            and row.get("artifactId") == artifact_id
            and row.get("path") == path
            and row.get("contract") == contract
            and row.get("expectedSha256") == EXPECTED_ARTIFACT_SHA256[path]
            and row.get("status") == "sealed_verified"
            and all(term in row.get("requiredContents", "") for term in terms),
            f"pre-runtime artifact binding is not exact: {artifact_id}",
        )


def _walk_dicts(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, f"{path}[{index}]")


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scalar_json_pointers(value: Any, path: str = "") -> dict[str, Any]:
    pointers: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            pointers.update(
                _scalar_json_pointers(item, f"{path}/{_json_pointer_escape(key)}")
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointers.update(_scalar_json_pointers(item, f"{path}/{index}"))
    else:
        pointers[path] = value
    return pointers


def _resolve_json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise FreezeError("invalid RFC 6901 JSON Pointer")
    current = value
    for encoded in pointer[1:].split("/"):
        if re.search(r"~(?![01])", encoded):
            raise FreezeError("invalid RFC 6901 JSON Pointer escape")
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise FreezeError("unresolved RFC 6901 JSON Pointer")
            current = current[token]
        elif isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise FreezeError("invalid RFC 6901 array index")
            index = int(token)
            if index >= len(current):
                raise FreezeError("unresolved RFC 6901 array index")
            current = current[index]
        else:
            raise FreezeError("RFC 6901 JSON Pointer traverses a scalar")
    return current


def _decode_standard_base64(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    return raw if base64.b64encode(raw).decode("ascii") == value else None


def _load_embedded_canonical_json(
    raw: bytes, errors: list[str], label: str
) -> dict[str, Any] | None:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, FreezeError) as exc:
        errors.append(f"{label} is not strict canonical JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} canonical JSON root is not an object")
        return None
    _need(
        errors,
        canonical_json_bytes(value) == raw and _all_strings_nfc(value),
        f"{label} bytes are not EvaCanonicalJSON/v1",
    )
    return value


def _check_base64_metadata(
    metadata: Any, errors: list[str], label: str
) -> bytes | None:
    if not isinstance(metadata, dict):
        errors.append(f"{label} canonical-byte metadata is not an object")
        return None
    encoded = metadata.get("base64", metadata.get("canonicalBytesBase64"))
    length = metadata.get("byteLength", metadata.get("canonicalByteLength"))
    digest = metadata.get("sha256", metadata.get("record_schema_sha256"))
    raw = _decode_standard_base64(encoded)
    _need(
        errors,
        raw is not None
        and isinstance(length, int)
        and length == len(raw)
        and digest == sha256_bytes(raw),
        f"{label} base64 length or hash is not exact",
    )
    return raw


def _check_content_addressed_payloads(
    value: Any, errors: list[str], label: str
) -> None:
    for path, row in _walk_dicts(value, label):
        if "payload" not in row or "canonicalSha256" not in row:
            continue
        payload = row.get("payload")
        try:
            raw = canonical_json_bytes(payload)
        except (TypeError, ValueError):
            errors.append(f"{path} payload cannot be canonicalized")
            continue
        _need(
            errors,
            row.get("canonicalSha256") == sha256_bytes(raw)
            and (
                "canonicalByteLength" not in row
                or row.get("canonicalByteLength") == len(raw)
            )
            and _all_strings_nfc(payload),
            f"{path} content address is not exact",
        )


def _check_schema_artifact(schema: dict[str, Any], errors: list[str]) -> None:
    _need(
        errors,
        schema.get("contract") == EXPECTED_ARTIFACT_CONTRACTS[SCHEMA_ARTIFACT_PATH]
        and schema.get("status") == "sealed_pre_runtime_design",
        "schema/export artifact contract or status is not sealed",
    )
    semantic = (
        schema.get("authorityBindings", {})
        .get("executionProfile", {})
        .get("semanticCore", {})
    )
    _need(
        errors,
        semantic.get("excludedTopLevelKey") == "preRuntimeRequiredArtifacts"
        and semantic.get("canonicalByteLengthAtBaseline") == 67629
        and semantic.get("sha256") == EXPECTED_PROFILE_SEMANTIC_CORE_SHA256,
        "schema/export artifact source-profile semantic core is not exact",
    )

    family = schema.get("schemaFamily", {})
    tables = family.get("tables", [])
    noncanonical_tables = family.get("nonCanonicalTables", [])
    _need(
        errors,
        family.get("canonicalTableCount") == 39
        and family.get("nonCanonicalTableCount") == 5
        and family.get("totalTableCount") == 44
        and isinstance(tables, list)
        and len(tables) == 39
        and [row.get("exportOrdinal") for row in tables if isinstance(row, dict)]
        == list(range(1, 40))
        and len({row.get("table") for row in tables if isinstance(row, dict)}) == 39,
        "schema/export artifact does not freeze exactly 39 canonical tables",
    )

    conventions = schema.get("databaseConventions", {})
    automatic_rule = conventions.get("automaticIndexRule", {})
    automatic_manifest = conventions.get("automaticForeignKeyIndexManifest", {})
    manifest_core = automatic_manifest.get("manifestCore", {})
    manifest_entries = manifest_core.get("entries", [])
    fk_pattern = re.compile(r"^(?P<name>[a-z][a-z0-9_]*): \((?P<local>[^)]+)\) -> ")
    unique_pattern = re.compile(r"^[a-z][a-z0-9_]*: UNIQUE \((?P<columns>[^)]+)\)")
    natural_name_pattern = re.compile(
        r"^ix_[a-z][a-z0-9_]*__fk__[a-z][a-z0-9_]*"
        r"(?:__[a-z][a-z0-9_]*)*$"
    )
    expected_entries: list[dict[str, Any]] = []
    explicit_names: set[str] = set()
    all_tables = [
        row
        for row in (*tables, *noncanonical_tables)
        if isinstance(row, dict) and isinstance(row.get("table"), str)
    ]
    for table in all_tables:
        unqualified_name = table["table"].split(".", 1)[-1]
        explicit_names.add(f"pk_{unqualified_name}")
        for field in (
            "foreignKeys",
            "uniques",
            "checks",
            "additionalIndexes",
            "indexes",
        ):
            for declaration in table.get(field, []):
                if isinstance(declaration, str) and ":" in declaration:
                    explicit_names.add(declaration.split(":", 1)[0])

        key_prefixes = [list(table.get("primaryKey", []))]
        for declaration in table.get("uniques", []):
            match = unique_pattern.match(declaration)
            if match is not None:
                key_prefixes.append(
                    [column.strip() for column in match["columns"].split(",")]
                )
        for declaration in table.get("foreignKeys", []):
            match = fk_pattern.match(declaration)
            if match is None:
                continue
            local_columns = [column.strip() for column in match["local"].split(",")]
            if any(
                prefix[: len(local_columns)] == local_columns for prefix in key_prefixes
            ):
                continue
            natural_name = f"ix_{unqualified_name}__fk__{'__'.join(local_columns)}"
            natural_name_sha256 = sha256_bytes(natural_name.encode("ascii"))
            selected_name = (
                natural_name
                if len(natural_name.encode("utf-8")) <= 63
                else f"{natural_name[:45]}__{natural_name_sha256[:16]}"
            )
            expected_entries.append(
                {
                    "table": table["table"],
                    "localColumns": local_columns,
                    "naturalName": natural_name,
                    "selectedName": selected_name,
                    "naturalNameSha256": natural_name_sha256,
                }
            )

    selected_names = [row["selectedName"] for row in expected_entries]
    shortened_count = sum(
        row["selectedName"] != row["naturalName"] for row in expected_entries
    )
    entries_sha256 = sha256_bytes(canonical_json_bytes(expected_entries))
    _need(
        errors,
        automatic_rule.get("method") == "BTREE"
        and automatic_rule.get("maxUtf8Bytes") == 63
        and automatic_rule.get("overLimitPrefixAsciiBytes") == 45
        and automatic_rule.get("hashSuffixHexChars") == 16
        and automatic_rule.get("manifestEntryCount") == 79
        and automatic_rule.get("shortenedEntryCount") == 50
        and automatic_rule.get("orderedEntriesCanonicalSha256")
        == EXPECTED_AUTOMATIC_FK_INDEX_MANIFEST_SHA256
        and manifest_core.get("contract") == "Eva4DAutomaticForeignKeyIndexManifest/v1"
        and manifest_core.get("schemaVersion") == 1
        and manifest_core.get("entryCount") == 79
        and manifest_core.get("shortenedEntryCount") == 50
        and manifest_core.get("naturalNameGrammar") == natural_name_pattern.pattern
        and manifest_entries == expected_entries
        and len(expected_entries) == 79
        and shortened_count == 50
        and entries_sha256 == EXPECTED_AUTOMATIC_FK_INDEX_MANIFEST_SHA256
        and automatic_manifest.get("orderedEntriesCanonicalSha256") == entries_sha256
        and len(selected_names) == len(set(selected_names))
        and not (set(selected_names) & explicit_names)
        and all(
            natural_name_pattern.fullmatch(row["naturalName"])
            and row["naturalName"].isascii()
            and row["selectedName"].isascii()
            and len(row["selectedName"].encode("utf-8")) <= 63
            for row in expected_entries
        ),
        "automatic foreign-key index authority is not exact or PostgreSQL-safe",
    )

    column_pattern = re.compile(
        r"^(?P<name>[a-z][a-z0-9_]*) (?P<type>.+?) (?P<nullability>NOT NULL|NULL)$"
    )

    def declared_columns(table: dict[str, Any]) -> list[tuple[str, str]] | None:
        parsed: list[tuple[str, str]] = []
        for declaration in table.get("columns", []):
            match = column_pattern.fullmatch(declaration)
            if match is None:
                return None
            parsed.append((match["name"], match["type"]))
        return parsed

    role_manifest = conventions.get("roleTableColumnGrantManifest", {})
    role_entries = role_manifest.get("entries", [])
    expected_role_entries: list[dict[str, Any]] = []
    role_source_valid = True
    for table in all_tables:
        columns = declared_columns(table)
        if columns is None:
            role_source_valid = False
            continue
        primary_key = set(table.get("primaryKey", []))
        cas_profile = table.get("cas")
        update_columns = (
            [name for name, _ in columns if name not in primary_key]
            if isinstance(cas_profile, str)
            and cas_profile.startswith(
                ("revisionCas", "currentSwap", "projectionUpsert")
            )
            else []
        )
        owner = table.get("owner", {})
        delete_owner = owner.get("delete") if isinstance(owner, dict) else None
        retention = table.get("retention")
        retention_profile = (
            retention.get("profile") if isinstance(retention, dict) else retention
        )
        if isinstance(retention, dict) and retention.get("deleteOwner") != delete_owner:
            role_source_valid = False
        table_privileges = ["SELECT", "INSERT"]
        if delete_owner and retention_profile:
            table_privileges.append("DELETE")
        expected_role_entries.append(
            {
                "table": table["table"],
                "roles": ["eva_runtime", "eva_dbos"],
                "tablePrivileges": table_privileges,
                "updateColumns": update_columns,
                "sourceCasProfile": cas_profile,
                "sourceDeleteOwner": delete_owner,
                "sourceRetentionProfile": retention_profile,
            }
        )
    role_entries_sha256 = sha256_bytes(canonical_json_bytes(expected_role_entries))
    _need(
        errors,
        set(role_manifest)
        == {
            "contract",
            "derivation",
            "roleOrder",
            "entryOrder",
            "entryCount",
            "updateTableCount",
            "deleteTableCount",
            "orderedEntriesCanonicalization",
            "orderedEntriesCanonicalSha256",
            "failClosedRule",
            "entries",
        }
        and role_manifest.get("contract")
        == "Eva24DPostgresqlRoleTableColumnGrantManifest/v1"
        and role_manifest.get("derivation")
        == {
            "selectAndInsert": (
                "grant table-level SELECT and INSERT on every sealed canonical "
                "and noncanonical table to both application roles"
            ),
            "update": (
                "grant column-level UPDATE only when cas starts with revisionCas, "
                "currentSwap or projectionUpsert, including any sealed profile "
                "variants, and then on every declared non-primary-key column only"
            ),
            "delete": (
                "grant table-level DELETE only when owner.delete is nonempty and "
                "the table has its sealed retention object/profile; for canonical "
                "tables retention.deleteOwner must equal owner.delete"
            ),
            "noImplicitPrivileges": (
                "REVOKE ALL from both application roles before applying each exact "
                "entry; no table-level UPDATE, TRUNCATE, REFERENCES or TRIGGER "
                "privilege"
            ),
        }
        and role_manifest.get("roleOrder") == ["eva_runtime", "eva_dbos"]
        and role_manifest.get("entryOrder")
        == (
            "schemaFamily canonical table order followed by noncanonical table "
            "order; updateColumns follow declared column order minus primaryKey"
        )
        and role_manifest.get("entryCount") == len(role_entries) == 44
        and role_manifest.get("updateTableCount")
        == sum(bool(row["updateColumns"]) for row in expected_role_entries)
        == 31
        and role_manifest.get("deleteTableCount")
        == sum("DELETE" in row["tablePrivileges"] for row in expected_role_entries)
        == 44
        and role_entries == expected_role_entries
        and role_entries_sha256
        == role_manifest.get("orderedEntriesCanonicalSha256")
        == EXPECTED_ROLE_GRANT_MANIFEST_SHA256
        and role_manifest.get("orderedEntriesCanonicalization")
        == (
            "UTF-8 JSON of entries array with lexicographically sorted object keys, "
            "separators comma/colon, ensure_ascii=false and no trailing LF"
        )
        and role_manifest.get("failClosedRule")
        == (
            "reject any role, table, privilege, update column, order, count, "
            "source-profile or canonical-hash mismatch"
        )
        and role_source_valid
        and all(
            "UPDATE" not in row["tablePrivileges"]
            and not (set(row["updateColumns"]) & set(table.get("primaryKey", [])))
            for row, table in zip(role_entries, all_tables)
        ),
        "schema role/table/column grant authority is not exact",
    )

    collation_manifest = conventions.get("collationManifest", {})
    eligible_types = ["text", "text[]", "varchar(64)"]
    expected_collation_entries: list[dict[str, Any]] = []
    collation_source_valid = True
    for table in all_tables:
        parsed = declared_columns(table)
        if parsed is None:
            collation_source_valid = False
            continue
        baseline = set(table.get("baseline4BColumnsPreserved", []))
        for declaration, (name, declared_type) in zip(table.get("columns", []), parsed):
            if declared_type not in eligible_types:
                continue
            expected_collation_entries.append(
                {
                    "table": table["table"],
                    "column": name,
                    "declaredType": declared_type,
                    "physicalAction": (
                        "alter_preserved_4b"
                        if declaration in baseline
                        else "create_or_add"
                    ),
                }
            )
    collation_entries = collation_manifest.get("entries", [])
    collation_entries_sha256 = sha256_bytes(
        canonical_json_bytes(expected_collation_entries)
    )
    _need(
        errors,
        set(collation_manifest)
        == {
            "contract",
            "collation",
            "eligibleDeclaredTypes",
            "materialization",
            "downgradeOfPreserved4B",
            "entryOrder",
            "entryCount",
            "preserved4BAlterCount",
            "orderedEntriesCanonicalization",
            "orderedEntriesCanonicalSha256",
            "failClosedRule",
            "entries",
        }
        and collation_manifest.get("contract") == "Eva24DPostgresqlCollationManifest/v1"
        and collation_manifest.get("collation") == "C"
        and collation_manifest.get("eligibleDeclaredTypes") == eligible_types
        and collation_manifest.get("materialization")
        == (
            'every entry is physically declared with explicit PostgreSQL COLLATE "C"; '
            "the five alter_preserved_4b entries are changed inside the admitted "
            "empty-only 4d_0003 transaction without editing historical 4B files"
        )
        and collation_manifest.get("downgradeOfPreserved4B")
        == (
            'restore the five historical columns to COLLATE "default" when 4d_0003 '
            "downgrades to 4b_0002"
        )
        and collation_manifest.get("entryOrder")
        == (
            "schemaFamily canonical table order followed by noncanonical table "
            "order, then declared column order"
        )
        and collation_manifest.get("entryCount")
        == len(collation_entries)
        == len(expected_collation_entries)
        == 201
        and collation_manifest.get("preserved4BAlterCount")
        == sum(
            row["physicalAction"] == "alter_preserved_4b"
            for row in expected_collation_entries
        )
        == 5
        and collation_entries == expected_collation_entries
        and collation_entries_sha256
        == collation_manifest.get("orderedEntriesCanonicalSha256")
        == EXPECTED_COLLATION_MANIFEST_SHA256
        and collation_manifest.get("orderedEntriesCanonicalization")
        == (
            "UTF-8 JSON of entries array with lexicographically sorted object keys, "
            "separators comma/colon, ensure_ascii=false and no trailing LF"
        )
        and collation_manifest.get("failClosedRule")
        == (
            "reject an eligible column missing from the manifest, an ineligible "
            "entry, a non-C physical attcollation, any order/count/hash mismatch or "
            "a preserved-4B action-set mismatch"
        )
        and collation_source_valid,
        "schema explicit-C collation authority is not exact",
    )

    installations = next(
        (
            table
            for table in tables
            if isinstance(table, dict)
            and table.get("table") == "eva_core.installations"
        ),
        {},
    )
    object_store_contracts = [
        row
        for row in installations.get("payloadContracts", [])
        if isinstance(row, dict) and row.get("column") == "object_store_payload"
    ]
    object_store_contract = (
        object_store_contracts[0] if len(object_store_contracts) == 1 else {}
    )
    expected_projection_authority = {
        "installationId": {
            "scalarColumn": "installation_id",
            "payloadField": "installation_id",
            "rule": "byte_equal",
        },
        "objectStoreId": {
            "scalarColumn": "object_store_id",
            "payloadField": "object_store_id",
            "algorithm": "UUIDv5_SHA1_namespace_name",
            "namespaceUuid": EXPECTED_OBJECT_STORE_NAMESPACE_UUID,
            "nameEncoding": "UTF-8",
            "sealedEvaLogicalId": "object-store:noor-4d:eva",
            "sealedEvaUuid": "62e07e08-9793-5eee-a1f7-2ed9305211de",
        },
        "absoluteHostPath": {
            "payloadField": "absolute_host_path_in_contract",
            "requiredValue": False,
        },
    }
    projected_store_id = str(
        uuid.uuid5(
            uuid.UUID(EXPECTED_OBJECT_STORE_NAMESPACE_UUID),
            expected_projection_authority["objectStoreId"]["sealedEvaLogicalId"],
        )
    )
    _need(
        errors,
        set(object_store_contract)
        == {
            "column",
            "allowed",
            "requiredWhen",
            "authorityEquality",
            "projectionAuthority",
        }
        and object_store_contract.get("allowed") == ["ObjectStore/v1"]
        and object_store_contract.get("requiredWhen") == "object_store_id IS NOT NULL"
        and object_store_contract.get("authorityEquality")
        == (
            "installation_id is byte-equal to payload.installation_id; scalar "
            "object_store_id is UUIDv5(namespace "
            f"{EXPECTED_OBJECT_STORE_NAMESPACE_UUID}, UTF-8 payload.object_store_id); "
            "payload.absolute_host_path_in_contract=false"
        )
        and object_store_contract.get("projectionAuthority")
        == expected_projection_authority
        and projected_store_id
        == expected_projection_authority["objectStoreId"]["sealedEvaUuid"],
        "schema ObjectStore scalar/payload projection authority is not exact",
    )

    principal_bindings = next(
        (
            table
            for table in tables
            if isinstance(table, dict)
            and table.get("table") == "eva_core.principal_bindings"
        ),
        {},
    )
    principal_binding_export = principal_bindings.get("export", {})
    expected_principal_binding_selection = {
        "operator": "set_union_distinct",
        "directReferenceClosure": {
            "sourceFamilies": [
                "eva_core.ingress_requests",
                "eva_core.messages",
                "eva_core.consent_grants",
            ],
            "rule": (
                "existing exported direct principal-binding-reference closure "
                "scoped to the target tenant and installation"
            ),
        },
        "relationshipChannelBindingEvidenceClosure": {
            "relationshipTable": "eva_core.relationships",
            "relationshipScopeColumns": ["tenant_id", "installation_id"],
            "relationshipEvidenceArrayColumn": "channel_binding_evidence_refs",
            "bindingTable": "eva_core.principal_bindings",
            "bindingTenantColumn": "tenant_id",
            "bindingVerificationEvidenceColumn": "verification_evidence_id",
            "matchRule": (
                "binding.tenant_id=relationship.tenant_id AND "
                "binding.verification_evidence_id="
                "ANY(relationship.channel_binding_evidence_refs)"
            ),
        },
        "selectedBindingEvidenceInstallationValidation": {
            "evidenceTable": "eva_evidence.evidence_envelopes",
            "bindingEvidenceColumn": "verification_evidence_id",
            "requiredEvidenceScopeColumns": ["tenant_id", "installation_id"],
            "requiredScope": "target tenant and installation",
            "onMismatch": "reject_export_without_widening",
        },
    }
    _need(
        errors,
        set(principal_binding_export)
        == {
            "recordContract",
            "relativePath",
            "primaryKeyOrder",
            "mapping",
            "selectionAuthority",
        }
        and principal_binding_export.get("recordContract")
        == "EvaCanonicalRecord/eva_core.principal_bindings/v1"
        and principal_binding_export.get("relativePath")
        == "records/006-eva_core-principal-bindings-v1.jsonl"
        and principal_binding_export.get("primaryKeyOrder")
        == ["tenant_id", "principal_binding_id"]
        and principal_binding_export.get("mapping")
        == (
            "selected binding IDs are the set union of the existing direct exported "
            "ingress/message/consent binding-reference closure and every same-tenant "
            "binding whose verification_evidence_id appears in an exported "
            "relationship.channel_binding_evidence_refs; all columns one-to-one; "
            "reject when selected binding verification evidence does not belong to "
            "the target installation rather than widening export scope"
        )
        and principal_binding_export.get("selectionAuthority")
        == expected_principal_binding_selection,
        "schema principal-binding export selection authority is not exact",
    )

    relationships = next(
        (
            table
            for table in tables
            if isinstance(table, dict)
            and table.get("table") == "eva_core.relationships"
        ),
        {},
    )
    expected_relationship_validation = [
        (
            "every channel_binding_evidence_refs element must resolve to "
            "eva_evidence.evidence_envelopes in the same tenant and installation "
            "before commit; each element may select every same-tenant "
            "eva_core.principal_bindings row whose verification_evidence_id matches, "
            "subject to the selected binding evidence belonging to this "
            "relationship's target installation"
        )
    ]
    _need(
        errors,
        relationships.get("referenceValidations") == expected_relationship_validation
        and relationships.get("foreignKeys", []).count(
            "fk_relationships_source_event: "
            "(tenant_id,installation_id,source_event_id) -> "
            "eva_memory.source_events(tenant_id,installation_id,source_event_id) "
            "ON DELETE RESTRICT"
        )
        == 1,
        "schema relationship reference-validation authority is not exact",
    )

    portable = schema.get("portableExport", {}).get(
        "generatedPortableRecordSchemas", {}
    )
    portable_schemas = portable.get("schemas", [])
    _need(
        errors,
        portable.get("contract") == "EvaPortableRecordSchemaCollection/v1"
        and portable.get("schemaCount") == 39
        and isinstance(portable_schemas, list)
        and len(portable_schemas) == 39,
        "portable record-schema collection is not exactly 39 schemas",
    )
    portable_by_uri: dict[str, str] = {}
    portable_by_table: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(portable_schemas):
        if not isinstance(row, dict):
            errors.append(f"portable record schema {index + 1} is not an object")
            continue
        raw = _check_base64_metadata(row, errors, f"portable record schema {index + 1}")
        decoded = (
            _load_embedded_canonical_json(
                raw, errors, f"portable record schema {index + 1}"
            )
            if raw is not None
            else None
        )
        table = tables[index] if index < len(tables) else {}
        transform = row.get("transformSpec")
        _need(
            errors,
            row.get("exportOrdinal") == index + 1
            and isinstance(table, dict)
            and row.get("table") == table.get("table")
            and row.get("recordContract")
            == table.get("export", {}).get("recordContract")
            and row.get("recordJsonlRelativePath")
            == table.get("export", {}).get("relativePath")
            and row.get("transformSpecSha256")
            == sha256_bytes(canonical_json_bytes(transform)),
            f"portable record schema {index + 1} table/transform binding is not exact",
        )
        if decoded is not None and isinstance(decoded.get("$id"), str):
            portable_by_uri[decoded["$id"]] = row.get("record_schema_sha256", "")
        if decoded is not None and isinstance(row.get("table"), str):
            portable_by_table[row["table"]] = decoded

    episodes_properties = (
        portable_by_table.get("eva_core.episodes", {})
        .get("properties", {})
        .get("row", {})
        .get("properties", {})
    )
    generation_schema = portable_by_table.get("eva_core.episode_generations", {}).get(
        "properties", {}
    )
    generation_row_properties = generation_schema.get("row", {}).get("properties", {})
    generation_key_properties = generation_schema.get("primary_key", {}).get(
        "properties", {}
    )
    event_properties = (
        portable_by_table.get("eva_core.episode_events", {})
        .get("properties", {})
        .get("row", {})
        .get("properties", {})
    )
    _need(
        errors,
        episodes_properties.get("current_generation", {}).get("minimum") == 1
        and generation_row_properties.get("generation", {}).get("minimum") == 1
        and generation_key_properties.get("generation", {}).get("minimum") == 1
        and event_properties.get("before_revision", {}).get("minimum") == 0,
        "portable episode primitive bounds reject an admitted initial generation",
    )

    portable_manifest_raw = _check_base64_metadata(
        portable.get("manifest"), errors, "portable record-schema manifest"
    )
    portable_manifest = (
        _load_embedded_canonical_json(
            portable_manifest_raw, errors, "portable record-schema manifest"
        )
        if portable_manifest_raw is not None
        else None
    )
    if portable_manifest is not None:
        summaries = portable_manifest.get("schemas", [])
        _need(
            errors,
            portable_manifest.get("contract") == "EvaPortableRecordSchemaManifest/v1"
            and portable.get("manifest", {}).get("sha256")
            == EXPECTED_SCHEMA_PORTABLE_MANIFEST_SHA256
            and isinstance(summaries, list)
            and len(summaries) == 39
            and all(
                isinstance(summary, dict)
                and isinstance(row, dict)
                and all(summary.get(key) == row.get(key) for key in summary)
                for summary, row in zip(summaries, portable_schemas)
            ),
            "portable record-schema manifest does not bind all 39 schemas",
        )

    object_authority = (
        schema.get("portableExport", {})
        .get("objectManifest", {})
        .get("recordSchemaAuthority", {})
    )
    object_raw = _check_base64_metadata(
        object_authority, errors, "object-manifest record schema"
    )
    object_schema = (
        _load_embedded_canonical_json(
            object_raw, errors, "object-manifest record schema"
        )
        if object_raw is not None
        else None
    )
    _need(
        errors,
        object_authority.get("record_schema_sha256")
        == EXPECTED_SCHEMA_OBJECT_MANIFEST_RECORD_SHA256,
        "object-manifest record schema hash is not exact",
    )

    reader_manifest = schema.get("genericReader", {}).get("outputSchemaManifest", {})
    readers = reader_manifest.get("schemas", [])
    _need(
        errors,
        reader_manifest.get("contract") == "EvaGenericReaderOutputSchemaCollection/v1"
        and reader_manifest.get("schemaCount") == 8
        and isinstance(readers, list)
        and len(readers) == 8
        and [row.get("outputOrdinal") for row in readers if isinstance(row, dict)]
        == list(range(1, 9)),
        "generic-reader output collection is not exactly eight schemas",
    )
    decoded_readers: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(readers):
        raw = _check_base64_metadata(
            row, errors, f"generic-reader output schema {index + 1}"
        )
        decoded = (
            _load_embedded_canonical_json(
                raw, errors, f"generic-reader output schema {index + 1}"
            )
            if raw is not None
            else None
        )
        if decoded is None or not isinstance(row, dict):
            continue
        decoded_readers[index + 1] = decoded
        refs = {
            nested.get("$ref")
            for _, nested in _walk_dicts(decoded)
            if isinstance(nested.get("$ref"), str)
        }
        expected_refs = {
            ref.get("schemaUri")
            for ref in row.get("referencedPortableSchemas", [])
            if isinstance(ref, dict)
        }
        _need(
            errors,
            expected_refs <= refs
            and all(uri in portable_by_uri for uri in expected_refs)
            and all(
                portable_by_uri.get(ref.get("schemaUri"))
                == ref.get("record_schema_sha256")
                for ref in row.get("referencedPortableSchemas", [])
                if isinstance(ref, dict)
            ),
            f"generic-reader output schema {index + 1} has an unbound portable ref",
        )

    summary_properties = decoded_readers.get(1, {}).get("properties", {})
    summary_record_sets = summary_properties.get("record_sets", {}).get(
        "prefixItems", []
    )
    summary_records_valid = (
        isinstance(summary_record_sets, list) and len(summary_record_sets) == 39
    )
    if summary_records_valid:
        for row, item in zip(portable_schemas, summary_record_sets):
            properties = item.get("properties", {}) if isinstance(item, dict) else {}
            if not (
                isinstance(row, dict)
                and properties.get("export_ordinal", {}).get("const")
                == row.get("exportOrdinal")
                and properties.get("table", {}).get("const") == row.get("table")
                and properties.get("relative_path", {}).get("const")
                == row.get("recordJsonlRelativePath")
                and properties.get("record_schema_sha256", {}).get("const")
                == row.get("record_schema_sha256")
            ):
                summary_records_valid = False
                break
    _need(
        errors,
        summary_records_valid,
        "generic-reader summary does not bind all 39 record-schema hashes",
    )

    summary_receipts = summary_properties.get("output_receipts", {}).get(
        "prefixItems", []
    )
    expected_receipts = [
        (
            row.get("outputRelativePath"),
            row.get("contract"),
            row.get("record_schema_sha256"),
        )
        for row in readers[1:]
        if isinstance(row, dict)
    ]
    expected_receipts.append(
        (
            "readback/object-manifest.jsonl",
            "EvaEncryptedObjectManifestRecord/v1",
            object_authority.get("record_schema_sha256"),
        )
    )
    actual_receipts = []
    if isinstance(summary_receipts, list):
        for item in summary_receipts:
            properties = item.get("properties", {}) if isinstance(item, dict) else {}
            actual_receipts.append(
                (
                    properties.get("relative_path", {}).get("const"),
                    properties.get("contract", {}).get("const"),
                    properties.get("record_schema_sha256", {}).get("const"),
                )
            )
    _need(
        errors,
        actual_receipts == expected_receipts
        and summary_properties.get("object_manifest_record_schema_sha256", {}).get(
            "const"
        )
        == object_authority.get("record_schema_sha256"),
        "generic-reader summary does not bind its seven outputs and object schema",
    )

    reader_manifest_raw = _check_base64_metadata(
        reader_manifest.get("manifest"), errors, "generic-reader schema manifest"
    )
    decoded_reader_manifest = (
        _load_embedded_canonical_json(
            reader_manifest_raw, errors, "generic-reader schema manifest"
        )
        if reader_manifest_raw is not None
        else None
    )
    if decoded_reader_manifest is not None:
        summaries = decoded_reader_manifest.get("schemas", [])
        _need(
            errors,
            decoded_reader_manifest.get("contract")
            == "EvaGenericReaderOutputSchemaManifest/v1"
            and reader_manifest.get("manifest", {}).get("sha256")
            == EXPECTED_SCHEMA_READER_MANIFEST_SHA256
            and decoded_reader_manifest.get("portableRecordSchemaManifestSha256")
            == portable.get("manifest", {}).get("sha256")
            and decoded_reader_manifest.get("objectManifestRecordSchemaSha256")
            == object_authority.get("record_schema_sha256")
            and isinstance(summaries, list)
            and len(summaries) == 8
            and all(
                isinstance(summary, dict)
                and isinstance(row, dict)
                and all(summary.get(key) == row.get(key) for key in summary)
                for summary, row in zip(summaries, readers)
            ),
            "generic-reader schema manifest does not bind its 39+1 inputs and eight outputs",
        )

    admission = schema.get("migrationFreeze", {}).get(
        "initialAdmissionPrecondition", {}
    )
    statements = admission.get("orderedPreDdlStatements", [])
    statement_names = [
        "set_transaction_isolation",
        "restore_external_session_authority",
        "acquire_existing_migration_advisory_lock",
        "attest_external_global_read_authority",
        "attest_source_revision",
        "lock_all_preserved_tables_against_writes",
        "count_all_preserved_rows_without_rls_filtering",
        "switch_to_ddl_owner_after_admission",
        "attest_ddl_owner",
    ]
    statement_hashes_valid = isinstance(statements, list) and len(statements) == 9
    if statement_hashes_valid:
        for ordinal, (name, statement) in enumerate(
            zip(statement_names, statements), start=1
        ):
            sql = statement.get("sqlUtf8") if isinstance(statement, dict) else None
            if not (
                isinstance(statement, dict)
                and statement.get("ordinal") == ordinal
                and statement.get("name") == name
                and isinstance(sql, str)
                and statement.get("sqlUtf8Sha256") == sha256_bytes(sql.encode("utf-8"))
            ):
                statement_hashes_valid = False
                break
    zero_counts = {
        "tenants_row_count": 0,
        "installations_row_count": 0,
        "episodes_row_count": 0,
        "episode_events_row_count": 0,
    }
    decision = admission.get("admissionDecision", {})
    canonical_counts = decision.get("canonicalResultBytesUtf8")
    on_admit = admission.get("onAdmit", {})
    on_reject = admission.get("onReject", {})
    _need(
        errors,
        admission.get("contract") == "Eva4DInitialCatalogAdmission/v1"
        and admission.get("appliesOnlyToRevision") == "4d_0003_core_continuity"
        and admission.get("sourceRevision") == "4b_0002_episode_atomicity"
        and admission.get("admissionClass") == "pre_traffic_empty_only"
        and admission.get("noDdlBeforeAdmissionDecision") is True
        and statement_hashes_valid
        and decision.get("requiredRowCounts") == zero_counts
        and isinstance(canonical_counts, str)
        and canonical_counts.encode("utf-8") == canonical_json_bytes(zero_counts)
        and decision.get("canonicalResultSha256")
        == sha256_bytes(canonical_counts.encode("utf-8"))
        and decision.get("admitIffEveryCountEqualsZero") is True
        and decision.get("oneOrMoreRowsDecision") == "rejected_nonempty"
        and decision.get("noBackfillOrDefaultInferenceAllowed") is True
        and on_admit.get("backfillAllowed") is False
        and on_admit.get("columnDefaultAllowed") is False
        and on_admit.get("syntheticSeedValueAllowed") is False
        and on_admit.get("commitRequiresAlembicVersion") == "4d_0003_core_continuity"
        and on_reject.get("raiseBeforeFirstDdl") is True
        and on_reject.get("ddlStatementCount") == 0
        and on_reject.get("rollbackWholeTransaction") is True
        and on_reject.get("catalogChanged") is False
        and on_reject.get("alembicVersionAfterRollback") == "4b_0002_episode_atomicity",
        "schema 4d_0003 admission is not exact empty-only authority",
    )

    audit = schema.get("selfAudit", {})
    _need(
        errors,
        object_schema is not None
        and audit.get("expectedExportRecordSets") == 39
        and audit.get("expectedObjectManifestRecordSchemas") == 1
        and audit.get("expectedGenericReaderOutputSchemas") == 8
        and audit.get("expectedScheduleSnapshotFamilies") == 18
        and audit.get("expectedAuxiliarySchemaManifestSha256")
        == reader_manifest.get("manifest", {}).get("sha256"),
        "schema/export self-audit counts or auxiliary hash are not exact",
    )


def _check_connector_artifact(connector: dict[str, Any], errors: list[str]) -> None:
    _need(
        errors,
        connector.get("contract")
        == EXPECTED_ARTIFACT_CONTRACTS[CONNECTOR_ARTIFACT_PATH]
        and connector.get("status") == "sealed_pre_runtime_fixture_not_implemented",
        "connector/provider artifact contract or status is not sealed",
    )
    source = connector.get("authorityBindings", {}).get("sourceProfileAtBaseline", {})
    _need(
        errors,
        source.get("semanticCoreCanonicalSha256")
        == EXPECTED_PROFILE_SEMANTIC_CORE_SHA256,
        "connector/provider artifact source-profile semantic core is not exact",
    )
    expected_counts = {
        "fixtureWorkerPrograms": 5,
        "connectorManifests": 5,
        "logicalBindings": 6,
        "concreteBindings": 12,
        "operationSpecifications": 17,
        "operationSchemaDocuments": 34,
        "capabilityManifests": 20,
        "providerStates": 28,
        "operationCallCases": 58,
        "rpcConformanceCases": 58,
        "errorConformanceCases": 4,
        "protocolFailureCases": 4,
        "ackVectors": 10,
        "scenarioOracles": 19,
    }
    counts = connector.get("counts", {})
    rpc = connector.get("languageNeutralRpcConformance", {})
    host = connector.get("hostEvidence", {})
    _need(
        errors,
        counts == expected_counts
        and len(connector.get("fixtureWorkerPrograms", {})) == 5
        and len(connector.get("connectorManifests", {})) == 5
        and len(connector.get("logicalBindings", [])) == 6
        and len(connector.get("concreteBindings", [])) == 12
        and len(connector.get("operationSchemas", {})) == 34
        and len(connector.get("capabilityManifests", [])) == 20
        and len(connector.get("providerStateMachine", {}).get("states", {})) == 28
        and len(connector.get("operationCallCases", {})) == 58
        and len(rpc.get("conformanceCases", [])) == 58
        and len(rpc.get("errorCases", [])) == 4
        and len(rpc.get("protocolFailureCases", [])) == 4
        and len(host.get("outcomeAckVectors", {})) == 10
        and len(connector.get("scenarios", {})) == 19,
        "connector/provider artifact counts are not exact",
    )
    _check_content_addressed_payloads(connector, errors, "connector")

    def is_utf8_canonical_text_set(value: Any) -> bool:
        return (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
            and len(value) == len(set(value))
            and value == sorted(value, key=lambda item: item.encode("utf-8"))
        )

    concrete_binding_payloads: dict[tuple[Any, Any], dict[str, Any]] = {}
    binding_sets_valid = True
    for binding in connector.get("concreteBindings", []):
        if not isinstance(binding, dict):
            binding_sets_valid = False
            continue
        payload = binding.get("payload", {})
        verification = binding.get("verificationEvidence", {}).get("payload", {})
        operations = payload.get("confirmed_operations")
        scopes = payload.get("confirmed_scopes")
        if not (
            isinstance(payload, dict)
            and isinstance(verification, dict)
            and is_utf8_canonical_text_set(operations)
            and is_utf8_canonical_text_set(scopes)
            and verification.get("verified_operations") == operations
            and verification.get("verified_scopes") == scopes
        ):
            binding_sets_valid = False
        concrete_binding_payloads[
            (binding.get("logicalBindingKey"), binding.get("identityRun"))
        ] = payload
    _need(
        errors,
        binding_sets_valid and len(concrete_binding_payloads) == 12,
        "connector concrete binding and verification set arrays are not exact "
        "UTF-8-canonical mirrors",
    )

    operation_cases = connector.get("operationCallCases", {})
    conformance_cases = rpc.get("conformanceCases", {})
    rpc_binding_roundtrips_valid = (
        isinstance(operation_cases, dict)
        and isinstance(conformance_cases, dict)
        and len(conformance_cases) == 58
    )
    for case_id, conformance_case in (
        conformance_cases.items() if isinstance(conformance_cases, dict) else []
    ):
        operation_case = operation_cases.get(case_id, {})
        expected_binding = concrete_binding_payloads.get(
            (
                operation_case.get("logicalBindingKey"),
                operation_case.get("identityRun"),
            )
        )
        initialize_binding = (
            conformance_case.get("initializeRequest", {})
            .get("params", {})
            .get("connector_binding")
            if isinstance(conformance_case, dict)
            else None
        )
        if not (
            isinstance(operation_case, dict)
            and isinstance(expected_binding, dict)
            and initialize_binding == expected_binding
            and is_utf8_canonical_text_set(
                initialize_binding.get("confirmed_operations")
            )
            and is_utf8_canonical_text_set(initialize_binding.get("confirmed_scopes"))
        ):
            rpc_binding_roundtrips_valid = False
    _need(
        errors,
        rpc_binding_roundtrips_valid,
        "connector RPC initialize bindings are not exact UTF-8-canonical "
        "concrete-binding roundtrips",
    )

    sandbox_hash = connector.get("fixtureWorkerSandbox", {}).get("canonicalSha256")
    programs = connector.get("fixtureWorkerPrograms", {})
    manifests = connector.get("connectorManifests", {})
    _need(
        errors,
        set(programs)
        == set(manifests)
        == {"mail", "erp", "calendar", "document", "channel"},
        "connector fixture program/manifest set is not exact",
    )
    for worker in sorted(set(programs) | set(manifests)):
        program = programs.get(worker, {})
        manifest = manifests.get(worker, {}).get("payload", {})
        _need(
            errors,
            manifest.get("contract") == "ConnectorManifest/v1"
            and manifest.get("executable_sha256") == program.get("canonicalSha256")
            and manifest.get("sandbox_profile_sha256") == sandbox_hash,
            f"connector {worker} manifest does not bind its program and sandbox",
        )

    for case_id, row in connector.get("operationCallCases", {}).items():
        if not isinstance(row, dict) or "protocolTrace" not in row:
            continue
        _need(
            errors,
            row.get("protocolTraceSha256")
            == sha256_bytes(canonical_json_bytes(row.get("protocolTrace"))),
            f"connector operation case {case_id} protocol trace hash is not exact",
        )
    scenarios = connector.get("scenarios", {})
    for scenario_id, row in scenarios.items():
        if not isinstance(row, dict) or "externalCallLedger" not in row:
            continue
        ledger = row.get("externalCallLedger", [])
        _need(
            errors,
            row.get("ledgerSha256") == sha256_bytes(canonical_json_bytes(ledger))
            and [entry.get("ordinal") for entry in ledger if isinstance(entry, dict)]
            == list(range(1, len(ledger) + 1)),
            f"connector scenario {scenario_id} ledger is not content-addressed and ordered",
        )

    turn_authority = host.get("ingressTurnEnvelopes", {})
    envelopes = turn_authority.get("envelopes", {})
    ack_vectors = host.get("outcomeAckVectors", {})
    vector_map = turn_authority.get("outcomeAckVectorMap", {})
    _need(
        errors,
        turn_authority.get("schemaContract") == "TurnEnvelope/v1"
        and turn_authority.get("schemaPath")
        == "schemas/contracts/TurnEnvelope-v1.schema.json"
        and turn_authority.get("schemaSha256")
        == "7a2f24945463b0b24ce57a591b939fa441e60060d4bedbe55bff440b2e18e242"
        and set(envelopes)
        == {
            "noorEva",
            "noorMira",
            "noorDocumentRejectEva",
            "noorConfirmationRejectEva",
            "noorErpAmbiguityEva",
            "noorCorrectionEva",
        }
        and len(vector_map) == len(ack_vectors) == 10
        and set(vector_map) == set(ack_vectors),
        "connector ingress authority does not bind five TurnEnvelopes to nine ACK vectors",
    )
    auth_bytes = turn_authority.get("authenticationEvidenceBytes", {})
    for envelope_id, wrapper in envelopes.items():
        if not isinstance(wrapper, dict):
            errors.append(f"connector TurnEnvelope {envelope_id} is not an object")
            continue
        raw = _decode_standard_base64(wrapper.get("canonicalBytes", {}).get("base64"))
        payload = wrapper.get("payload", {})
        identity = (
            "Mira"
            if payload.get("installation_id") == "fixture:installation:mira:001"
            else "Eva"
        )
        _need(
            errors,
            raw is not None
            and raw == canonical_json_bytes(payload)
            and wrapper.get("canonicalByteLength") == len(raw)
            and wrapper.get("canonicalSha256") == sha256_bytes(raw)
            and payload.get("contract") == "TurnEnvelope/v1"
            and payload.get("authentication_evidence_sha256")
            == auth_bytes.get(identity, {}).get("canonicalSha256"),
            f"connector TurnEnvelope {envelope_id} literal bytes are not exact",
        )
    for vector_id, envelope_id in vector_map.items():
        ingress = ack_vectors.get(vector_id, {}).get("ingress", {})
        ingress_payload = ingress.get("payload", {})
        envelope = envelopes.get(envelope_id, {})
        turn = envelope.get("payload", {})
        _need(
            errors,
            ingress_payload.get("contract") == "IngressRequest/v1"
            and ingress_payload.get("turn_envelope_sha256")
            == envelope.get("canonicalSha256")
            and all(
                ingress_payload.get(field) == turn.get(field)
                for field in (
                    "installation_id",
                    "tenant_id",
                    "channel_id",
                    "request_id",
                    "conversation_id",
                )
            ),
            f"connector ACK vector {vector_id} ingress-to-TurnEnvelope mapping is not exact",
        )

    presence_replies = host.get("outcomeDeliveryContent", {}).get("presenceReplies", {})
    c04_expected = {
        "c04bDocumentFallbackEva": ("Eva", 7, 2),
        "c04bDocumentFallbackMira": ("Mira", 7, 2),
        "c04cReplyFallbackEva": ("Eva", 11, 4),
        "c04cReplyFallbackMira": ("Mira", 11, 4),
    }
    for scenario_id, (identity, calls, receipts) in c04_expected.items():
        scenario = scenarios.get(scenario_id, {})
        expected = scenario.get("expected", {})
        reply = presence_replies.get(scenario_id, {}).get("payload", {})
        _need(
            errors,
            scenario.get("identityRun") == identity
            and scenario.get("episodeId") == "fixture:episode:noor:001"
            and len(scenario.get("externalCallLedger", [])) == calls
            and expected.get("connectorCalls") == calls
            and expected.get("postRecoveryModelCalls") == 0
            and expected.get("replyCandidates") == 0
            and expected.get("effectReceipts") == receipts
            and expected.get("fallbackReason") == "candidate_absent"
            and reply.get("contract") == "PresenceReply/v1"
            and reply.get("reply_candidate_sha256") is None
            and reply.get("fallback_reason") == "candidate_absent"
            and reply.get("episode_id") == "fixture:episode:noor:001",
            f"connector C04 fallback surface is not exact for {scenario_id}",
        )

    c07 = scenarios.get("c07AmbiguousBeforeCommit", {})
    c07_expected = c07.get("expected", {})
    c07_first = connector.get("operationCallCases", {}).get(
        "calendar.create.ambiguous-before-commit", {}
    )
    _need(
        errors,
        c07.get("postRecoveryCalls", [])[:2]
        == [
            "calendar.reconcile.not-observed",
            "calendar.create.ordinal2-after-not-observed",
        ]
        and c07_expected.get("connectorCalls") == 13
        and c07_expected.get("calendarCreateInvocations") == 2
        and c07_expected.get("calendarOrdinal1ProviderMutationDelta") == 0
        and c07_expected.get("reconcileResult") == "not_observed"
        and c07_expected.get("calendarOrdinal2ProviderMutationDelta") == 1
        and c07_expected.get("ordinal3EffectAttempts") == 0
        and c07_first.get("outcomeKind") == "ambiguous_transport"
        and c07_first.get("providerMutationDelta") == 0
        and c07_first.get("faultMarker", {}).get("payload", {}).get("trigger_phase")
        == "after_request_consumed_before_provider_commit",
        "connector C07 ambiguous-before-commit surface is not exact",
    )

    c10 = scenarios.get("c10ReadbackRepeat", {})
    c10_expected = c10.get("expected", {})
    c10_ledger = c10.get("externalCallLedger", [])
    c10_first = connector.get("operationCallCases", {}).get(
        "calendar.readback.matches", {}
    )
    c10_repeat = connector.get("operationCallCases", {}).get(
        "calendar.readback.matches.c10-repeat", {}
    )
    c10_authority = host.get("c10ReadbackRepeatAuthority", {})
    c10_intent = c10_authority.get("readIntent", {}).get("payload", {})
    c10_pre_crash = c10_authority.get("preCrashAttemptReference", {})
    c10_attempt = c10_authority.get("repeatReadAttempt", {}).get("payload", {})
    c10_observation = c10_authority.get("repeatReadObservation", {}).get("payload", {})
    c10_readback_wrapper = host.get("providerReadbacks", {}).get(
        "calendarC10Repeat", {}
    )
    c10_readback = c10_readback_wrapper.get("payload", {})
    c10_capability = c10_authority.get("capabilityGrant", {}).get("payload", {})
    c10_credential = c10_authority.get("credentialGrant", {}).get("payload", {})
    c10_request_binding = c10_authority.get("requestHashBinding", {})
    c10_response_binding = c10_authority.get("responseHashBinding", {})
    c10_provider_binding = c10_authority.get("providerStateBinding", {})
    c10_first_wire = c10_first.get("providerExchange", {})
    c10_repeat_wire = c10_repeat.get("providerExchange", {})
    _need(
        errors,
        "calendar.readback.matches.c10-repeat" in rpc.get("conformanceCases", [])
        and c10_repeat.get("caseId") == "calendar.readback.matches.c10-repeat"
        and c10_repeat.get("operationKey") == "calendar.readback"
        and c10_repeat.get("identityRun") == "Eva"
        and c10_repeat.get("originalEffectCaseId") == "calendar.readback.matches"
        and c10_repeat.get("attemptOrdinal") == 2
        and c10_repeat.get("crashScheduleId")
        == "C10-after-readback-response-before-commit"
        and c10_repeat.get("durableReadAuthorityRef")
        == "hostEvidence.c10ReadbackRepeatAuthority"
        and c10_repeat.get("outcomeKind") == "readback"
        and c10_repeat.get("externalCallDelta") == 1
        and c10_repeat.get("providerMutationDelta") == 0
        and c10_repeat.get("providerStateBefore")
        == c10_repeat.get("providerStateAfter")
        == "calendarEvent"
        and c10_repeat.get("requestPayload", {}).get("canonicalSha256")
        == c10_first.get("requestPayload", {}).get("canonicalSha256")
        and c10_repeat_wire.get("request", {}).get("sha256")
        == c10_first_wire.get("request", {}).get("sha256")
        and c10_repeat_wire.get("response", {}).get("sha256")
        == c10_first_wire.get("response", {}).get("sha256")
        and c10_repeat.get("providerResponseBody")
        == c10_first.get("providerResponseBody"),
        "connector C10 ordinal-2 provider read is not an exact non-mutating repeat",
    )
    _need(
        errors,
        c10_authority.get("crashScheduleId")
        == "C10-after-readback-response-before-commit"
        and c10_authority.get("operationCaseId")
        == "calendar.readback.matches.c10-repeat"
        and c10_authority.get("rpcConformanceCaseId")
        == "calendar.readback.matches.c10-repeat"
        and c10_intent.get("contract") == "ReadIntent/v1"
        and c10_intent.get("read_intent_id")
        == "fixture:read-intent:calendar-readback:noor:001"
        and c10_intent.get("maximum_attempts") == 2
        and c10_pre_crash.get("read_intent_id") == c10_intent.get("read_intent_id")
        and c10_pre_crash.get("ordinal") == 1
        and c10_pre_crash.get("durableRecoveryState") == "ambiguous"
        and c10_pre_crash.get("responseBytesDurablyAdoptable") is False
        and c10_attempt.get("contract") == "ReadAttempt/v1"
        and c10_attempt.get("read_attempt_id")
        == "fixture:read-attempt:calendar-readback:noor:c10:002"
        and c10_attempt.get("read_intent_id") == c10_intent.get("read_intent_id")
        and c10_attempt.get("ordinal") == 2
        and c10_attempt.get("state") == "observed"
        and c10_observation.get("contract") == "ReadObservation/v1"
        and c10_observation.get("observation_id")
        == "fixture:observation:calendar-readback:noor:c10:002"
        and c10_observation.get("read_intent_id") == c10_intent.get("read_intent_id")
        and c10_observation.get("read_attempt_id") == c10_attempt.get("read_attempt_id")
        and c10_readback.get("contract") == "EffectReadback/v1"
        and c10_readback.get("readback_id") == "fixture:readback:calendar:noor:c10:002"
        and c10_readback.get("read_intent_id") == c10_intent.get("read_intent_id")
        and c10_readback.get("read_attempt_id") == c10_attempt.get("read_attempt_id")
        and c10_authority.get("repeatEffectReadbackRef")
        == "hostEvidence.providerReadbacks.calendarC10Repeat"
        and c10_authority.get("repeatEffectReadbackSha256")
        == c10_readback_wrapper.get("canonicalSha256"),
        "connector C10 durable ordinal-2 read authority is not exact",
    )
    _need(
        errors,
        c10_capability.get("contract") == "CapabilityGrant/v1"
        and c10_capability.get("grant_id")
        == c10_attempt.get("capability_grant_id")
        == "fixture:grant:calendar-readback-matches-c10-repeat:002"
        and c10_capability.get("grant_id") != c10_pre_crash.get("capability_grant_id")
        and c10_capability.get("nonce")
        == "fixture:nonce:capability:calendar-readback-matches-c10-repeat:002"
        and c10_credential.get("contract") == "CredentialGrant/v1"
        and c10_credential.get("credential_grant_id")
        == c10_attempt.get("credential_grant_id")
        == "fixture:credential-grant:calendar-readback-matches-c10-repeat:002"
        and c10_credential.get("credential_grant_id")
        != c10_pre_crash.get("credential_grant_id")
        and c10_credential.get("nonce")
        == "fixture:nonce:credential:calendar-readback-matches-c10-repeat:002"
        and c10_capability.get("authorized_payload_sha256")
        == c10_repeat.get("requestPayload", {}).get("canonicalSha256")
        and c10_request_binding.get("connectorInvokeRequestSha256")
        == c10_attempt.get("request_sha256")
        and c10_request_binding.get("phaseUniqueFromOrdinal1") is True
        and c10_response_binding.get("connectorInvokeSuccessSha256")
        == c10_attempt.get("response_sha256")
        and c10_response_binding.get("providerRequestWireSha256")
        == c10_repeat_wire.get("request", {}).get("sha256")
        and c10_response_binding.get("providerResponseWireSha256")
        == c10_repeat_wire.get("response", {}).get("sha256")
        == c10_observation.get("response_sha256")
        and c10_provider_binding.get("providerMutationDelta") == 0
        and c10_provider_binding.get("externalCallDelta") == 1
        and c10_provider_binding.get("beforeStateRef")
        == c10_provider_binding.get("afterStateRef")
        == "calendarEvent",
        "connector C10 repeat grants or request/response bindings are not exact",
    )
    _need(
        errors,
        c10.get("caseId") == "c10-after-readback-response-before-commit"
        and c10.get("identityRun") == "Eva"
        and c10.get("crashScheduleId") == "C10-after-readback-response-before-commit"
        and c10.get("durableReadAuthorityRef")
        == "hostEvidence.c10ReadbackRepeatAuthority"
        and c10.get("calls", [])[5:7]
        == [
            "calendar.readback.matches",
            "calendar.readback.matches.c10-repeat",
        ]
        and len(c10_ledger) == 12
        and [row.get("operationCaseId") for row in c10_ledger[5:7]]
        == [
            "calendar.readback.matches",
            "calendar.readback.matches.c10-repeat",
        ]
        and c10_ledger[5].get("requestPayloadSha256")
        == c10_ledger[6].get("requestPayloadSha256")
        and c10_ledger[5].get("providerRequestWireSha256")
        == c10_ledger[6].get("providerRequestWireSha256")
        and c10_ledger[5].get("providerResponseWireSha256")
        == c10_ledger[6].get("providerResponseWireSha256")
        and c10_ledger[5].get("providerMutationDelta")
        == c10_ledger[6].get("providerMutationDelta")
        == 0
        and c10_expected
        == {
            "connectorCalls": 12,
            "businessReads": 4,
            "effects": 4,
            "readbacks": 4,
            "reconciles": 0,
            "probes": 0,
            "providerMutations": 4,
            "effectReceipts": 4,
            "providerReadbacks": 3,
            "outcomeAckReceipts": 1,
            "durableEffectReadbacks": 3,
            "calendarReadbackInvocations": 2,
            "calendarCreateInvocations": 1,
            "calendarProviderMutations": 1,
        },
        "connector C10 crash/recovery scenario is not exact",
    )

    for identity, scenario_id in (
        ("Eva", "c13ReofferEva"),
        ("Mira", "c13ReofferMira"),
    ):
        scenario = scenarios.get(scenario_id, {})
        expected = scenario.get("expected", {})
        ledger = scenario.get("externalCallLedger", [])
        final_two = ledger[-2:] if isinstance(ledger, list) else []
        _need(
            errors,
            scenario.get("identityRun") == identity
            and len(ledger) == 12
            and expected.get("connectorCalls") == 12
            and expected.get("outcomeDeliveryInvocations") == 2
            and expected.get("outcomeProviderMutations") == 1
            and expected.get("newDeliveryIdentities") == 0
            and expected.get("differentWireOrChallenge") == 0
            and expected.get("fabricatedAcks") == 0
            and len(final_two) == 2
            and final_two[0].get("requestPayloadSha256")
            == final_two[1].get("requestPayloadSha256")
            and final_two[0].get("providerRequestWireSha256")
            == final_two[1].get("providerRequestWireSha256")
            and final_two[0].get("providerResponseWireSha256")
            == final_two[1].get("providerResponseWireSha256")
            and final_two[0].get("providerMutationDelta") == 1
            and final_two[1].get("providerMutationDelta") == 0,
            f"connector C13 exact re-offer surface is not exact for {identity}",
        )


def _check_model_artifact(model: dict[str, Any], errors: list[str]) -> None:
    _need(
        errors,
        model.get("contract") == EXPECTED_ARTIFACT_CONTRACTS[MODEL_ARTIFACT_PATH]
        and model.get("status")
        == "sealed_pre_code_fixture_program_not_runtime_implemented",
        "model-vector artifact contract or status is not sealed",
    )
    authority = model.get("authorityBindings", {})
    _need(
        errors,
        authority.get("sourceProfileAtBaseline", {}).get(
            "semanticCoreExcludingPreRuntimeRequiredArtifactsSha256"
        )
        == EXPECTED_PROFILE_SEMANTIC_CORE_SHA256
        and authority.get("schemaExportFreezeDependency", {}).get("sha256")
        == EXPECTED_ARTIFACT_SHA256[SCHEMA_ARTIFACT_PATH],
        "model-vector source-profile/schema binding is not exact",
    )

    for path, row in _walk_dicts(model, "model"):
        metadata = None
        payload = None
        if isinstance(row.get("canonicalBytes"), dict):
            metadata = row["canonicalBytes"]
            payload = row.get("payload", row.get("program"))
        elif {"base64", "byteLength", "sha256"} <= set(row):
            metadata = row
        if metadata is None:
            continue
        raw = _check_base64_metadata(metadata, errors, path)
        if raw is not None and payload is not None:
            _need(
                errors,
                raw == canonical_json_bytes(payload) and _all_strings_nfc(payload),
                f"{path} bytes do not encode the attached canonical payload",
            )
    _check_content_addressed_payloads(model, errors, "model")

    for vector in model.get("happyPathVectors", []):
        if not isinstance(vector, dict):
            errors.append("model happy-path vector is not an object")
            continue
        bindings = (
            ("requestCanonicalBytes", vector.get("request")),
            (
                "modelInputCanonicalBytes",
                vector.get("request", {}).get("model_input"),
            ),
            ("responseCanonicalBytes", vector.get("response")),
        )
        for field, payload in bindings:
            raw = _check_base64_metadata(
                vector.get(field), errors, f"model {vector.get('vectorId')} {field}"
            )
            _need(
                errors,
                raw is not None and raw == canonical_json_bytes(payload),
                f"model {vector.get('vectorId')} {field} does not bind its payload",
            )

    adapter = model.get("fixtureAdapterProfile", {})
    manifest = model.get("modelAdapterManifest", {})
    program = model.get("modelFixtureProgram", {})
    manifest_hash = manifest.get("canonicalBytes", {}).get("sha256")
    program_hash = program.get("canonicalBytes", {}).get("sha256")
    manifest_payload = manifest.get("payload", {})
    schema_binding = model.get("schemaSnapshotBinding", {})
    _need(
        errors,
        manifest_payload.get("contract") == "ModelAdapterManifest/v1"
        and program.get("program", {}).get("contract") == "ModelFixtureProgram/v1"
        and adapter.get("modelAdapterManifestSha256") == manifest_hash
        and adapter.get("modelAdapterManifestAddress") == f"sha256:{manifest_hash}"
        and adapter.get("programSha256") == program_hash
        and adapter.get("programAddress") == f"sha256:{program_hash}"
        and manifest_payload.get("modelProgramSha256") == program_hash
        and manifest_payload.get("modelProgramByteLength")
        == program.get("canonicalBytes", {}).get("byteLength")
        and schema_binding.get("model_adapter_manifest_sha256") == manifest_hash
        and schema_binding.get("model_program_sha256") == program_hash,
        "model manifest/program/schema cross-binding is not exact",
    )

    dispatches = model.get("modelDispatchVectors", [])
    _need(
        errors,
        isinstance(dispatches, list) and len(dispatches) == 20,
        "model dispatch vector count is not exactly 20",
    )
    grant_ids: list[Any] = []
    nonces: list[Any] = []
    for dispatch in dispatches:
        if not isinstance(dispatch, dict):
            errors.append("model dispatch vector is not an object")
            continue
        grant_wrapper = dispatch.get("credentialGrant", {})
        grant = grant_wrapper.get("payload", {})
        attempt_wrapper = dispatch.get("modelAttempt", {})
        attempt = attempt_wrapper.get("payload", {})
        row = dispatch.get("eva_core.model_attempts", {})
        grant_hash = grant_wrapper.get("canonicalBytes", {}).get("sha256")
        attempt_hash = attempt_wrapper.get("canonicalBytes", {}).get("sha256")
        grant_ids.append(grant.get("credential_grant_id"))
        nonces.append(grant.get("nonce"))
        _need(
            errors,
            dispatch.get("oneUseCredentialGrant") is True
            and grant.get("contract") == "CredentialGrant/v1"
            and grant.get("executable_sha256") == program_hash
            and grant.get("credential_grant_id") == attempt.get("credential_grant_id")
            and grant.get("issued_at") == dispatch.get("dispatchAt")
            and attempt.get("contract") == "ModelAttempt/v1"
            and attempt.get("episode_id") == "fixture:episode:noor:001"
            and attempt.get("purpose") == dispatch.get("purpose")
            and attempt.get("ordinal") == dispatch.get("attemptOrdinal")
            and row.get("payload") == attempt
            and row.get("payload_sha256") == attempt_hash
            and row.get("credential_grant_payload") == grant
            and row.get("credential_grant_sha256") == grant_hash
            and row.get("credential_grant_id") == grant.get("credential_grant_id")
            and row.get("credential_grant_nonce") == grant.get("nonce")
            and row.get("credential_grant_deadline") == grant.get("deadline")
            and row.get("model_adapter_manifest_sha256") == manifest_hash
            and row.get("model_program_sha256") == program_hash,
            f"model dispatch {dispatch.get('dispatchVectorId')} grant/attempt row is not exact",
        )
        if attempt.get("state") == "ambiguous":
            _need(
                errors,
                attempt.get("response_sha256") is None,
                f"model dispatch {dispatch.get('dispatchVectorId')} ambiguous response is fabricated",
            )
    _need(
        errors,
        len(set(grant_ids)) == len(grant_ids) == 20
        and len(set(nonces)) == len(nonces) == 20
        and None not in grant_ids
        and None not in nonces,
        "model dispatch grants and nonces are not 20 distinct one-use values",
    )

    rate_limit = [
        row
        for row in dispatches
        if isinstance(row, dict) and row.get("fixtureCaseId") == "provider-rate-limit"
    ]
    _need(
        errors,
        [row.get("attemptOrdinal") for row in rate_limit] == [1, 2]
        and len(
            {
                row.get("credentialGrant", {})
                .get("payload", {})
                .get("credential_grant_id")
                for row in rate_limit
            }
        )
        == 2
        and model.get("provider429Recovery", {}).get("ordinal3Allowed") is False
        and model.get("provider429Recovery", {}).get("hiddenRetries") == 0,
        "model provider-429 recovery does not use two exact grants and no hidden retry",
    )


def _resolve_object_path(value: Any, path: Any) -> Any:
    if not isinstance(path, str) or not path:
        raise KeyError("empty object path")
    current = value
    for component in path.split("."):
        if not isinstance(current, dict) or component not in current:
            raise KeyError(path)
        current = current[component]
    return current


def _check_model_connector_crosswalk(
    model: dict[str, Any], connector: dict[str, Any], errors: list[str]
) -> None:
    crosswalk = model.get("connectorAuthorityCrosswalk", {})
    _need(
        errors,
        crosswalk.get("authorityArtifactSha256")
        == EXPECTED_ARTIFACT_SHA256[CONNECTOR_ARTIFACT_PATH],
        "model connector-authority whole-file binding is not exact",
    )

    def target(binding: dict[str, Any], label: str) -> Any:
        try:
            payload = _resolve_object_path(connector, binding.get("sourcePath"))
        except KeyError:
            errors.append(f"model connector crosswalk source path is missing: {label}")
            return None
        _need(
            errors,
            binding.get("canonicalSha256")
            == sha256_bytes(canonical_json_bytes(payload)),
            f"model connector crosswalk hash is not exact: {label}",
        )
        return payload

    happy = {
        row.get("vectorId"): row
        for row in model.get("happyPathVectors", [])
        if isinstance(row, dict)
    }
    document_context = (
        happy.get("happy-document-eva", {})
        .get("request", {})
        .get("model_input", {})
        .get("presence_context", {})
    )
    document_bindings = crosswalk.get("documentPresenceContext", {})
    document_receipt = target(
        document_bindings.get("effect_receipts[0]", {}),
        "document effect_receipts[0]",
    )
    document_readback = target(
        document_bindings.get("effect_readbacks[0]", {}),
        "document effect_readbacks[0]",
    )
    _need(
        errors,
        document_context.get("effect_receipts") == [document_receipt]
        and document_context.get("effect_readbacks") == [document_readback],
        "model document PresenceContext differs from connector durable authority",
    )

    reply_bindings = crosswalk.get("replyPresenceContexts", {})
    receipt_binding = reply_bindings.get("effect_receipts", {})
    readback_binding = reply_bindings.get("effect_readbacks", {})
    try:
        reply_receipts = [
            _resolve_object_path(connector, path)
            for path in receipt_binding.get("sourcePaths", [])
        ]
        reply_readbacks = [
            _resolve_object_path(connector, path)
            for path in readback_binding.get("sourcePaths", [])
        ]
    except KeyError as exc:
        errors.append(f"model reply PresenceContext source path is missing: {exc}")
        reply_receipts = []
        reply_readbacks = []
    _need(
        errors,
        receipt_binding.get("canonicalSha256s")
        == [sha256_bytes(canonical_json_bytes(row)) for row in reply_receipts]
        and readback_binding.get("canonicalSha256s")
        == [sha256_bytes(canonical_json_bytes(row)) for row in reply_readbacks]
        and len(reply_receipts) == len(reply_readbacks) == 3,
        "model reply PresenceContext connector receipt/readback hashes are not exact",
    )
    delivery_receipt = target(
        reply_bindings.get("delivery_receipts[0]", {}),
        "reply delivery_receipts[0]",
    )
    target(
        reply_bindings.get("outcome_view", {}),
        "reply outcome_view",
    )
    shared_delivery = crosswalk.get("sharedConfirmationDeliveryIntent", {})
    shared_payload = shared_delivery.get("payload")
    _need(
        errors,
        shared_delivery.get("canonicalBytes", {}).get("sha256")
        == sha256_bytes(canonical_json_bytes(shared_payload))
        and shared_delivery.get("samePayloadForEvaAndMira") is True,
        "model shared confirmation DeliveryIntent bytes are not exact",
    )
    for vector_id in ("happy-reply-eva", "happy-reply-mira"):
        vector = happy.get(vector_id, {})
        context = (
            vector.get("request", {}).get("model_input", {}).get("presence_context", {})
        )
        _need(
            errors,
            context.get("effect_receipts") == reply_receipts
            and context.get("effect_readbacks") == reply_readbacks
            and context.get("delivery_receipts") == [delivery_receipt]
            and context.get("delivery_intents") == [shared_payload],
            f"model {vector_id} PresenceContext differs from connector durable authority",
        )

    candidates = crosswalk.get("replyCandidates", {})
    for identity, vector_id in (
        ("Eva", "happy-reply-eva"),
        ("Mira", "happy-reply-mira"),
    ):
        candidate = target(candidates.get(identity, {}), f"{identity} ReplyCandidate")
        _need(
            errors,
            happy.get(vector_id, {}).get("response") == candidate,
            f"model {identity} ReplyCandidate differs from connector delivery authority",
        )


def _json_value_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def _resolve_json_schema_ref(
    ref: Any,
    root_schema: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not isinstance(ref, str):
        return None
    base, separator, fragment = ref.partition("#")
    target_root = root_schema if not base else registry.get(base)
    if not isinstance(target_root, dict):
        return None
    target: Any = target_root
    if separator and fragment:
        if not fragment.startswith("/"):
            return None
        for token in fragment[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                return None
            target = target[token]
    return (target, target_root) if isinstance(target, dict) else None


def _json_schema_matches(
    instance: Any,
    schema: Any,
    root_schema: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> bool:
    """Validate the closed JSON-Schema subset used by the sealed contracts."""

    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        return False
    ref = schema.get("$ref")
    if ref is not None:
        resolved = _resolve_json_schema_ref(ref, root_schema, registry)
        if resolved is None or not _json_schema_matches(
            instance, resolved[0], resolved[1], registry
        ):
            return False

    if "const" in schema and not _json_value_equal(instance, schema["const"]):
        return False
    if "enum" in schema and not any(
        _json_value_equal(instance, item) for item in schema["enum"]
    ):
        return False
    if "allOf" in schema and not all(
        _json_schema_matches(instance, item, root_schema, registry)
        for item in schema["allOf"]
    ):
        return False
    if "anyOf" in schema and not any(
        _json_schema_matches(instance, item, root_schema, registry)
        for item in schema["anyOf"]
    ):
        return False
    if (
        "oneOf" in schema
        and sum(
            _json_schema_matches(instance, item, root_schema, registry)
            for item in schema["oneOf"]
        )
        != 1
    ):
        return False
    if "not" in schema and _json_schema_matches(
        instance, schema["not"], root_schema, registry
    ):
        return False
    if "if" in schema:
        branch = (
            "then"
            if _json_schema_matches(instance, schema["if"], root_schema, registry)
            else "else"
        )
        if branch in schema and not _json_schema_matches(
            instance, schema[branch], root_schema, registry
        ):
            return False

    expected_types = schema.get("type")
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    if isinstance(expected_types, list):
        type_matches = {
            "null": instance is None,
            "boolean": isinstance(instance, bool),
            "integer": isinstance(instance, int) and not isinstance(instance, bool),
            "number": isinstance(instance, (int, float))
            and not isinstance(instance, bool),
            "string": isinstance(instance, str),
            "array": isinstance(instance, list),
            "object": isinstance(instance, dict),
        }
        if not any(type_matches.get(item, False) for item in expected_types):
            return False

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            return False
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            return False
        try:
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                return False
        except (re.error, TypeError):
            return False
        if schema.get("format") == "uuid":
            try:
                if str(uuid.UUID(instance)) != instance:
                    return False
            except (ValueError, AttributeError):
                return False

    if isinstance(instance, int) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            return False
        if "maximum" in schema and instance > schema["maximum"]:
            return False

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return False
        if schema.get("uniqueItems") is True:
            encoded = [canonical_json_bytes(item) for item in instance]
            if len(encoded) != len(set(encoded)):
                return False
        if "items" in schema and not all(
            _json_schema_matches(item, schema["items"], root_schema, registry)
            for item in instance
        ):
            return False
        if "contains" in schema:
            matches = sum(
                _json_schema_matches(item, schema["contains"], root_schema, registry)
                for item in instance
            )
            if matches < schema.get("minContains", 1):
                return False
            if "maxContains" in schema and matches > schema["maxContains"]:
                return False

    if isinstance(instance, dict):
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            return False
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            return False
        required = schema.get("required", [])
        if not isinstance(required, list) or any(
            key not in instance for key in required
        ):
            return False
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        if not isinstance(properties, dict) or not isinstance(patterns, dict):
            return False
        if "propertyNames" in schema and not all(
            _json_schema_matches(key, schema["propertyNames"], root_schema, registry)
            for key in instance
        ):
            return False
        for key, value in instance.items():
            matched = False
            if key in properties:
                matched = True
                if not _json_schema_matches(
                    value, properties[key], root_schema, registry
                ):
                    return False
            try:
                matching_patterns = [
                    item
                    for pattern, item in patterns.items()
                    if re.search(pattern, key)
                ]
            except (re.error, TypeError):
                return False
            if matching_patterns:
                matched = True
                if not all(
                    _json_schema_matches(value, item, root_schema, registry)
                    for item in matching_patterns
                ):
                    return False
            if not matched:
                additional = schema.get("additionalProperties", {})
                if additional is False:
                    return False
                if isinstance(additional, dict) and not _json_schema_matches(
                    value, additional, root_schema, registry
                ):
                    return False
    return True


def _load_contract_schema_registry(
    root: Path, schema_artifact: dict[str, Any], errors: list[str]
) -> dict[str, dict[str, Any]]:
    authority = schema_artifact.get("authorityBindings", {}).get("contracts", {})
    try:
        manifest_path = _path(root, CONTRACT_MANIFEST_PATH)
        manifest = load_json_exact(manifest_path)
    except (OSError, FreezeError) as exc:
        errors.append(f"contract schema manifest: {exc}")
        return {}
    expected_manifest_hash = authority.get("manifestSha256")
    schemas = manifest.get("schemas", {})
    _need(
        errors,
        authority.get("manifestPath") == CONTRACT_MANIFEST_PATH
        and expected_manifest_hash
        == "fcf6655ccc61f9258b132620b02d51492c7eb19ce762b4e1728548b6fa2d53a5"
        and sha256_file(manifest_path) == expected_manifest_hash
        and manifest.get("manifest") == "EvaContractSchemaManifest/v1"
        and manifest.get("contractCount") == 64
        and isinstance(schemas, dict)
        and len(schemas) == 64,
        "bound contract-schema manifest is not exact",
    )
    registry: dict[str, dict[str, Any]] = {}
    if not isinstance(schemas, dict):
        return registry
    for contract, digest in schemas.items():
        relative = f"schemas/contracts/{contract.replace('/', '-')}.schema.json"
        try:
            path = _path(root, relative)
            document = load_json_exact(path)
        except (OSError, FreezeError) as exc:
            errors.append(f"bound contract schema {contract}: {exc}")
            continue
        schema_id = document.get("$id")
        _need(
            errors,
            isinstance(schema_id, str)
            and sha256_file(path) == digest
            and document.get("properties", {}).get("contract", {}).get("const")
            == contract,
            f"bound contract schema bytes are not exact: {contract}",
        )
        if isinstance(schema_id, str):
            registry[schema_id] = document
    return registry


def _partial_unique_applies(row: dict[str, Any], predicate: str | None) -> bool:
    if predicate is None:
        return True
    match = re.fullmatch(r"([a-z0-9_]+)='([^']*)'", predicate)
    if match:
        return row.get(match.group(1)) == match.group(2)
    match = re.fullmatch(r"([a-z0-9_]+) IS NOT NULL", predicate)
    if match:
        return row.get(match.group(1)) is not None
    raise ValueError(f"unsupported frozen partial unique predicate: {predicate}")


def _check_snapshot_relations(
    rows_by_table: dict[str, list[dict[str, Any]]],
    table_definitions: dict[str, dict[str, Any]],
) -> bool:
    for table, definition in table_definitions.items():
        rows = rows_by_table.get(table, [])
        primary_key = definition.get("primaryKey", [])
        primary_values = [tuple(row.get(key) for key in primary_key) for row in rows]
        if len(primary_values) != len(set(primary_values)):
            return False
        for spec in definition.get("uniques", []):
            match = re.fullmatch(r"[^:]+: UNIQUE \(([^)]*)\)(?: WHERE (.*))?", spec)
            if match is None:
                return False
            columns = [item.strip() for item in match.group(1).split(",")]
            seen: set[tuple[Any, ...]] = set()
            for row in rows:
                if not _partial_unique_applies(row, match.group(2)):
                    continue
                key = tuple(row.get(column) for column in columns)
                if any(value is None for value in key):
                    continue
                if key in seen:
                    return False
                seen.add(key)
        for spec in definition.get("foreignKeys", []):
            match = re.fullmatch(
                r"[^:]+: \(([^)]*)\) -> ([a-z_]+\.[a-z_]+)\(([^)]*)\) .+",
                spec,
            )
            if match is None:
                return False
            source = [item.strip() for item in match.group(1).split(",")]
            target_table = match.group(2)
            target = [item.strip() for item in match.group(3).split(",")]
            target_values = {
                tuple(row.get(column) for column in target)
                for row in rows_by_table.get(target_table, [])
            }
            for row in rows:
                key = tuple(row.get(column) for column in source)
                if any(value is None for value in key):
                    continue
                if key not in target_values:
                    return False
    return True


def _check_schedule_companions(
    schedule: dict[str, Any],
    schema: dict[str, Any],
    connector: dict[str, Any],
    model: dict[str, Any],
    errors: list[str],
) -> None:
    authority = schedule.get("authorityBindings", {})
    expected_artifacts = [
        {
            "artifactId": "schema_export_mapping",
            "path": SCHEMA_ARTIFACT_PATH,
            "requiredAtRuntimeCodingGate": True,
            "sha256": EXPECTED_ARTIFACT_SHA256[SCHEMA_ARTIFACT_PATH],
        },
        {
            "artifactId": "connector_provider_fixtures",
            "path": CONNECTOR_ARTIFACT_PATH,
            "requiredAtRuntimeCodingGate": True,
            "sha256": EXPECTED_ARTIFACT_SHA256[CONNECTOR_ARTIFACT_PATH],
        },
        {
            "artifactId": "deterministic_model_vectors",
            "path": MODEL_ARTIFACT_PATH,
            "requiredAtRuntimeCodingGate": True,
            "sha256": EXPECTED_ARTIFACT_SHA256[MODEL_ARTIFACT_PATH],
        },
    ]
    portable = schedule.get("portableSnapshotAuthority", {})
    _need(
        errors,
        authority.get("companionArtifacts") == expected_artifacts
        and portable.get("companionWholeFileSha256")
        == {
            "schema": EXPECTED_ARTIFACT_SHA256[SCHEMA_ARTIFACT_PATH],
            "connector": EXPECTED_ARTIFACT_SHA256[CONNECTOR_ARTIFACT_PATH],
            "model": EXPECTED_ARTIFACT_SHA256[MODEL_ARTIFACT_PATH],
        },
        "schedule companion whole-file hashes are not exact",
    )

    schema_portable = (
        schema.get("portableExport", {})
        .get("generatedPortableRecordSchemas", {})
        .get("schemas", [])
    )
    summary_keys = {
        "canonicalByteLength",
        "exportOrdinal",
        "recordContract",
        "recordJsonlRelativePath",
        "recordSchemaRelativePath",
        "record_schema_sha256",
        "sourceDefinitionSha256",
        "table",
        "transformSpecSha256",
    }
    expected_portable = [
        {key: row.get(key) for key in summary_keys}
        for row in schema_portable
        if isinstance(row, dict)
    ]
    schema_readers = (
        schema.get("genericReader", {})
        .get("outputSchemaManifest", {})
        .get("schemas", [])
    )
    reader_keys = {
        "canonicalByteLength",
        "contract",
        "outputOrdinal",
        "outputRelativePath",
        "record_schema_sha256",
        "schemaRelativePath",
    }
    expected_readers = [
        {key: row.get(key) for key in reader_keys}
        for row in schema_readers
        if isinstance(row, dict)
    ]
    object_schema = (
        schema.get("portableExport", {})
        .get("objectManifest", {})
        .get("recordSchemaAuthority", {})
    )
    expected_object_schema = {
        key: object_schema.get(key)
        for key in (
            "canonicalByteLength",
            "record_schema_sha256",
            "schemaRelativePath",
        )
    }
    _need(
        errors,
        portable.get("portableRecordSchemaManifestSha256")
        == EXPECTED_SCHEMA_PORTABLE_MANIFEST_SHA256
        and portable.get("genericReaderOutputSchemaManifestSha256")
        == EXPECTED_SCHEMA_READER_MANIFEST_SHA256
        and portable.get("portableRecordSchemas") == expected_portable
        and portable.get("genericReaderOutputSchemas") == expected_readers
        and portable.get("objectManifestRecordSchema") == expected_object_schema,
        "schedule does not bind the exact 39+1+8 schema authority",
    )

    correction = portable.get("companionContentBindings", {}).get(
        "correction_before_first_effect", {}
    )
    connector_correction = connector.get("hostEvidence", {}).get(
        "correctionBeforeFirstEffectAuthority", {}
    )
    model_correction = model.get("correctionBeforeFirstEffect", {})
    expected_correction = {
        "connector_ingress_sha256": connector_correction.get(
            "correctionIngress", {}
        ).get("canonicalSha256"),
        "connector_terminal_presence_reply_sha256": connector_correction.get(
            "generation2", {}
        ).get("terminalPresenceReplySha256"),
        "connector_turn_envelope_sha256": connector_correction.get(
            "correctionTurnEnvelopeSha256"
        ),
        "model_document_draft_sha256": model_correction.get("generation2", {})
        .get("documentDraft", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "model_generation_1_decision_sha256": model_correction.get("generation1", {})
        .get("modelDecision", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "model_generation_1_validated_plan_sha256": model_correction.get(
            "generation1", {}
        )
        .get("validatedPlan", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "model_generation_2_decision_sha256": model_correction.get("generation2", {})
        .get("modelDecision", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "model_generation_2_validated_plan_sha256": model_correction.get(
            "generation2", {}
        )
        .get("validatedPlan", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "model_reply_candidate_sha256": model_correction.get("generation2", {})
        .get("replyCandidate", {})
        .get("canonicalBytes", {})
        .get("sha256"),
    }
    content = portable.get("companionContentBindings", {})
    _need(
        errors,
        correction == expected_correction
        and content.get("portable_record_schema_manifest_sha256")
        == EXPECTED_SCHEMA_PORTABLE_MANIFEST_SHA256
        and content.get("generic_reader_output_schema_manifest_sha256")
        == EXPECTED_SCHEMA_READER_MANIFEST_SHA256
        and content.get("object_manifest_record_schema_sha256")
        == EXPECTED_SCHEMA_OBJECT_MANIFEST_RECORD_SHA256
        and content.get("model_program_sha256")
        == model.get("fixtureAdapterProfile", {}).get("programSha256")
        and content.get("model_adapter_manifest_sha256")
        == model.get("fixtureAdapterProfile", {}).get("modelAdapterManifestSha256")
        and content.get("shared_delivery_intent_payload_sha256")
        == model.get("connectorAuthorityCrosswalk", {})
        .get("sharedConfirmationDeliveryIntent", {})
        .get("canonicalBytes", {})
        .get("sha256"),
        "schedule companion content hashes are not exact",
    )


def _check_harness_entropy_authority(
    schedule: dict[str, Any],
    profile: dict[str, Any] | None,
    fact_by_id: dict[Any, dict[str, Any]],
    errors: list[str],
) -> None:
    authority = schedule.get("harnessEntropyInjectionAuthority", {})
    top_keys = {
        "authority_hash_rule",
        "authority_sha256",
        "canonicalization",
        "contract",
        "durable_consumption_trace_gate",
        "exact_fixture_scope",
        "forbidden_activation_and_shortcuts",
        "nonce_injection",
        "portable_snapshot_preservation",
        "profile_semantic_binding",
        "purpose",
        "schedule_path_bindings",
        "schema_version",
        "status",
    }
    if not _exact_keys(
        errors, authority, top_keys, "schedule harness entropy authority"
    ):
        return
    authority_without_hash = {
        key: value for key, value in authority.items() if key != "authority_sha256"
    }
    _need(
        errors,
        authority.get("contract") == "Eva4DHarnessEntropyInjectionAuthority/v1"
        and authority.get("schema_version") == 1
        and authority.get("status") == "sealed_harness_only_fail_closed"
        and authority.get("canonicalization") == "EvaCanonicalJSON/v1"
        and authority.get("authority_hash_rule")
        == (
            "SHA-256 over EvaCanonicalJSON/v1 bytes of this authority object "
            "excluding only authority_sha256"
        )
        and authority.get("authority_sha256")
        == sha256_bytes(canonical_json_bytes(authority_without_hash))
        == EXPECTED_HARNESS_ENTROPY_AUTHORITY_SHA256
        and authority.get("purpose")
        == (
            "Permit the exact Eva fixed-nonce known-answer envelope only while "
            "executing these isolated 4D deterministic schedules; this is not "
            "runtime nonce authority."
        ),
        "schedule harness entropy authority content address or scope is not exact",
    )

    nonce = authority.get("nonce_injection", {})
    nonce_raw = _decode_standard_base64(nonce.get("nonce_base64"))
    try:
        nonce_hex_raw = bytes.fromhex(str(nonce.get("nonce_hex", "")))
    except ValueError:
        nonce_hex_raw = b""
    _need(
        errors,
        set(nonce)
        == {
            "consumption_ordinal",
            "cross_scenario_state_reuse_allowed",
            "general_rng_seed",
            "injection_point",
            "interface",
            "maximum_consumptions_per_isolated_scenario_run",
            "nonce_base64",
            "nonce_byte_length",
            "nonce_encoding",
            "nonce_hex",
            "nonce_sha256",
            "ownership",
            "production_random_nonce_rule_override_allowed",
            "second_or_reentrant_consumption_action",
        }
        and nonce_raw == nonce_hex_raw
        and len(nonce_hex_raw) == nonce.get("nonce_byte_length") == 24
        and nonce.get("nonce_hex") == EXPECTED_HARNESS_NONCE_HEX
        and nonce.get("nonce_sha256")
        == sha256_bytes(nonce_hex_raw)
        == EXPECTED_HARNESS_NONCE_SHA256
        and nonce.get("nonce_encoding") == "lowercase_hex"
        and nonce.get("ownership") == "noor_4d_schedule_harness_only"
        and nonce.get("interface")
        == (
            "one-shot in-memory entropy callback passed explicitly by the isolated "
            "schedule harness to the object-encryption fixture adapter"
        )
        and nonce.get("injection_point")
        == (
            "the nonce argument of nacl.secret.Aead(key).encrypt(plaintext, "
            "aad=header, nonce=exact_nonce)"
        )
        and nonce.get("maximum_consumptions_per_isolated_scenario_run") == 1
        and nonce.get("consumption_ordinal") == 1
        and nonce.get("second_or_reentrant_consumption_action")
        == "fail_closed_before_encryption"
        and nonce.get("cross_scenario_state_reuse_allowed") is False
        and nonce.get("general_rng_seed") is False
        and nonce.get("production_random_nonce_rule_override_allowed") is False,
        "schedule harness nonce injection is not exact, one-use or harness-only",
    )

    profile_binding = authority.get("profile_semantic_binding", {})
    encrypted = (profile or {}).get("encryptedObjectFormat", {})
    fixture_keys = encrypted.get("keyBinding", {}).get("fourDFixtureKeyRecords", [])
    kat_vectors = encrypted.get("knownAnswerVectors", {}).get("vectors", [])
    eva_key = next(
        (
            row
            for row in fixture_keys
            if isinstance(row, dict) and row.get("identity") == "Eva"
        ),
        {},
    )
    eva_kat = next(
        (
            row
            for row in kat_vectors
            if isinstance(row, dict) and row.get("identity") == "Eva"
        ),
        {},
    )
    _need(
        errors,
        profile_binding
        == {
            "fixture_key_record_canonical_sha256": (
                EXPECTED_EVA_FIXTURE_KEY_RECORD_SHA256
            ),
            "fixture_key_selector": (
                "encryptedObjectFormat.keyBinding.fourDFixtureKeyRecords[identity=Eva]"
            ),
            "known_answer_vector_canonical_sha256": EXPECTED_EVA_KAT_VECTOR_SHA256,
            "known_answer_vector_selector": (
                "encryptedObjectFormat.knownAnswerVectors.vectors[identity=Eva]"
            ),
            "path": PROFILE_PATH,
            "runtime_nonce_rule_remains_authoritative": True,
            "semantic_core_sha256": EXPECTED_PROFILE_SEMANTIC_CORE_SHA256,
        }
        and sha256_bytes(canonical_json_bytes(eva_key))
        == EXPECTED_EVA_FIXTURE_KEY_RECORD_SHA256
        and sha256_bytes(canonical_json_bytes(eva_kat))
        == EXPECTED_EVA_KAT_VECTOR_SHA256,
        "schedule harness entropy authority does not bind the exact profile fixture",
    )

    scope = authority.get("exact_fixture_scope", {})
    expected_scope_values = {
        "canonical_aad_sha256": EXPECTED_XCHACHA_KATS["Eva"]["aadSha256"],
        "complete_envelope_byte_length": 553,
        "complete_envelope_sha256": EXPECTED_XCHACHA_KATS["Eva"]["envelopeSha256"],
        "identity": "Eva",
        "installation_id": EXPECTED_XCHACHA_KATS["Eva"]["installationId"],
        "key_ref": EXPECTED_XCHACHA_KATS["Eva"]["keyRef"],
        "key_sha256": EXPECTED_XCHACHA_KATS["Eva"]["keySha256"],
        "object_id": EXPECTED_XCHACHA_KATS["Eva"]["objectId"],
        "object_store_id": "62e07e08-9793-5eee-a1f7-2ed9305211de",
        "plaintext_byte_length": 122,
        "plaintext_sha256": EXPECTED_DOCUMENT_SHA256,
        "tenant_id": EXPECTED_XCHACHA_KATS["Eva"]["tenantId"],
    }
    allowed_attempts = scope.get("allowed_effect_attempts", [])
    allowed_attempts_valid = (
        isinstance(allowed_attempts, list) and len(allowed_attempts) == 2
    )
    expected_attempt_bindings = (
        (
            "effect-attempt/document/accepted",
            1,
            "baseline_generation_1",
        ),
        (
            "correction/effect-attempt/document",
            2,
            "correction_generation_2",
        ),
    )
    for binding, (fact_id, generation, scenario_scope) in zip(
        allowed_attempts, expected_attempt_bindings
    ):
        fact = fact_by_id.get(fact_id, {})
        row = fact.get("portable_record", {}).get("row", {})
        payload = row.get("payload", {})
        expected_binding = {
            "catalog_fact_id": fact_id,
            "catalog_fact_portable_record_sha256": fact.get("portable_record_sha256"),
            "contract_effect_attempt_id": payload.get("effect_attempt_id"),
            "generation": generation,
            "object_candidate_ciphertext_sha256": expected_scope_values[
                "complete_envelope_sha256"
            ],
            "object_candidate_envelope_byte_length": 553,
            "object_candidate_id": expected_scope_values["object_id"],
            "object_store_id": expected_scope_values["object_store_id"],
            "ordinal": 1,
            "portable_effect_attempt_id": row.get("effect_attempt_id"),
            "scenario_scope": scenario_scope,
        }
        if not (
            isinstance(binding, dict)
            and binding == expected_binding
            and row.get("capability_grant_payload", {}).get("generation") == generation
            and row.get("object_candidate_ciphertext_sha256")
            == expected_scope_values["complete_envelope_sha256"]
            and row.get("object_candidate_envelope_byte_length") == 553
            and row.get("object_candidate_id") == expected_scope_values["object_id"]
            and row.get("object_store_id") == expected_scope_values["object_store_id"]
            and row.get("ordinal") == 1
        ):
            allowed_attempts_valid = False
    _need(
        errors,
        set(scope)
        == {
            "allowed_effect_attempts",
            "canonical_aad_sha256",
            "complete_envelope_byte_length",
            "complete_envelope_sha256",
            "identity",
            "installation_id",
            "key_ref",
            "key_sha256",
            "object_id",
            "object_store_id",
            "plaintext_byte_length",
            "plaintext_sha256",
            "scope_match_rule",
            "tenant_id",
        }
        and all(scope.get(key) == value for key, value in expected_scope_values.items())
        and scope.get("scope_match_rule")
        == (
            "Before consumption, identity, tenant_id, installation_id, object_id, "
            "object_store_id, key_ref, key_sha256 and exactly one allowed effect-"
            "attempt binding MUST all match byte-for-byte; any mismatch fails closed "
            "before nonce release."
        )
        and allowed_attempts_valid,
        "schedule harness entropy fixture scope or effect-attempt binding is not exact",
    )

    trace = authority.get("durable_consumption_trace_gate", {})
    required_trace_fields = [
        "contract",
        "schema_version",
        "authority_sha256",
        "scenario_run_id",
        "consumption_ordinal",
        "identity",
        "tenant_id",
        "installation_id",
        "object_id",
        "object_store_id",
        "key_ref",
        "key_sha256",
        "effect_attempt_id",
        "nonce_sha256",
        "complete_envelope_sha256",
        "ledger_sequence",
        "committed_at",
        "ledger_commit_receipt_sha256",
    ]
    required_trace_values = {
        "complete_envelope_sha256": expected_scope_values["complete_envelope_sha256"],
        "consumption_ordinal": 1,
        "contract": "Eva4DHarnessEntropyConsumptionTrace/v1",
        "identity": "Eva",
        "installation_id": expected_scope_values["installation_id"],
        "key_ref": expected_scope_values["key_ref"],
        "key_sha256": expected_scope_values["key_sha256"],
        "nonce_sha256": EXPECTED_HARNESS_NONCE_SHA256,
        "object_id": expected_scope_values["object_id"],
        "object_store_id": expected_scope_values["object_store_id"],
        "schema_version": 1,
        "tenant_id": expected_scope_values["tenant_id"],
    }
    _need(
        errors,
        set(trace)
        == {
            "attempt_binding_rule",
            "authority_hash_binding_rule",
            "contract",
            "ledger_owner",
            "maximum_rows_per_uniqueness_key",
            "missing_duplicate_or_unflushed_trace_action",
            "portable_snapshot_row",
            "required_exact_values",
            "required_order",
            "required_trace_fields",
            "uniqueness_key",
        }
        and trace.get("contract") == "Eva4DHarnessEntropyConsumptionTrace/v1"
        and trace.get("ledger_owner") == "isolated_4d_schedule_harness"
        and trace.get("maximum_rows_per_uniqueness_key") == 1
        and trace.get("uniqueness_key") == ["authority_sha256", "scenario_run_id"]
        and trace.get("portable_snapshot_row") is False
        and trace.get("missing_duplicate_or_unflushed_trace_action")
        == "fail_closed_before_candidate_adoption"
        and trace.get("authority_hash_binding_rule")
        == ("trace.authority_sha256 MUST equal this object's authority_sha256 field")
        and trace.get("attempt_binding_rule")
        == (
            "trace.effect_attempt_id MUST equal exactly one exact_fixture_scope."
            "allowed_effect_attempts portable_effect_attempt_id for the current "
            "isolated scenario"
        )
        and trace.get("required_exact_values") == required_trace_values
        and trace.get("required_trace_fields") == required_trace_fields
        and trace.get("required_order")
        == [
            "reserve exactly one allowed effect attempt and bind its immutable AAD fields",
            "consume the one-shot exact nonce and produce the exact complete envelope",
            (
                "append and durably flush the consumption trace and obtain its ledger "
                "commit receipt"
            ),
            (
                "only after that durable receipt permit candidate adoption, selected-"
                "object insertion or ObjectRef selection"
            ),
        ],
        "schedule harness entropy durable trace, uniqueness or ordering gate is not exact",
    )

    forbidden = authority.get("forbidden_activation_and_shortcuts", {})
    forbidden_boolean_keys = {
        "candidate_adoption_before_durable_trace_allowed",
        "copy_known_answer_envelope_as_candidate_allowed",
        "default_bootstrap_import_allowed",
        "default_bootstrap_registration_allowed",
        "effect_attempt_state_reuse_across_isolated_runs_allowed",
        "environment_variable_activation_allowed",
        "file_or_service_configuration_activation_allowed",
        "found_same_without_this_run_consumption_trace_allowed",
        "non_fixture_installation_allowed",
        "preseed_cas_path_allowed",
        "preseed_complete_envelope_allowed",
        "preseed_object_ref_allowed",
        "production_dependency_graph_reachability_allowed",
        "production_import_allowed",
    }
    _need(
        errors,
        set(forbidden) == forbidden_boolean_keys | {"required_initial_state"}
        and all(forbidden.get(key) is False for key in forbidden_boolean_keys)
        and forbidden.get("required_initial_state")
        == (
            "No selected ObjectRef and no CAS entry for the exact object_id/"
            "ciphertext_sha256 may be preseeded; the isolated scenario must reach "
            "the selected object only through its bound document attempt and the "
            "traced one-shot encryption path."
        ),
        "schedule harness entropy production boundary or no-shortcut rule is not exact",
    )

    preservation = authority.get("portable_snapshot_preservation", {})
    snapshot_bindings = [
        {
            "canonical_snapshot_sha256": row.get("canonical_snapshot_sha256"),
            "snapshot_id": row.get("snapshot_id"),
        }
        for row in schedule.get("portableSnapshotAuthority", {}).get(
            "precomputedSnapshots", []
        )
        if isinstance(row, dict)
    ]
    expected_preservation = {
        "breaker_schedules_sha256": sha256_bytes(
            canonical_json_bytes(schedule.get("breakerSchedules"))
        ),
        "canonical_snapshot_binding_vector_sha256": sha256_bytes(
            canonical_json_bytes(snapshot_bindings)
        ),
        "concurrency_schedules_sha256": sha256_bytes(
            canonical_json_bytes(schedule.get("concurrencySchedules"))
        ),
        "count_vectors_sha256": sha256_bytes(
            canonical_json_bytes(schedule.get("countVectors"))
        ),
        "crash_schedules_sha256": sha256_bytes(
            canonical_json_bytes(schedule.get("crashSchedules"))
        ),
        "portable_snapshot_authority_sha256": sha256_bytes(
            canonical_json_bytes(schedule.get("portableSnapshotAuthority"))
        ),
        "snapshot_hash_mutation_allowed": False,
        "snapshot_instance_count": 31,
        "snapshot_record_or_count_mutation_allowed": False,
    }
    _need(
        errors,
        preservation == expected_preservation,
        "schedule harness entropy authority does not preserve all 31 snapshots",
    )

    bindings = authority.get("schedule_path_bindings", {})
    bindings_without_hash = {
        key: value
        for key, value in bindings.items()
        if key != "binding_manifest_sha256"
    }
    schedule_without_authority = {
        key: value
        for key, value in schedule.items()
        if key != "harnessEntropyInjectionAuthority"
    }
    scalar_pointers = _scalar_json_pointers(schedule_without_authority)
    ciphertext = expected_scope_values["complete_envelope_sha256"]
    ciphertext_paths = sorted(
        path for path, value in scalar_pointers.items() if value == ciphertext
    )
    manifest_paths = [
        path
        for path in ciphertext_paths
        if re.fullmatch(
            r"/portableSnapshotAuthority/precomputedSnapshots/[0-9]+/"
            r"object_manifest_records/[0-9]+/ciphertext_sha256",
            path,
        )
    ]
    delta_paths = [
        path
        for path in ciphertext_paths
        if re.fullmatch(
            r"/portableSnapshotAuthority/snapshotDefinitions/[0-9]+/delta/"
            r"selected_object_inserts/[0-9]+/(?:ciphertext|envelope)_sha256",
            path,
        )
    ]
    reference_maps_valid = True
    for key in (
        "effect_attempt_fact_reference_paths",
        "object_ref_fact_reference_paths",
    ):
        reference_map = bindings.get(key, {})
        if not isinstance(reference_map, dict):
            reference_maps_valid = False
            continue
        for fact_id, paths in reference_map.items():
            actual = sorted(
                path for path, value in scalar_pointers.items() if value == fact_id
            )
            if paths != actual:
                reference_maps_valid = False
    try:
        selected_object = _resolve_json_pointer(
            schedule_without_authority, bindings.get("selected_object_catalog_path")
        )
    except FreezeError:
        selected_object = None
    if isinstance(selected_object, dict):
        selected_without_hash = {
            key: value
            for key, value in selected_object.items()
            if key != "catalog_entry_sha256"
        }
    else:
        selected_without_hash = None
    baseline_object = fact_by_id.get("object/document-confirmation", {})
    correction_object = fact_by_id.get("correction/object/document", {})
    _need(
        errors,
        set(bindings)
        == {
            "baseline_object_ref_row_sha256",
            "binding_manifest_hash_rule",
            "binding_manifest_sha256",
            "binding_validation_rule",
            "correction_object_ref_row_sha256",
            "effect_attempt_fact_reference_paths",
            "object_ref_fact_reference_paths",
            "path_syntax",
            "precomputed_object_manifest_paths",
            "selected_ciphertext_occurrence_count",
            "selected_ciphertext_occurrence_paths",
            "selected_object_catalog_entry_sha256",
            "selected_object_catalog_path",
            "selected_object_delta_insert_paths",
        }
        and bindings.get("binding_manifest_hash_rule")
        == (
            "SHA-256 over EvaCanonicalJSON/v1 bytes of schedule_path_bindings "
            "excluding only binding_manifest_sha256"
        )
        and bindings.get("binding_manifest_sha256")
        == sha256_bytes(canonical_json_bytes(bindings_without_hash))
        == EXPECTED_HARNESS_ENTROPY_BINDING_MANIFEST_SHA256
        and bindings.get("path_syntax")
        == (
            "RFC 6901 JSON Pointer over the schedule artifact before adding "
            "harnessEntropyInjectionAuthority"
        )
        and bindings.get("binding_validation_rule")
        == (
            "Every listed pointer MUST resolve to its exact bound hash or fact ID, "
            "and every occurrence outside harnessEntropyInjectionAuthority of the "
            "selected ciphertext or either allowed attempt/object fact ID MUST "
            "appear exactly once in the corresponding exhaustive pointer set."
        )
        and bindings.get("selected_ciphertext_occurrence_count") == 33
        and bindings.get("selected_ciphertext_occurrence_paths") == ciphertext_paths
        and bindings.get("precomputed_object_manifest_paths") == manifest_paths
        and bindings.get("selected_object_delta_insert_paths") == delta_paths
        and reference_maps_valid
        and bindings.get("effect_attempt_fact_reference_paths", {}).keys()
        == {
            "correction/effect-attempt/document",
            "effect-attempt/document/accepted",
        }
        and bindings.get("object_ref_fact_reference_paths", {}).keys()
        == {"correction/object/document", "object/document-confirmation"}
        and isinstance(selected_without_hash, dict)
        and bindings.get("selected_object_catalog_entry_sha256")
        == selected_object.get("catalog_entry_sha256")
        == sha256_bytes(canonical_json_bytes(selected_without_hash))
        and bindings.get("baseline_object_ref_row_sha256")
        == baseline_object.get("portable_record", {}).get("row_sha256")
        and bindings.get("correction_object_ref_row_sha256")
        == correction_object.get("portable_record", {}).get("row_sha256"),
        "schedule harness entropy exhaustive path bindings are not exact",
    )


def _check_schedule_artifact(
    root: Path,
    schedule: dict[str, Any],
    schema: dict[str, Any],
    connector: dict[str, Any],
    model: dict[str, Any],
    profile: dict[str, Any] | None,
    errors: list[str],
) -> None:
    _need(
        errors,
        schedule.get("contract") == EXPECTED_ARTIFACT_CONTRACTS[SCHEDULE_ARTIFACT_PATH]
        and schedule.get("status")
        == "sealed_pre_runtime_fixture_with_exact_precomputed_portable_snapshots"
        and _all_strings_nfc(schedule),
        "schedule artifact contract, status or NFC form is not sealed",
    )
    source = schedule.get("authorityBindings", {}).get("sourceProfileAtBaseline", {})
    _need(
        errors,
        source.get("semanticCoreSha256") == EXPECTED_PROFILE_SEMANTIC_CORE_SHA256,
        "schedule source-profile semantic core is not exact",
    )
    _check_schedule_companions(schedule, schema, connector, model, errors)

    portable = schedule.get("portableSnapshotAuthority", {})
    seal = portable.get("validationSeal", {})
    _need(
        errors,
        portable.get("contract") == "Eva24DPortableSnapshotAuthority/v1"
        and portable.get("schemaVersion") == 1
        and portable.get("runtimeInferenceAllowed") is False
        and portable.get("postExecutionExpectedHashMutationAllowed") is False
        and portable.get("snapshotFamilyOrder") == EXPECTED_SNAPSHOT_FAMILIES
        and portable.get("snapshotInstanceOrder") == EXPECTED_SNAPSHOT_INSTANCES
        and seal
        == {
            "alternate_snapshot_count": 13,
            "base_snapshot_family_count": 18,
            "count_vector_length": 40,
            "duplicate_primary_key_errors": 0,
            "foreign_key_errors": 0,
            "portable_fact_count": 390,
            "portable_schema_validation_errors": 0,
            "record_set_vector_length": 39,
            "runtime_inference_required": False,
            "snapshot_instance_count": 31,
            "unique_constraint_errors": 0,
        },
        "schedule snapshot counts or no-runtime-inference seal are not exact",
    )

    schema_rows = (
        schema.get("portableExport", {})
        .get("generatedPortableRecordSchemas", {})
        .get("schemas", [])
    )
    table_rows = schema.get("schemaFamily", {}).get("tables", [])
    table_definitions = {
        row.get("table"): row for row in table_rows if isinstance(row, dict)
    }
    registry = _load_contract_schema_registry(root, schema, errors)
    portable_schemas: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(schema_rows):
        if not isinstance(row, dict):
            continue
        raw = _decode_standard_base64(row.get("canonicalBytesBase64"))
        decoded = (
            _load_embedded_canonical_json(
                raw, errors, f"schedule portable schema {index + 1}"
            )
            if raw is not None
            else None
        )
        if decoded is not None and isinstance(row.get("table"), str):
            portable_schemas[row["table"]] = decoded
            if isinstance(decoded.get("$id"), str):
                registry[decoded["$id"]] = decoded

    object_authority = (
        schema.get("portableExport", {})
        .get("objectManifest", {})
        .get("recordSchemaAuthority", {})
    )
    object_raw = _decode_standard_base64(object_authority.get("canonicalBytesBase64"))
    object_record_schema = (
        _load_embedded_canonical_json(
            object_raw, errors, "schedule object-manifest schema"
        )
        if object_raw is not None
        else None
    )

    catalog = portable.get("fixtureFactCatalog", {})
    facts = catalog.get("facts", [])
    fact_by_id = {row.get("fact_id"): row for row in facts if isinstance(row, dict)}
    catalog_valid = (
        catalog.get("contract") == "Eva4DPortableFixtureFactCatalog/v1"
        and catalog.get("schema_version") == 1
        and catalog.get("fact_count") == 390
        and isinstance(facts, list)
        and len(facts) == 390
        and len(fact_by_id) == 390
    )
    connector_bindings = {
        row.get("logicalBindingKey"): row
        for row in connector.get("concreteBindings", [])
        if isinstance(row, dict) and row.get("identityRun") == "Eva"
    }
    redacted_binding_count = 0
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict):
            catalog_valid = False
            continue
        fact_id = fact.get("fact_id")
        table = fact.get("table")
        wrapper = fact.get("portable_record")
        definition = table_definitions.get(table)
        portable_schema = portable_schemas.get(table)
        if not (
            isinstance(fact_id, str)
            and isinstance(table, str)
            and isinstance(wrapper, dict)
            and isinstance(definition, dict)
            and isinstance(portable_schema, dict)
        ):
            catalog_valid = False
            continue
        row = wrapper.get("row")
        primary_key = wrapper.get("primary_key")
        entry_without_hash = {
            key: value for key, value in fact.items() if key != "catalog_entry_sha256"
        }
        if not (
            fact.get("export_ordinal") == definition.get("exportOrdinal")
            and fact.get("primary_key") == primary_key
            and wrapper.get("schema_version") == 1
            and wrapper.get("source_table") == table
            and wrapper.get("contract")
            == definition.get("export", {}).get("recordContract")
            and isinstance(row, dict)
            and isinstance(primary_key, dict)
            and primary_key
            == {key: row.get(key) for key in definition.get("primaryKey", [])}
            and wrapper.get("row_sha256") == sha256_bytes(canonical_json_bytes(row))
            and fact.get("portable_record_sha256")
            == sha256_bytes(canonical_json_bytes(wrapper))
            and fact.get("catalog_entry_sha256")
            == sha256_bytes(canonical_json_bytes(entry_without_hash))
            and _json_schema_matches(
                wrapper, portable_schema, portable_schema, registry
            )
        ):
            catalog_valid = False
        if table == "eva_core.connector_bindings" and isinstance(row, dict):
            binding_key = fact_id.removeprefix("binding/")
            source_binding = connector_bindings.get(binding_key, {})
            source_payload = source_binding.get("payload", {})
            redacted_payload = dict(source_payload)
            credential_reference = redacted_payload.get("credential_reference")
            redacted_payload.update(
                {
                    "contract": "EvaRedactedConnectorBinding/v1",
                    "credential_reauthorization_required": True,
                    "credential_reference": None,
                    "credential_reference_sha256": sha256_bytes(
                        credential_reference.encode("utf-8")
                    )
                    if isinstance(credential_reference, str)
                    else None,
                    "source_contract": "ConnectorBinding/v1",
                }
            )
            if not (
                source_binding.get("canonicalSha256")
                == row.get("source_payload_sha256")
                == sha256_bytes(canonical_json_bytes(source_payload))
                and row.get("payload") == redacted_payload
                and row.get("confirmed_operations")
                == source_payload.get("confirmed_operations")
                and row.get("confirmed_scopes")
                == source_payload.get("confirmed_scopes")
                and row.get("redacted_payload_sha256")
                == sha256_bytes(canonical_json_bytes(redacted_payload))
                and row.get("credential_reauthorization_required") is True
            ):
                catalog_valid = False
            redacted_binding_count += 1

    happy_observed_read_fact_ids = (
        "read-attempt/mail-search",
        "read-attempt/mail-read",
        "read-attempt/erp",
        "read-attempt/availability",
        "read-attempt/calendar-verify",
        "read-attempt/document-verify",
        "read-attempt/confirmation-verify",
    )
    _need(
        errors,
        all(
            fact_by_id.get(fact_id, {}).get("table") == "eva_effects.read_attempts"
            and fact_by_id[fact_id]
            .get("portable_record", {})
            .get("row", {})
            .get("state")
            == "observed"
            and fact_by_id[fact_id]
            .get("portable_record", {})
            .get("row", {})
            .get("revision")
            == 2
            and isinstance(
                fact_by_id[fact_id]
                .get("portable_record", {})
                .get("row", {})
                .get("response_sha256"),
                str,
            )
            for fact_id in happy_observed_read_fact_ids
        ),
        "schedule seven happy successful read attempts are not exact revision-2 "
        "observed fences",
    )
    catalog_hashes = [
        row.get("catalog_entry_sha256") for row in facts if isinstance(row, dict)
    ]
    crosswalk = catalog.get("logical_relational_id_crosswalk", [])
    try:
        namespace = uuid.UUID(catalog.get("uuid_namespace", ""))
    except (ValueError, AttributeError):
        namespace = None
    crosswalk_valid = (
        namespace is not None
        and isinstance(crosswalk, list)
        and len(crosswalk) == 406
        and [row.get("logical_id") for row in crosswalk if isinstance(row, dict)]
        == sorted(row.get("logical_id") for row in crosswalk if isinstance(row, dict))
        and len({row.get("logical_id") for row in crosswalk if isinstance(row, dict)})
        == 406
        and len(
            {row.get("portable_uuid") for row in crosswalk if isinstance(row, dict)}
        )
        == 406
        and all(
            isinstance(row, dict)
            and str(uuid.uuid5(namespace, row.get("logical_id", "")))
            == row.get("portable_uuid")
            for row in crosswalk
        )
    )
    _need(
        errors,
        catalog_valid
        and redacted_binding_count == 6
        and catalog.get("catalog_sha256")
        == sha256_bytes(canonical_json_bytes(catalog_hashes))
        and crosswalk_valid,
        "schedule 390-fact catalog, wrappers or portable schema validation is not exact",
    )
    _check_harness_entropy_authority(schedule, profile, fact_by_id, errors)

    selected = portable.get("selectedEncryptedObjects", {})
    selected_rows = selected.get("objects", [])
    selected_by_ciphertext: dict[str, dict[str, Any]] = {}
    selected_valid = (
        selected.get("contract") == "Eva4DSelectedEncryptedObjectCatalog/v1"
        and selected.get("schema_version") == 1
        and selected.get("object_count") == 1
        and isinstance(selected_rows, list)
        and len(selected_rows) == 1
    )
    for item in selected_rows if isinstance(selected_rows, list) else []:
        if not isinstance(item, dict):
            selected_valid = False
            continue
        raw = _decode_standard_base64(item.get("complete_envelope_base64"))
        try:
            encoded_hex = bytes.fromhex(item.get("complete_envelope_hex", ""))
        except (TypeError, ValueError):
            encoded_hex = b""
        without_hash = {
            key: value for key, value in item.items() if key != "catalog_entry_sha256"
        }
        manifest_record = item.get("manifest_record", {})
        if not (
            raw is not None
            and raw == encoded_hex
            and raw.startswith(b"EVA2XO01")
            and item.get("complete_envelope_byte_length") == len(raw)
            and item.get("ciphertext_sha256") == sha256_bytes(raw)
            and item.get("catalog_entry_sha256")
            == sha256_bytes(canonical_json_bytes(without_hash))
            and isinstance(manifest_record, dict)
            and manifest_record.get("ciphertext_sha256")
            == item.get("ciphertext_sha256")
        ):
            selected_valid = False
        if isinstance(item.get("ciphertext_sha256"), str):
            selected_by_ciphertext[item["ciphertext_sha256"]] = item
    _need(
        errors,
        selected_valid and len(selected_by_ciphertext) == 1,
        "schedule selected encrypted-object catalog is not exact",
    )

    layout = portable.get("countVectorLayout", [])
    layout_valid = (
        isinstance(layout, list)
        and len(layout) == 40
        and [row.get("index") for row in layout if isinstance(row, dict)]
        == list(range(40))
        and [row.get("exportOrdinal") for row in layout if isinstance(row, dict)]
        == list(range(1, 41))
        and [row.get("table") for row in layout[:-1] if isinstance(row, dict)]
        == [row.get("table") for row in table_rows if isinstance(row, dict)]
        and layout[-1]
        == {
            "exportOrdinal": 40,
            "index": 39,
            "table": "encrypted_objects",
        }
    )
    definitions = portable.get("snapshotDefinitions", [])
    snapshots = portable.get("precomputedSnapshots", [])
    definition_by_id = {
        row.get("snapshot_id"): row for row in definitions if isinstance(row, dict)
    }
    snapshot_by_id = {
        row.get("snapshot_id"): row for row in snapshots if isinstance(row, dict)
    }
    structure_valid = (
        layout_valid
        and isinstance(definitions, list)
        and isinstance(snapshots, list)
        and len(definitions) == len(snapshots) == 31
        and list(definition_by_id) == EXPECTED_SNAPSHOT_INSTANCES
        and list(snapshot_by_id) == EXPECTED_SNAPSHOT_INSTANCES
    )
    materialized_facts: dict[str, dict[tuple[str, bytes], str]] = {}
    materialized_objects: dict[str, dict[str, dict[str, Any]]] = {}
    snapshots_valid = structure_valid
    full_cas_profile_counts: dict[str, int] = {}
    full_cas_edges: set[tuple[str, str, str]] = set()
    full_cas_replacements_valid = True

    def fact_key(fact: dict[str, Any]) -> tuple[str, bytes]:
        return (
            fact.get("table", ""),
            canonical_json_bytes(fact.get("primary_key")),
        )

    def exact_fact_reference(reference: Any, fact_id: Any) -> bool:
        fact = fact_by_id.get(fact_id)
        return (
            isinstance(reference, dict)
            and isinstance(fact, dict)
            and reference.get("table") == fact.get("table")
            and reference.get("primary_key") == fact.get("primary_key")
        )

    for position, snapshot_id in enumerate(EXPECTED_SNAPSHOT_INSTANCES):
        definition = definition_by_id.get(snapshot_id, {})
        snapshot = snapshot_by_id.get(snapshot_id, {})
        base_id = definition.get("base_snapshot_id")
        if base_id is None:
            state: dict[tuple[str, bytes], str] = {}
            object_state: dict[str, dict[str, Any]] = {}
        elif base_id in materialized_facts:
            state = dict(materialized_facts[base_id])
            object_state = dict(materialized_objects[base_id])
        else:
            snapshots_valid = False
            state = {}
            object_state = {}
        delta = definition.get("delta", {})
        if not (
            set(definition)
            == {
                "base_snapshot_id",
                "delta",
                "expected_encrypted_object_count",
                "expected_fact_count",
                "snapshot_family",
                "snapshot_id",
            }
            and definition.get("snapshot_id") == snapshot_id
            and definition.get("snapshot_family") in EXPECTED_SNAPSHOT_FAMILIES
            and set(delta)
            == {
                "deletes",
                "full_cas_replacements",
                "inserts",
                "selected_object_deletes",
                "selected_object_inserts",
            }
        ):
            snapshots_valid = False
        for reference in delta.get("deletes", []):
            fact_id = reference.get("expected_base_fact_id")
            fact = fact_by_id.get(fact_id, {})
            key = fact_key(fact)
            if (
                not exact_fact_reference(reference, fact_id)
                or state.get(key) != fact_id
            ):
                snapshots_valid = False
            else:
                del state[key]
        for reference in delta.get("full_cas_replacements", []):
            if not isinstance(reference, dict):
                snapshots_valid = False
                full_cas_replacements_valid = False
                continue
            old_id = reference.get("expected_base_fact_id")
            new_id = reference.get("replacement_fact_id")
            old_fact = fact_by_id.get(old_id, {})
            new_fact = fact_by_id.get(new_id, {})
            old_key = fact_key(old_fact)
            new_key = fact_key(new_fact)
            exact_replacement = (
                exact_fact_reference(reference, old_id)
                and exact_fact_reference(reference, new_id)
                and old_key == new_key
                and state.get(old_key) == old_id
            )
            table = reference.get("table")
            cas_profile = table_definitions.get(table, {}).get("cas")
            if isinstance(cas_profile, str):
                full_cas_profile_counts[cas_profile] = (
                    full_cas_profile_counts.get(cas_profile, 0) + 1
                )
            if (
                isinstance(table, str)
                and isinstance(old_id, str)
                and isinstance(new_id, str)
            ):
                full_cas_edges.add((table, old_id, new_id))
            old_row = old_fact.get("portable_record", {}).get("row", {})
            new_row = new_fact.get("portable_record", {}).get("row", {})
            if isinstance(cas_profile, str) and cas_profile.startswith("revisionCas"):
                old_revision = old_row.get("revision")
                new_revision = new_row.get("revision")
                exact_replacement = (
                    exact_replacement
                    and type(old_revision) is int
                    and type(new_revision) is int
                    and new_revision == old_revision + 1
                )
            elif cas_profile != "currentSwap":
                exact_replacement = False
            if not exact_replacement:
                snapshots_valid = False
                full_cas_replacements_valid = False
            else:
                state[old_key] = new_id
        for reference in delta.get("inserts", []):
            fact_id = reference.get("fact_id")
            fact = fact_by_id.get(fact_id, {})
            key = fact_key(fact)
            if not exact_fact_reference(reference, fact_id) or key in state:
                snapshots_valid = False
            else:
                state[key] = fact_id
        for reference in delta.get("selected_object_deletes", []):
            digest = reference.get("ciphertext_sha256")
            if digest not in object_state:
                snapshots_valid = False
            else:
                del object_state[digest]
        for reference in delta.get("selected_object_inserts", []):
            digest = reference.get("ciphertext_sha256")
            selected_object = selected_by_ciphertext.get(digest)
            if not (
                isinstance(selected_object, dict)
                and digest not in object_state
                and reference.get("envelope_sha256") == digest
                and reference.get("envelope_byte_length")
                == selected_object.get("complete_envelope_byte_length")
            ):
                snapshots_valid = False
            else:
                object_state[digest] = selected_object
        materialized_facts[snapshot_id] = state
        materialized_objects[snapshot_id] = object_state

        record_sets = snapshot.get("record_sets", [])
        actual_record_sets: list[dict[str, Any]] = []
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        state_ids = set(state.values())
        record_ids: list[str] = []
        if not isinstance(record_sets, list) or len(record_sets) != 39:
            snapshots_valid = False
            record_sets = []
        for ordinal, (table_definition, schema_summary) in enumerate(
            zip(table_rows, schema_rows), start=1
        ):
            table = table_definition.get("table")
            matching = [row for row in record_sets if row.get("table") == table]
            if len(matching) != 1:
                snapshots_valid = False
                continue
            record_set = matching[0]
            ids = [
                fact_id
                for fact_id in state_ids
                if fact_by_id.get(fact_id, {}).get("table") == table
            ]
            primary_key_order = table_definition.get("primaryKey", [])
            ids.sort(
                key=lambda fact_id: tuple(
                    str(fact_by_id[fact_id]["primary_key"].get(key))
                    for key in primary_key_order
                )
            )
            record_ids.extend(ids)
            jsonl = b"".join(
                canonical_json_bytes(fact_by_id[fact_id]["portable_record"]) + b"\n"
                for fact_id in ids
            )
            expected_record_set = {
                "exportOrdinal": ordinal,
                "jsonl_sha256": sha256_bytes(jsonl),
                "record_count": len(ids),
                "record_fact_ids": ids,
                "record_schema_sha256": schema_summary.get("record_schema_sha256"),
                "table": table,
            }
            if record_set != expected_record_set:
                snapshots_valid = False
            actual_record_sets.append(expected_record_set)
            rows_by_table[table] = [
                fact_by_id[fact_id]["portable_record"]["row"] for fact_id in ids
            ]
        if set(record_ids) != state_ids or len(record_ids) != len(state_ids):
            snapshots_valid = False
        record_vector = [
            {key: value for key, value in row.items() if key != "record_fact_ids"}
            for row in actual_record_sets
        ]
        count_vector = [row["record_count"] for row in actual_record_sets] + [
            len(object_state)
        ]

        object_records: list[dict[str, Any]] = []
        object_ref_rows = rows_by_table.get("eva_evidence.object_refs", [])
        for digest in sorted(object_state):
            item = object_state[digest]
            candidates = [
                row for row in object_ref_rows if row.get("ciphertext_sha256") == digest
            ]
            if len(candidates) != 1:
                snapshots_valid = False
                continue
            object_ref = candidates[0]
            manifest_record = dict(item.get("manifest_record", {}))
            manifest_record["object_ref_row_sha256"] = sha256_bytes(
                canonical_json_bytes(object_ref)
            )
            if not (
                manifest_record.get("object_id") == object_ref.get("object_id")
                and manifest_record.get("object_store_id")
                == object_ref.get("object_store_id")
                and manifest_record.get("tenant_id") == object_ref.get("tenant_id")
                and manifest_record.get("plaintext_sha256")
                == object_ref.get("plaintext_sha256")
                and manifest_record.get("envelope_byte_length")
                == object_ref.get("envelope_byte_length")
                and manifest_record.get("wrapped_key_ref")
                == object_ref.get("wrapped_key_ref")
                and (
                    object_record_schema is None
                    or _json_schema_matches(
                        manifest_record,
                        object_record_schema,
                        object_record_schema,
                        registry,
                    )
                )
            ):
                snapshots_valid = False
            object_records.append(manifest_record)
        object_jsonl = b"".join(
            canonical_json_bytes(row) + b"\n" for row in object_records
        )
        object_hash = sha256_bytes(object_jsonl)
        snapshot_projection = {
            "snapshot_family": snapshot.get("snapshot_family"),
            "source_installation_id": snapshot.get("source_installation_id"),
            "count_vector": count_vector,
            "record_set_vector": record_vector,
            "object_manifest_sha256": object_hash,
            "encrypted_object_count": len(object_state),
        }
        if not (
            snapshot.get("snapshot_id") == snapshot_id
            and snapshot.get("snapshot_family") == definition.get("snapshot_family")
            and snapshot.get("source_installation_id")
            == "0a44a85c-4bbb-5330-8803-60fab86e5a94"
            and snapshot.get("count_vector") == count_vector
            and len(count_vector) == 40
            and snapshot.get("record_sets") == actual_record_sets
            and snapshot.get("record_set_vector") == record_vector
            and len(record_vector) == 39
            and snapshot.get("object_manifest_records") == object_records
            and snapshot.get("object_manifest_jsonl_sha256") == object_hash
            and snapshot.get("object_manifest_sha256") == object_hash
            and snapshot.get("encrypted_object_count") == len(object_state)
            and definition.get("expected_fact_count") == len(state)
            and definition.get("expected_encrypted_object_count") == len(object_state)
            and snapshot.get("canonical_snapshot_sha256")
            == sha256_bytes(canonical_json_bytes(snapshot_projection))
            and _check_snapshot_relations(rows_by_table, table_definitions)
        ):
            snapshots_valid = False
    _need(
        errors,
        full_cas_replacements_valid
        and full_cas_profile_counts
        == {
            "currentSwap": 5,
            "revisionCas": 95,
            "revisionCas plus ack_claim_revision CAS": 2,
        }
        and len(full_cas_edges) == 72,
        "schedule full CAS replacements are not the exact ordered +1 revisionCas chains or sealed currentSwap set",
    )
    _need(
        errors,
        snapshots_valid,
        "schedule 31 snapshot deltas, 39 record sets, count40, relations or object manifests are not exact",
    )

    count_vectors = schedule.get("countVectors", {})
    count_vectors_valid = isinstance(count_vectors, dict) and set(count_vectors) == set(
        EXPECTED_SNAPSHOT_INSTANCES
    )
    for snapshot_id in EXPECTED_SNAPSHOT_INSTANCES:
        snapshot = snapshot_by_id.get(snapshot_id, {})
        vector = count_vectors.get(snapshot_id, {})
        if not (
            vector.get("values") == snapshot.get("count_vector")
            and vector.get("canonicalSha256")
            == sha256_bytes(canonical_json_bytes(snapshot.get("count_vector")))
            and vector.get("canonicalSnapshotSha256")
            == snapshot.get("canonical_snapshot_sha256")
        ):
            count_vectors_valid = False
    _need(
        errors,
        count_vectors_valid,
        "schedule top-level count-vector bindings are not exact",
    )

    schedule_references_valid = True

    def walk_snapshot_oracles(value: Any) -> None:
        nonlocal schedule_references_valid
        if isinstance(value, dict):
            if "snapshot_id" in value:
                snapshot = snapshot_by_id.get(value.get("snapshot_id"))
                if not isinstance(snapshot, dict):
                    schedule_references_valid = False
                else:
                    expected_fields = {
                        "snapshot_family": snapshot.get("snapshot_family"),
                        "canonical_snapshot_sha256": snapshot.get(
                            "canonical_snapshot_sha256"
                        ),
                        "count_vector": snapshot.get("count_vector"),
                        "count_vector_sha256": sha256_bytes(
                            canonical_json_bytes(snapshot.get("count_vector"))
                        ),
                        "record_set_vector_sha256": sha256_bytes(
                            canonical_json_bytes(snapshot.get("record_set_vector"))
                        ),
                        "object_manifest_sha256": snapshot.get(
                            "object_manifest_sha256"
                        ),
                    }
                    if any(
                        key in value and value.get(key) != expected
                        for key, expected in expected_fields.items()
                    ):
                        schedule_references_valid = False
            for item in value.values():
                walk_snapshot_oracles(item)
        elif isinstance(value, list):
            for item in value:
                walk_snapshot_oracles(item)

    schedule_surfaces = {
        key: schedule.get(key)
        for key in (
            "concurrencySchedules",
            "breakerSchedules",
            "crashSchedules",
            "commonTerminalOracle",
        )
    }
    walk_snapshot_oracles(schedule_surfaces)
    snapshot_reference_keys = {
        "preCrashCountVector",
        "postRecoveryCountVector",
        "initialCountVector",
        "countVector",
        "portableTerminalSnapshot",
    }

    def walk_named_snapshot_references(value: Any) -> None:
        nonlocal schedule_references_valid
        if isinstance(value, dict):
            for key, item in value.items():
                if key in snapshot_reference_keys and isinstance(item, str):
                    if item not in snapshot_by_id:
                        schedule_references_valid = False
                if key == "terminal" and isinstance(item, str) and "." in item:
                    if item not in snapshot_by_id:
                        schedule_references_valid = False
                walk_named_snapshot_references(item)
        elif isinstance(value, list):
            for item in value:
                walk_named_snapshot_references(item)

    walk_named_snapshot_references(schedule_surfaces)
    fact_reference_count = 0

    def walk_fact_references(value: Any, path: str) -> None:
        nonlocal fact_reference_count, schedule_references_valid
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if isinstance(item, str) and item in fact_by_id:
                    fact_reference_count += 1
                if isinstance(item, list):
                    fact_reference_count += sum(
                        isinstance(entry, str) and entry in fact_by_id for entry in item
                    )
                is_fact_reference = (
                    "fact_id" in key.lower()
                    or key in {"expected_base_fact_id", "replacement_fact_id"}
                ) and ".primary_key" not in path
                if is_fact_reference and isinstance(item, str):
                    if item not in fact_by_id:
                        schedule_references_valid = False
                if is_fact_reference and isinstance(item, list):
                    if any(entry not in fact_by_id for entry in item):
                        schedule_references_valid = False
                walk_fact_references(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk_fact_references(item, f"{path}[{index}]")

    for key, value in schedule.items():
        if key not in {
            "portableSnapshotAuthority",
            "harnessEntropyInjectionAuthority",
        }:
            walk_fact_references(value, f"$.{key}")
    for key, value in portable.items():
        if key != "fixtureFactCatalog":
            walk_fact_references(value, f"$.portableSnapshotAuthority.{key}")
    _need(
        errors,
        schedule_references_valid and fact_reference_count == 4690,
        "schedule fact or snapshot references do not all resolve exactly",
    )

    crash_schedules = schedule.get("crashSchedules", [])
    profile_points = [
        row.get("crashPoint")
        for row in (profile or {}).get("crashOracles", [])
        if isinstance(row, dict)
    ]
    schedule_points = [
        row.get("profileCrashPoint") for row in crash_schedules if isinstance(row, dict)
    ]
    expected_points = {point: 1 for point in profile_points}
    if "after model response and before response commit" in expected_points:
        expected_points["after model response and before response commit"] = 3
    actual_points = {
        point: schedule_points.count(point) for point in set(schedule_points)
    }
    _need(
        errors,
        isinstance(crash_schedules, list)
        and len(crash_schedules) == 17
        and expected_points == actual_points,
        "schedule crash coverage does not exactly cover the 15 profile points",
    )

    c13 = next(
        (
            row
            for row in crash_schedules
            if isinstance(row, dict)
            and row.get("crashId") == "C13-after-outcome-accept-before-receipt"
        ),
        {},
    )
    c13_authority = c13.get("reofferAuthority", {})
    c13_fact_id = c13_authority.get("ordinal_2_effect_attempt_fact_id")
    c13_fact = fact_by_id.get(c13_fact_id, {}).get("portable_record", {}).get("row", {})
    first_fact = (
        fact_by_id.get("effect-attempt/outcome/accepted", {})
        .get("portable_record", {})
        .get("row", {})
    )
    c13_definition = definition_by_id.get("terminal.c13", {})
    c13_delta = c13_definition.get("delta", {})
    connector_c13 = connector.get("scenarios", {}).get("c13ReofferEva", {})
    connector_c13_ledger = connector_c13.get("externalCallLedger", [])
    c13_delivery = (
        fact_by_id.get(c13_authority.get("delivery_fact_id"), {})
        .get("portable_record", {})
        .get("row", {})
    )
    _need(
        errors,
        c13.get("postRecoveryCallDelta")
        == {
            "businessConnector": 0,
            "model": 0,
            "newProviderMutation": 0,
            "outcomeReoffer": 1,
        }
        and c13.get("preCrashCountVector") == "outcomeInFlight"
        and c13.get("postRecoveryCountVector") == "terminal.c13"
        and c13_fact_id == "effect-attempt/outcome/c13-reoffer-ordinal2"
        and c13_definition.get("base_snapshot_id") == "terminal"
        and c13_delta.get("inserts")
        == [
            {
                "fact_id": c13_fact_id,
                "primary_key": fact_by_id.get(c13_fact_id, {}).get("primary_key"),
                "table": "eva_effects.effect_attempts",
            }
        ]
        and all(
            c13_delta.get(key) == []
            for key in (
                "deletes",
                "full_cas_replacements",
                "selected_object_deletes",
                "selected_object_inserts",
            )
        )
        and c13_fact.get("ordinal") == 2
        and first_fact.get("ordinal") == 1
        and c13_fact.get("effect_intent_id") == first_fact.get("effect_intent_id")
        and c13_fact.get("capability_grant_id") != first_fact.get("capability_grant_id")
        and c13_fact.get("credential_grant_id") != first_fact.get("credential_grant_id")
        and c13_authority.get("delivery_id")
        == c13_delivery.get("intent_payload", {}).get("delivery_id")
        and c13_authority.get("offer_payload_sha256")
        == c13_delivery.get("offer_payload_sha256")
        and c13_authority.get("receipt_payload_sha256")
        == c13_delivery.get("receipt_payload_sha256")
        and c13_authority.get("ack_challenge_sha256")
        == c13_delivery.get("ack_challenge_sha256")
        and c13_authority.get("wire_sha256") == c13_delivery.get("offer_wire_sha256")
        and len(connector_c13_ledger) == 12
        and not any("reconcile" in call for call in connector_c13.get("calls", []))
        and connector_c13_ledger[-1].get("providerMutationDelta") == 0
        and connector_c13_ledger[-2].get("providerMutationDelta") == 1,
        "schedule C13 exact ordinal-2 re-offer or no-reconcile authority is not exact",
    )

    correction_schedule = next(
        (
            row
            for row in schedule.get("concurrencySchedules", [])
            if isinstance(row, dict)
            and row.get("scheduleId") == "correction-before-first-effect"
        ),
        {},
    )
    correction_oracle = correction_schedule.get("oracle", {})
    correction_gen1 = correction_oracle.get("generation1", {})
    correction_gen2 = correction_oracle.get("generation2", {})
    correction_terminal_state = materialized_facts.get("terminal.correction", {})
    correction_terminal_ids = set(correction_terminal_state.values())
    correction_gen1_state = materialized_facts.get("planCommitted.correctionGen1", {})
    correction_gen1_ids = set(correction_gen1_state.values())

    def rows_for(fact_ids: set[str], table: str) -> list[dict[str, Any]]:
        return [
            fact_by_id[fact_id]["portable_record"]["row"]
            for fact_id in fact_ids
            if fact_by_id.get(fact_id, {}).get("table") == table
        ]

    generation_rows = rows_for(correction_terminal_ids, "eva_core.episode_generations")
    episode_rows = rows_for(correction_terminal_ids, "eva_core.episodes")
    plan_rows = rows_for(correction_terminal_ids, "eva_core.plans")
    model_rows = rows_for(correction_terminal_ids, "eva_core.model_attempts")
    generation_bound_tables = (
        "eva_effects.read_intents",
        "eva_effects.effect_intents",
        "eva_effects.deliveries",
    )
    all_terminal_generation_bound_rows = [
        row
        for table in generation_bound_tables
        for row in rows_for(correction_terminal_ids, table)
    ]
    connector_correction_scenario = connector.get("scenarios", {}).get(
        "correctionBeforeFirstEffect", {}
    )
    correction_authority = correction_schedule.get("authority", {})
    correction_object = fact_by_id.get(
        correction_authority.get("object_ref_fact_id"), {}
    )
    correction_delivery = fact_by_id.get(
        correction_authority.get("outcome_delivery_fact_id"), {}
    )
    _need(
        errors,
        correction_schedule.get("caseId") == "correction-before-first-effect"
        and correction_schedule.get("snapshotOracle", {})
        .get("generation_1_plan_committed_before_every_read_or_effect", {})
        .get("snapshot_id")
        == "planCommitted.correctionGen1"
        and correction_schedule.get("snapshotOracle", {})
        .get("generation_2_terminal", {})
        .get("snapshot_id")
        == "terminal.correction"
        and correction_gen1
        == {
            "connectorOperationInvocations": 0,
            "deliveries": 0,
            "effectAttempts": 0,
            "effectIntents": 0,
            "effectReadbacks": 0,
            "effectReceipts": 0,
            "modelAttempts": 1,
            "planSteps": 11,
            "plannedSlot": "2026-08-26T14:00:00+02:00",
            "plans": 1,
            "providerMutations": 0,
            "readAttempts": 0,
            "readIntents": 0,
            "readObservations": 0,
            "state": "superseded",
        }
        and correction_gen2.get("selectedSlot") == "2026-08-26T15:00:00+02:00"
        and correction_gen2.get("connectorOperationInvocations") == 11
        and correction_gen2.get("providerMutations") == 4
        and correction_gen2.get("modelAttempts") == 3
        and correction_gen2.get("readIntents")
        == correction_gen2.get("readAttempts")
        == correction_gen2.get("readObservations")
        == 7
        and correction_gen2.get("effectIntents")
        == correction_gen2.get("effectAttempts")
        == correction_gen2.get("effectReceipts")
        == 4
        and correction_gen2.get("effectReadbacks") == 3
        and correction_gen2.get("deliveries") == 2
        and len(rows_for(correction_gen1_ids, "eva_effects.read_intents")) == 0
        and len(rows_for(correction_gen1_ids, "eva_effects.effect_intents")) == 0
        and len(rows_for(correction_gen1_ids, "eva_effects.deliveries")) == 0
        and all(
            row.get("generation") == 2 for row in all_terminal_generation_bound_rows
        )
        and sorted((row.get("generation"), row.get("state")) for row in generation_rows)
        == [(1, "superseded"), (2, "sealed")]
        and len(episode_rows) == 1
        and episode_rows[0].get("current_generation") == 2
        and episode_rows[0].get("supersedes_generation") == 1
        and episode_rows[0].get("state") == "completed"
        and sorted((row.get("generation"), row.get("state")) for row in plan_rows)
        == [(1, "superseded"), (2, "completed")]
        and sorted((row.get("generation"), row.get("purpose")) for row in model_rows)
        == [
            (1, "plan"),
            (2, "document_draft"),
            (2, "plan"),
            (2, "reply"),
        ]
        and correction_authority.get("object_ref_portable_record_sha256")
        == correction_object.get("portable_record_sha256")
        and correction_authority.get("outcome_delivery_portable_record_sha256")
        == correction_delivery.get("portable_record_sha256")
        and correction_oracle.get("visiblePresenceReply", {}).get("contains") == "15.00"
        and "14.00"
        in correction_oracle.get("visiblePresenceReply", {}).get("forbidden", [])
        and connector_correction_scenario.get("expected", {}).get(
            "generation1ConnectorCalls"
        )
        == 0
        and connector_correction_scenario.get("expected", {}).get(
            "generation2ConnectorCalls"
        )
        == 11,
        "schedule correction generation-1 supersession or generation-2 terminal authority is not exact",
    )


def _check_sealed_packs(
    packs: dict[str, Any], vertical: dict[str, Any], errors: list[str]
) -> None:
    _exact_keys(
        errors,
        packs,
        {
            "canonicalization",
            "fixtureSigningKey",
            "identityPackSigningRule",
            "policyPackSigningRule",
            "identityPacks",
            "policyPack",
            "hostBindingRule",
            "identityRunIsolation",
            "identitySwapRule",
            "fixtureReplyVectorRule",
            "functionalIdentityConstantsAllowedInExecutable",
            "packActivationBeforeSignatureAndHashVerificationAllowed",
        },
        "sealed pack fixtures",
    )
    signing_key = packs.get("fixtureSigningKey", {})
    public_key = _decode_unpadded_base64url(signing_key.get("rawPublicKeyBase64url"))
    _need(
        errors,
        signing_key
        == {
            "algorithm": "Ed25519",
            "rawPublicKeyBase64url": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
            "rawPublicKeySha256": "56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c",
            "classification": "public_test_fixture_not_a_secret",
            "privateKeyBytesStoredInPackProfileRuntimeEvidenceOrModelContext": False,
            "checkerCryptographicSignatureValidationPerformed": False,
            "runtimeImplementationMustUseMaintainedEd25519Verifier": True,
        }
        and public_key is not None
        and len(public_key) == 32
        and sha256_bytes(public_key) == signing_key.get("rawPublicKeySha256"),
        "fixture pack signing-key binding is not exact",
    )
    _need(
        errors,
        "only signature omitted" in packs.get("identityPackSigningRule", "")
        and "source_sha256 and signature omitted"
        in packs.get("identityPackSigningRule", "")
        and "only release_signature_sha256 omitted"
        in packs.get("policyPackSigningRule", "")
        and "source_sha256 and release_signature_sha256 omitted"
        in packs.get("policyPackSigningRule", ""),
        "pack signing and source-hash inputs are not exact",
    )

    identity_payload_keys = {
        "schema_version",
        "contract",
        "identity_id",
        "pack_version",
        "state",
        "display_name",
        "pronouns",
        "language",
        "voice_style",
        "values",
        "temperament",
        "humour",
        "conversation_boundaries",
        "channel_presentations",
        "privacy_preferences",
        "consent_preferences",
        "initiative_preferences",
        "unknown_relation_preferences",
        "issuer",
        "source_sha256",
        "signature_algorithm",
        "signature",
        "issued_at",
    }
    identity_rows = packs.get("identityPacks", [])
    _need(
        errors,
        len(identity_rows) == 2,
        "exactly two identity pack fixtures are required",
    )
    seen_names: list[str] = []
    for row in identity_rows:
        if not isinstance(row, dict):
            errors.append("identity pack fixture row must be an object")
            continue
        _exact_keys(
            errors,
            row,
            {"fixtureId", "canonicalSha256", "signingInputSha256", "payload"},
            "identity pack fixture row",
        )
        payload = row.get("payload", {})
        _exact_keys(errors, payload, identity_payload_keys, "IdentityPack/v1 payload")
        name = payload.get("display_name")
        expected = EXPECTED_IDENTITY_PACKS.get(name)
        if expected is None:
            errors.append("identity pack display name is not Eva or Mira")
            continue
        seen_names.append(name)
        source_payload = dict(payload)
        source_payload.pop("source_sha256", None)
        source_payload.pop("signature", None)
        signing_payload = dict(payload)
        signing_payload.pop("signature", None)
        signature = _decode_unpadded_base64url(payload.get("signature"))
        _need(
            errors,
            row.get("fixtureId") == expected["fixtureId"]
            and payload.get("schema_version") == 1
            and payload.get("contract") == "IdentityPack/v1"
            and payload.get("identity_id") == expected["identityId"]
            and payload.get("pack_version") == 1
            and payload.get("state") == "current"
            and payload.get("language") == "nl-NL"
            and payload.get("voice_style", {}).get("qualities")
            == expected["voiceQualities"]
            and payload.get("signature_algorithm") == "Ed25519"
            and payload.get("signature") == expected["signature"]
            and signature is not None
            and len(signature) == 64
            and _all_strings_nfc(payload)
            and payload.get("source_sha256")
            == sha256_bytes(canonical_json_bytes(source_payload))
            == expected["sourceSha256"]
            and row.get("signingInputSha256")
            == sha256_bytes(canonical_json_bytes(signing_payload))
            == expected["signingInputSha256"]
            and row.get("canonicalSha256")
            == sha256_bytes(canonical_json_bytes(payload))
            == expected["canonicalSha256"],
            f"content-addressed IdentityPack/v1 is not exact for {name}",
        )
    _need(
        errors,
        seen_names == ["Eva", "Mira"],
        "identity pack order is not Eva then Mira",
    )

    policy_row = packs.get("policyPack", {})
    _exact_keys(
        errors,
        policy_row,
        {
            "fixtureId",
            "canonicalSha256",
            "signingInputSha256",
            "detachedSignatureBase64url",
            "payload",
        },
        "policy pack fixture",
    )
    policy = policy_row.get("payload", {})
    _exact_keys(
        errors,
        policy,
        {
            "schema_version",
            "contract",
            "policy_id",
            "policy_version",
            "effect_policies",
            "budgets",
            "retry_policy_id",
            "retention_policy_id",
            "privacy_policy_id",
            "effective_at",
            "source_sha256",
            "release_signature_sha256",
        },
        "PolicyPack/v1 payload",
    )
    sealed_operations = vertical.get("allowedOperations", [])
    effect_policies = policy.get("effect_policies", [])
    _need(
        errors,
        len(effect_policies) == len(sealed_operations) == 11,
        "policy pack operation count differs from the sealed vertical",
    )
    for actual, sealed in zip(effect_policies, sealed_operations):
        if not isinstance(actual, dict):
            errors.append("policy effect row must be an object")
            continue
        _need(
            errors,
            set(actual)
            == {
                "operation",
                "effect_class",
                "consent_exempt",
                "proof_floor",
                "maximum_attempts",
            }
            and actual.get("operation") == sealed.get("capability")
            and actual.get("effect_class") == sealed.get("class")
            and actual.get("proof_floor") == sealed.get("proofFloor")
            and actual.get("maximum_attempts") == 2
            and actual.get("consent_exempt")
            is (
                sealed.get("class")
                in {"recovery_read", "verification_read", "breaker_probe_read"}
            ),
            f"policy effect row is not exact for {sealed.get('capability')}",
        )
    source_policy = dict(policy)
    source_policy.pop("source_sha256", None)
    source_policy.pop("release_signature_sha256", None)
    signing_policy = dict(policy)
    signing_policy.pop("release_signature_sha256", None)
    detached_signature = _decode_unpadded_base64url(
        policy_row.get("detachedSignatureBase64url")
    )
    _need(
        errors,
        policy_row.get("fixtureId") == "fixture:policy-pack:noor-4d:001"
        and policy.get("schema_version") == 1
        and policy.get("contract") == "PolicyPack/v1"
        and policy.get("policy_id") == "policy:noor-4d:fixture"
        and policy.get("policy_version") == 1
        and policy.get("budgets")
        == {
            "maximum_model_attempts": 4,
            "maximum_connector_calls": 24,
            "maximum_visible_deliveries": 2,
            "maximum_wall_clock_ms": 120000,
            "maximum_input_tokens": 30000,
            "maximum_output_tokens": 5000,
            "maximum_provider_cost_euro_cents": 150,
        }
        and policy.get("source_sha256")
        == sha256_bytes(canonical_json_bytes(source_policy))
        == EXPECTED_POLICY_SOURCE_SHA256
        and policy_row.get("signingInputSha256")
        == sha256_bytes(canonical_json_bytes(signing_policy))
        == EXPECTED_POLICY_SIGNING_INPUT_SHA256
        and policy_row.get("detachedSignatureBase64url") == EXPECTED_POLICY_SIGNATURE
        and detached_signature is not None
        and len(detached_signature) == 64
        and policy.get("release_signature_sha256")
        == sha256_bytes(detached_signature)
        == EXPECTED_POLICY_SIGNATURE_SHA256
        and policy_row.get("canonicalSha256")
        == sha256_bytes(canonical_json_bytes(policy))
        == EXPECTED_POLICY_PACK_SHA256
        and _all_strings_nfc(policy),
        "content-addressed PolicyPack/v1 is not exact",
    )

    linkage = packs.get("hostBindingRule", {})
    _need(
        errors,
        set(linkage)
        == {
            "selection",
            "presenceAndModelInput",
            "modelAttempt",
            "outcomeAndReply",
            "acceptanceEvidence",
            "missingCanonicalLinkageGate",
        }
        and "PresenceContext/v1" in linkage.get("presenceAndModelInput", "")
        and "policy_pack_sha256" in linkage.get("presenceAndModelInput", "")
        and "ModelAttempt/v1.request_sha256" in linkage.get("modelAttempt", "")
        and "no direct identity or policy hash field" in linkage.get("modelAttempt", "")
        and "neither contract has a direct policy_pack_sha256 field"
        in linkage.get("outcomeAndReply", "")
        and "EvaAcceptanceEvidenceBundle/v1" in linkage.get("acceptanceEvidence", "")
        and "no runtime implementation"
        in linkage.get("missingCanonicalLinkageGate", ""),
        "pack-to-v1 host linkage invents fields or remains open",
    )
    isolation = packs.get("identityRunIsolation", {})
    _need(
        errors,
        isolation
        == {
            "Eva": {
                "installationFixtureId": "fixture:installation:001",
                "installationUuid": "0a44a85c-4bbb-5330-8803-60fab86e5a94",
                "currentIdentityPackSha256": EXPECTED_IDENTITY_PACKS["Eva"][
                    "canonicalSha256"
                ],
            },
            "Mira": {
                "installationFixtureId": "fixture:installation:mira:001",
                "installationUuid": "18597c5d-591a-58bd-9a76-543d24e7e282",
                "currentIdentityPackSha256": EXPECTED_IDENTITY_PACKS["Mira"][
                    "canonicalSha256"
                ],
            },
            "rule": "Eva and Mira execute in two isolated fresh empty-install snapshots; no database contains both state=current payloads and each installation has exactly one current identity pack version",
        },
        "Eva and Mira current packs are not isolated to separate empty installs",
    )
    _need(
        errors,
        packs.get("functionalIdentityConstantsAllowedInExecutable") is False
        and packs.get("packActivationBeforeSignatureAndHashVerificationAllowed")
        is False
        and "two isolated Eva and Mira empty-install runs"
        in packs.get("identitySwapRule", "")
        and "byte-identical release executable" in packs.get("identitySwapRule", "")
        and "only the content-addressed current IdentityPack configuration changes"
        in packs.get("identitySwapRule", "")
        and "not a generic host success renderer"
        in packs.get("fixtureReplyVectorRule", "")
        and "without a canned golden reply" in packs.get("fixtureReplyVectorRule", ""),
        "identity swap or non-golden reply-vector rule is not exact",
    )


def _check_profile(
    profile: dict[str, Any], vertical: dict[str, Any], errors: list[str]
) -> None:
    root_keys = {
        "contract",
        "status",
        "decisionDate",
        "authorityBindings",
        "trancheBoundary",
        "preRuntimeRequiredArtifacts",
        "fixtureIdUuidRule",
        "connectorExecutionEligibility",
        "ingressOutcomeAckBinding",
        "fixtureWorkerPlan",
        "plannerHostAuthority",
        "sealedPackFixtures",
        "happyPath",
        "documentFixture",
        "correctionFixture",
        "erpAmbiguityFixture",
        "visibleOutcomeOracles",
        "encryptedObjectFormat",
        "caseOracles",
        "crashOracles",
        "acceptanceEvidence",
        "nonClaims",
    }
    _exact_keys(errors, profile, root_keys, "profile")
    _need(
        errors,
        profile.get("contract") == "Eva2Noor4DExecutionProfile/v1",
        "profile contract is not exact",
    )
    _need(
        errors,
        profile.get("status") == "sealed_pre_code_profile_not_implemented",
        "profile status inflates implementation",
    )

    authority = profile.get("authorityBindings", {})
    vertical_binding = authority.get("verticalContract", {})
    _need(
        errors,
        vertical_binding.get("path") == VERTICAL_PATH
        and vertical_binding.get("sha256") == EXPECTED_IMMUTABLE_FILES[VERTICAL_PATH],
        "vertical contract binding is not exact",
    )
    expected_map_paths = {
        path: digest
        for path, digest in EXPECTED_IMMUTABLE_FILES.items()
        if path.startswith("docs/eva2-build-map-")
    }
    actual_maps = {
        row.get("path"): row.get("sha256")
        for row in authority.get("buildMaps", [])
        if isinstance(row, dict)
    }
    _need(errors, actual_maps == expected_map_paths, "build-map bindings are not exact")

    boundary = profile.get("trancheBoundary", {})
    tranche_4d = boundary.get("tranche4D", {})
    tranche_4e = boundary.get("tranche4E", {})
    _need(
        errors,
        set(tranche_4d.get("ownedCaseIds", [])) == EXPECTED_4D_CASES,
        "4D case ownership is not exact",
    )
    _need(
        errors,
        set(tranche_4e.get("ownedCaseIds", [])) == EXPECTED_4E_CASES,
        "4E deferred case ownership is not exact",
    )
    _need(
        errors,
        tranche_4d.get("genericReaderSurfaceRequired") is True
        and tranche_4d.get("secondSystemRestoreProofRequired") is False
        and tranche_4e.get("realOsProcessCrashProofRequired") is True,
        "4D/4E proof boundary is not exact",
    )

    _check_pre_runtime_required_artifacts(
        profile.get("preRuntimeRequiredArtifacts", {}), errors
    )

    uuid_rule = profile.get("fixtureIdUuidRule", {})
    root_namespace = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
    namespace_name = (
        "urn:eva2:vertical:noor-4d:"
        "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1"
    )
    derived = uuid.uuid5(root_namespace, namespace_name)
    _need(
        errors,
        uuid_rule.get("algorithm") == "UUIDv5_SHA1_namespace_name"
        and uuid_rule.get("namespaceName") == namespace_name
        and uuid_rule.get("derivedNamespace") == str(derived),
        "fixture UUID namespace rule is not exact",
    )
    vectors = uuid_rule.get("testVectors", [])
    vector_ids = {row.get("fixtureId") for row in vectors if isinstance(row, dict)}
    _need(
        errors, vector_ids == EXPECTED_UUID_VECTOR_IDS, "UUID vector set is not exact"
    )
    for row in vectors:
        if not isinstance(row, dict) or set(row) != {"fixtureId", "uuid"}:
            errors.append("UUID vector fields are not exact")
            continue
        fixture_id = row["fixtureId"]
        expected_uuid = str(
            uuid.uuid5(derived, unicodedata.normalize("NFC", fixture_id))
        )
        _need(
            errors,
            row["uuid"] == expected_uuid,
            f"UUID vector mismatch for {fixture_id}",
        )

    eligibility = profile.get("connectorExecutionEligibility", {})
    _need(
        errors,
        eligibility.get("bindingStatusRequired") == "verified"
        and eligibility.get("capabilityActivationStatusRequired") == "active"
        and eligibility.get("bindingStatusAliasAllowed") is False
        and eligibility.get("verifiedAloneAllowsExecution") is False
        and eligibility.get("activeActivationAloneAllowsExecution") is False,
        "verified plus active connector eligibility rule is not exact",
    )
    _need(
        errors,
        eligibility.get("exactRule")
        == "eligible = connector_binding.status == 'verified' AND capability_activation.status == 'active' AND requested_operation is present in both records AND both scopes cover the same tenant, principal, resource and request",
        "connector eligibility expression is not exact",
    )

    ack_binding = profile.get("ingressOutcomeAckBinding", {})
    ack_fixture = ack_binding.get("publicKeyFixture", {})
    _need(
        errors,
        ack_binding.get("algorithm") == "P-256 ES256"
        and ack_binding.get("signatureFormat") == "P1363"
        and ack_fixture.get("path") == "backup/history/root/design/fixtures/noor-client-ack-key-v1.json"
        and ack_fixture.get("fileSha256")
        == "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93"
        and ack_fixture.get("canonicalPublicJwkSha256")
        == "5bb0d99f6da06462346c922d989d913f3308d616dfbf52f70fdd4544c0065b83"
        and ack_fixture.get("privateKeyBytesStoredInIngressOfferReceiptOrEvidence")
        is False
        and "IngressRequest/v1.client_ack_key_sha256"
        in ack_binding.get("admissionRule", "")
        and "DeliveryOffer/v2.proof.ack_key_sha256" in ack_binding.get("offerRule", "")
        and "DeliveryOfferV2.validate_against_ingress(IngressRequest)"
        in ack_binding.get("offerIngressBinder", "")
        and "DeliveryReceiptV2.validate_against_offer(offer, ingress, signature_verifier=..., challenge_claimer=...)"
        in ack_binding.get("receiptBinder", ""),
        "ingress, offer and receipt ACK-key binding is not exact",
    )
    _need(
        errors,
        ack_binding.get("signatureInputFields")
        == vertical["fixtureAuthority"]["ingress"]["ackSignedFields"],
        "ACK signed-field binding differs from the sealed vertical",
    )

    workers = profile.get("fixtureWorkerPlan", {})
    worker_rows = workers.get("workers", [])
    bindings: list[str] = []
    for row in worker_rows:
        bindings.extend(row.get("bindingIds", []))
    sealed_bindings = [row["bindingId"] for row in vertical["connectorBindings"]]
    _need(
        errors,
        workers.get("workerCount") == 5
        and workers.get("bindingCount") == 6
        and len(worker_rows) == 5
        and sorted(bindings) == sorted(sealed_bindings),
        "five-worker to six-binding mapping is not exact",
    )
    channel_rows = [
        row for row in worker_rows if row.get("connector") == "channel_fixture"
    ]
    _need(
        errors,
        len(channel_rows) == 1
        and set(channel_rows[0].get("bindingIds", []))
        == {
            "fixture:binding:confirmation-delivery:001",
            "fixture:binding:outcome-delivery:001",
        },
        "shared channel worker mapping is not exact",
    )

    planner = profile.get("plannerHostAuthority", {}).get("planner", {})
    _need(
        errors,
        planner.get("mayOnlyPropose")
        == ["ModelDecision/v1", "DocumentDraft/v1", "ReplyCandidate/v1"]
        and all(
            planner.get(field) is False
            for field in (
                "tools",
                "sessions",
                "memoryAuthority",
                "retryAuthority",
                "effectAuthority",
                "grantAuthority",
                "outcomeAuthority",
            )
        ),
        "planner remains over-authoritative",
    )

    _check_sealed_packs(profile.get("sealedPackFixtures", {}), vertical, errors)

    happy = profile.get("happyPath", {})
    _need(
        errors,
        happy.get("clock") == vertical["fixtureAuthority"]["clock"]
        and happy.get("timezone") == vertical["fixtureAuthority"]["timezone"]
        and happy.get("naturalRequest") == vertical["naturalRequest"]["text"],
        "happy-path authority fields differ from the vertical contract",
    )
    meeting = happy.get("expectedMeeting", {})
    _need(
        errors,
        meeting.get("start") == vertical["happyOutcome"]["meetingStart"]
        and meeting.get("end") == vertical["happyOutcome"]["meetingEnd"],
        "happy-path meeting is not exact",
    )
    calls = happy.get("connectorCalls", [])
    actual_calls = [
        (row.get("operation"), row.get("class"), row.get("bindingId"))
        for row in calls
        if isinstance(row, dict)
    ]
    _need(errors, actual_calls == EXPECTED_CALLS, "eleven-call trace is not exact")
    _need(
        errors,
        [row.get("ordinal") for row in calls] == list(range(1, 12)),
        "connector call ordinals are not 1 through 11",
    )
    count = happy.get("connectorCallCountOracle", {})
    _need(
        errors,
        count
        == {
            "total": 11,
            "businessReads": 4,
            "effects": 4,
            "providerReadbacks": 3,
            "reconcileReads": 0,
            "breakerProbes": 0,
            "initializationExternalCalls": 0,
        },
        "happy-path call counts are not exact",
    )
    _need(
        errors,
        [
            (row.get("purpose"), row.get("contract"))
            for row in happy.get("modelAttempts", [])
        ]
        == [
            ("plan", "ModelDecision/v1"),
            ("document_draft", "DocumentDraft/v1"),
            ("reply", "ReplyCandidate/v1"),
        ],
        "happy-path model attempts are not exact",
    )

    document = profile.get("documentFixture", {})
    document_bytes = str(document.get("text", "")).encode("utf-8")
    _need(
        errors,
        document.get("text") == EXPECTED_DOCUMENT_TEXT
        and document.get("mimeType") == "text/plain"
        and document.get("byteLength") == len(document_bytes) == 122
        and document.get("sha256")
        == sha256_bytes(document_bytes)
        == EXPECTED_DOCUMENT_SHA256
        and document.get("maximumByteLength") == 65536,
        "document text, length, hash or limit is not exact",
    )

    correction = profile.get("correctionFixture", {})
    correction_bytes = str(correction.get("correctionText", "")).encode("utf-8")
    _need(
        errors,
        correction.get("correctionText") == "Niet om 14.00 uur, maak er 15.00 uur van."
        and correction.get("oldGeneration") == 1
        and correction.get("newGeneration") == 2
        and correction.get("oldSlot") == "2026-08-26T14:00:00+02:00"
        and correction.get("newSlot") == "2026-08-26T15:00:00+02:00"
        and correction.get("correctionSha256") == sha256_bytes(correction_bytes)
        and correction.get("oracle")
        == next(
            row["oracle"]
            for row in vertical["requiredVariants"]
            if row["caseId"] == "correction-before-first-effect"
        ),
        "correction fixture is not exact",
    )
    ambiguity = profile.get("erpAmbiguityFixture", {})
    question_bytes = str(ambiguity.get("combinedNecessaryQuestion", "")).encode("utf-8")
    _need(
        errors,
        ambiguity.get("matchCardinality") == 2
        and [row.get("customerId") for row in ambiguity.get("matches", [])]
        == ["erp:customer:204", "erp:customer:319"]
        and ambiguity.get("questionSha256") == sha256_bytes(question_bytes)
        and ambiguity.get("oracle")
        == next(
            row["oracle"]
            for row in vertical["requiredVariants"]
            if row["caseId"] == "erp-ambiguous-match"
        ),
        "ERP ambiguity fixture is not exact",
    )

    visible = profile.get("visibleOutcomeOracles", {})
    for identifier, expected_text in EXPECTED_VISIBLE_TEXTS.items():
        row = visible.get(identifier, {})
        raw = expected_text.encode("utf-8")
        _need(
            errors,
            row.get("text") == expected_text
            and row.get("utf8ByteLength") == len(raw)
            and row.get("sha256") == sha256_bytes(raw)
            and "\u2013" not in expected_text
            and "\u2014" not in expected_text,
            f"visible outcome {identifier} is not exact",
        )
    unknown = visible.get("outcomeDeliveryUnknown", {})
    _need(
        errors,
        unknown.get("bytesOfferedOrTransmittedBeforeValidAckMayBeTrue") is True
        and unknown.get("humanVisibilityBeforeValidAck")
        == "unknown_and_must_not_be_claimed_either_way"
        and unknown.get("durableDeliveryClaimBeforeValidAck") is False
        and unknown.get("commitmentStateBeforeValidAck") == "open",
        "client ACK visibility and delivery-claim distinction is not exact",
    )
    model_ambiguous = visible.get("modelResponseAmbiguous", {})
    plan_text = model_ambiguous.get("planText", "")
    _need(
        errors,
        plan_text
        == "Ik kon het plan niet betrouwbaar afronden. Er is nog niets in de agenda gezet en niets verstuurd. Wil je dat ik het opnieuw als een nieuw verzoek probeer?"
        and model_ambiguous.get("planTextUtf8ByteLength")
        == len(plan_text.encode("utf-8"))
        == 154
        and model_ambiguous.get("planTextSha256")
        == sha256_bytes(plan_text.encode("utf-8"))
        and "authenticated new request" in model_ambiguous.get("resumeRule", ""),
        "ambiguous model call has no exact visible recovery outcome",
    )

    sealed_cases = vertical.get("requiredVariants", [])
    profile_cases = profile.get("caseOracles", [])
    _need(
        errors,
        [row.get("caseId") for row in profile_cases]
        == [row.get("caseId") for row in sealed_cases],
        "case oracle order or set differs from the sealed vertical",
    )
    visible_ids = set(visible)
    for sealed, actual in zip(sealed_cases, profile_cases):
        if not isinstance(actual, dict):
            errors.append("case oracle row must be an object")
            continue
        for key, value in sealed.items():
            _need(
                errors,
                actual.get(key) == value,
                f"sealed case field differs for {sealed.get('caseId')}: {key}",
            )
        case_id = sealed["caseId"]
        expected_tranche = "4D" if case_id in EXPECTED_4D_CASES else "4E"
        expected_status = (
            "required_not_implemented"
            if expected_tranche == "4D"
            else "deferred_to_4e_not_implemented"
        )
        _need(
            errors,
            actual.get("executionTranche") == expected_tranche
            and actual.get("preCodeStatus") == expected_status,
            f"case tranche/status differs for {case_id}",
        )
        _need(
            errors,
            actual.get("visibleOutcomeOracleId") in visible_ids,
            f"case has no closed visible-outcome oracle: {case_id}",
        )

    crash_rows = profile.get("crashOracles", [])
    crash_points = [
        row.get("crashPoint") for row in crash_rows if isinstance(row, dict)
    ]
    _need(
        errors,
        crash_points == vertical.get("requiredCrashPoints"),
        "crash oracle points/order differ from the sealed vertical",
    )
    required_crash_terms = [
        ("no admission", "no model"),
        ("same admitted request", "no second admission"),
        ("same reserved attempt", "at most one provider call"),
        (
            "state ambiguous",
            "no blind repeat",
            "for plan purpose only",
            "EpisodeState becomes waiting_user",
            "Commitment becomes waiting",
            "AttentionWake becomes waiting",
            "for document_draft purpose",
            "exact documentRejected",
            "for reply purpose",
            "zero new model calls",
        ),
        ("same committed plan", "does not repeat"),
        ("zero provider writes", "superseded generation"),
        ("reconciles", "never performs a blind resend"),
        ("found_same", "second effect is forbidden"),
        ("prevents effect repetition", "readback"),
        ("repeating the verification read", "repeating the effect is forbidden"),
        ("reconciles", "without blind resend"),
        ("rebuilt", "no business effect"),
        ("commitment remains visibly open", "no business effect repeats"),
        ("closes the same episode exactly once", "without another delivery"),
        ("exact committed terminal response bytes", "zero model or connector calls"),
    ]
    for row, terms in zip(crash_rows, required_crash_terms):
        oracle = row.get("fourDRecoveryOracle", "") if isinstance(row, dict) else ""
        _need(
            errors,
            set(row) == {"crashPoint", "fourDRecoveryOracle"}
            and all(term in oracle for term in terms),
            f"crash recovery oracle is not closed for {row.get('crashPoint')}",
        )

    _check_object_format(profile.get("encryptedObjectFormat", {}), errors)

    acceptance = profile.get("acceptanceEvidence", {})
    _need(
        errors,
        acceptance.get("testsAloneCanAccept") is False
        and len(acceptance.get("preCodeFreezeAcceptance", [])) == 4
        and any(
            "runtime implementation remains explicitly blocked" in item
            for item in acceptance.get("preCodeFreezeAcceptance", [])
        )
        and len(acceptance.get("fourDBehaviorEvidenceRequiredBeforeFourDClaim", []))
        >= 11
        and any(
            "DBOS-free generic-reader receipt" in item
            for item in acceptance.get(
                "fourDBehaviorEvidenceRequiredBeforeFourDClaim", []
            )
        )
        and any(
            "five content-addressed fixture-worker manifests" in item
            for item in acceptance.get(
                "fourDBehaviorEvidenceRequiredBeforeFourDClaim", []
            )
        )
        and any(
            "content-addressed deterministic model adapter" in item
            for item in acceptance.get(
                "fourDBehaviorEvidenceRequiredBeforeFourDClaim", []
            )
        )
        and "disposable real connectors"
        in acceptance.get("externalProductClaimRequires", ""),
        "acceptance evidence still allows tests or fixture evidence to inflate claims",
    )
    nonclaims = profile.get("nonClaims", [])
    required_nonclaim_terms = (
        "pre-code design freeze",
        "profile alone does not authorize runtime code",
        "requirements, not executed receipts",
        "no real provider",
        "no real OS process crash",
        "no Windows or macOS support claim",
        "no stock DBOS",
        "no dependency installability",
        "no FIPS",
        "no production-active ObjectRef or ObjectStore",
        "Eva and Mira installation-scoped fixture key records",
        "no object over 65536",
        "no Eva 1 executable import",
    )
    _need(
        errors,
        len(nonclaims) == 12
        and all(
            any(term in claim for claim in nonclaims)
            for term in required_nonclaim_terms
        ),
        "profile nonclaims are incomplete",
    )


def _check_object_format(fmt: dict[str, Any], errors: list[str]) -> None:
    _exact_keys(
        errors,
        fmt,
        {
            "contract",
            "implementation",
            "primitive",
            "ownCryptographicPrimitiveAllowed",
            "keyBytes",
            "nonceBytes",
            "tagBytes",
            "maximumPlaintextBytes",
            "maximumCanonicalAadBytes",
            "maximumStoredEnvelopeBytes",
            "magicAscii",
            "magicHex",
            "formatVersion",
            "byteLayout",
            "aad",
            "encryptionProcedure",
            "decryptionProcedure",
            "hashAndLengthSemantics",
            "physicalContentAddressing",
            "nonceRule",
            "knownAnswerVectors",
            "objectStoreActivationRecords",
            "keyBinding",
            "atomicCreateAndReadback",
        },
        "encrypted object format",
    )
    _need(
        errors,
        fmt.get("contract") == "Eva4DEncryptedObject/v1"
        and fmt.get("implementation") == "PyNaCl==1.6.2 nacl.secret.Aead"
        and fmt.get("primitive") == "crypto_aead_xchacha20poly1305_ietf"
        and fmt.get("ownCryptographicPrimitiveAllowed") is False
        and fmt.get("keyBytes") == 32
        and fmt.get("nonceBytes") == 24
        and fmt.get("tagBytes") == 16
        and fmt.get("maximumPlaintextBytes") == 65536
        and fmt.get("maximumCanonicalAadBytes") == 1024
        and fmt.get("maximumStoredEnvelopeBytes") == 66612
        and fmt.get("magicAscii") == "EVA2XO01"
        and fmt.get("magicHex") == b"EVA2XO01".hex()
        and fmt.get("formatVersion") == 1,
        "encrypted object primitive, framing or bounds are not exact",
    )
    layout = fmt.get("byteLayout", [])
    expected_layout = [
        (0, 8, "magic_and_version"),
        (8, 4, "canonical_aad_byte_length"),
        (12, "canonical_aad_byte_length", "canonical_aad_json"),
        ("12 + canonical_aad_byte_length", 24, "nonce"),
        (
            "36 + canonical_aad_byte_length",
            "plaintext_byte_length",
            "ciphertext",
        ),
        (
            "36 + canonical_aad_byte_length + plaintext_byte_length",
            16,
            "poly1305_tag",
        ),
    ]
    actual_layout = [
        (row.get("offset"), row.get("byteLength"), row.get("field"))
        for row in layout
        if isinstance(row, dict)
    ]
    _need(errors, actual_layout == expected_layout, "object byte layout is not exact")

    aad = fmt.get("aad", {})
    sample_text = aad.get("sampleCanonicalJson", "")
    try:
        sample = json.loads(
            sample_text,
            object_pairs_hook=_reject_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, FreezeError) as exc:
        errors.append(f"object AAD sample is invalid: {exc}")
        return
    sample_bytes = sample_text.encode("utf-8")
    _need(
        errors,
        canonical_json_bytes(sample) == sample_bytes,
        "object AAD sample is not canonical JSON",
    )
    expected_aad_keys = {
        "contract",
        "installation_id",
        "key_ref",
        "mime_type",
        "object_id",
        "plaintext_byte_length",
        "plaintext_sha256",
        "tenant_id",
    }
    _need(
        errors,
        set(sample) == expected_aad_keys
        and aad.get("exactKeys") == sorted(expected_aad_keys),
        "object AAD key set/order is not exact",
    )
    _need(
        errors,
        sample["contract"] == "Eva4DObjectAAD/v1"
        and sample["installation_id"] == "0a44a85c-4bbb-5330-8803-60fab86e5a94"
        and sample["tenant_id"] == "66a4aad5-0550-5211-8b36-398eabe5b7be"
        and sample["object_id"] == "8fa3f97c-dc66-52f2-840c-d441b55f6a60"
        and sample["key_ref"] == "6cb79e1a-220a-5780-b544-e2b4dcf1f19e"
        and sample["mime_type"] == "text/plain"
        and sample["plaintext_byte_length"] == 122
        and sample["plaintext_sha256"] == EXPECTED_DOCUMENT_SHA256,
        "object AAD sample does not bind the Noor object",
    )
    header = b"EVA2XO01" + struct.pack(">I", len(sample_bytes)) + sample_bytes
    _need(
        errors,
        aad.get("sampleCanonicalJsonByteLength") == len(sample_bytes) == 379
        and aad.get("sampleCanonicalJsonSha256") == sha256_bytes(sample_bytes)
        and aad.get("sampleAlgorithmInputByteLength") == len(header) == 391
        and aad.get("sampleAlgorithmInputSha256") == sha256_bytes(header),
        "object AAD sample hashes or lengths are not exact",
    )
    semantics = fmt.get("hashAndLengthSemantics", {})
    _need(
        errors,
        semantics.get("objectRefByteLength")
        == "ObjectRef.byte_length is plaintextByteLength, not stored envelope length"
        and semantics.get("objectRefCiphertextSha256")
        == "ObjectRef.ciphertext_sha256 is ciphertextSha256 over the complete stored envelope"
        and semantics.get("sampleDocumentStoredByteLength") == 12 + 379 + 24 + 122 + 16,
        "ObjectRef hash/length mapping is not exact",
    )
    mapping = fmt.get("keyBinding", {}).get("objectRefMapping", {})
    _need(
        errors,
        set(mapping)
        == {
            "aadObjectIdEquals",
            "aadKeyRefEquals",
            "aadMimeTypeEquals",
            "aadPlaintextSha256Equals",
            "fullEnvelopeSha256Equals",
            "aadPlaintextByteLengthEquals",
        }
        and "ObjectRef.object_id" in mapping.get("aadObjectIdEquals", "")
        and "ObjectRef.wrapped_key_ref" in mapping.get("aadKeyRefEquals", "")
        and "ObjectRef.mime_type" in mapping.get("aadMimeTypeEquals", ""),
        "AAD to ObjectRef authority mapping is incomplete",
    )
    key_binding = fmt.get("keyBinding", {})
    _exact_keys(
        errors,
        key_binding,
        {
            "keyMaterialStoredInEnvelope",
            "keyReferenceStoredInCanonicalAad",
            "objectAndKeyReferenceImmutableAfterCreate",
            "objectRefMapping",
            "scopeMustMatch",
            "fourDFixtureKeyRecords",
            "fourDFixtureKeyRule",
            "objectRefRunnableGate",
            "keyWrappingRotationAndErase",
        },
        "encrypted object key binding",
    )
    fixture_keys = key_binding.get("fourDFixtureKeyRecords", [])
    _need(
        errors,
        len(fixture_keys) == 2
        and [row.get("identity") for row in fixture_keys] == ["Eva", "Mira"],
        "4D must have separate Eva and Mira fixture-key records",
    )
    keys_by_identity: dict[str, dict[str, Any]] = {}
    for row in fixture_keys:
        if not isinstance(row, dict):
            errors.append("4D fixture-key row must be an object")
            continue
        identity = row.get("identity")
        expected = EXPECTED_XCHACHA_KATS.get(identity)
        try:
            key_bytes = bytes.fromhex(str(row.get("keyMaterial", "")))
        except ValueError:
            key_bytes = b""
        _need(
            errors,
            expected is not None
            and set(row)
            == {
                "identity",
                "contract",
                "classification",
                "keyAuthorityRef",
                "installationId",
                "tenantId",
                "keyRef",
                "algorithm",
                "keyMaterialEncoding",
                "keyMaterial",
                "keySha256",
                "productionUseAuthorized",
            }
            and row.get("contract") == "Eva4DFixtureObjectKeyRecord/v1"
            and row.get("classification") == "public_test_fixture_not_a_secret"
            and row.get("keyAuthorityRef") == expected.get("keyAuthorityRef")
            and row.get("installationId") == expected.get("installationId")
            and row.get("tenantId") == expected.get("tenantId")
            and row.get("keyRef") == expected.get("keyRef")
            and row.get("algorithm") == "XChaCha20-Poly1305"
            and row.get("keyMaterialEncoding") == "lowercase_hex"
            and row.get("keyMaterial") == expected.get("keyMaterial")
            and len(key_bytes) == 32
            and row.get("keySha256")
            == sha256_bytes(key_bytes)
            == expected.get("keySha256")
            and row.get("productionUseAuthorized") is False,
            f"4D fixture-only key record is not exact for {identity}",
        )
        if isinstance(identity, str):
            keys_by_identity[identity] = row
    _need(
        errors,
        "separate installation-scoped records"
        in key_binding.get("fourDFixtureKeyRule", "")
        and "only inside the deterministic fixture profile"
        in key_binding.get("objectRefRunnableGate", ""),
        "4D fixture-only ObjectRef resolver/gate is not exact",
    )

    kat = fmt.get("knownAnswerVectors", {})
    _exact_keys(
        errors,
        kat,
        {"purpose", "libraryCall", "runtimeNonceRuleUnchanged", "vectors"},
        "XCha known-answer vectors",
    )
    kat_rows = kat.get("vectors", [])
    _need(
        errors,
        "format conformance only" in kat.get("purpose", "")
        and "fixed nonces" in kat.get("purpose", "")
        and "PyNaCl 1.6.2" in kat.get("libraryCall", "")
        and "decrypt must return" in kat.get("libraryCall", "")
        and "runtime must omit nonce" in kat.get("runtimeNonceRuleUnchanged", "")
        and len(kat_rows) == 2,
        "XCha known-answer vector scope or runtime nonce boundary is not exact",
    )
    _need(
        errors,
        [row.get("identity") for row in kat_rows if isinstance(row, dict)]
        == ["Eva", "Mira"],
        "XCha known-answer vectors are not unique Eva then Mira",
    )
    for row in kat_rows:
        if not isinstance(row, dict):
            errors.append("XCha known-answer vector must be an object")
            continue
        identity = row.get("identity")
        expected = EXPECTED_XCHACHA_KATS.get(identity)
        fixture_key = keys_by_identity.get(identity, {})
        try:
            aad_value = json.loads(
                row.get("canonicalAadJson", ""),
                object_pairs_hook=_reject_pairs,
                parse_float=_reject_float,
                parse_constant=_reject_constant,
            )
            aad_bytes = row["canonicalAadJson"].encode("utf-8")
            nonce = bytes.fromhex(row.get("nonceHex", ""))
            ciphertext = bytes.fromhex(row.get("ciphertextHex", ""))
            tag = bytes.fromhex(row.get("tagHex", ""))
            envelope = bytes.fromhex(row.get("completeEnvelopeHex", ""))
        except (KeyError, ValueError, json.JSONDecodeError, FreezeError):
            aad_value = {}
            aad_bytes = b""
            nonce = b""
            ciphertext = b""
            tag = b""
            envelope = b""
        header = b"EVA2XO01" + struct.pack(">I", len(aad_bytes)) + aad_bytes
        object_ref = row.get("expectedObjectRef", {})
        _need(
            errors,
            expected is not None
            and set(row)
            == {
                "identity",
                "installationId",
                "tenantId",
                "objectId",
                "keyRef",
                "keySha256",
                "nonceHex",
                "canonicalAadJson",
                "canonicalAadByteLength",
                "canonicalAadSha256",
                "algorithmInputByteLength",
                "algorithmInputSha256",
                "ciphertextHex",
                "tagHex",
                "ciphertextAndTagSha256",
                "completeEnvelopeHex",
                "completeEnvelopeByteLength",
                "completeEnvelopeSha256",
                "expectedObjectRefCanonicalSha256",
                "expectedObjectRef",
            }
            and canonical_json_bytes(aad_value) == aad_bytes
            and set(aad_value) == expected_aad_keys
            and aad_value.get("contract") == "Eva4DObjectAAD/v1"
            and aad_value.get("installation_id")
            == row.get("installationId")
            == fixture_key.get("installationId")
            == expected.get("installationId")
            and aad_value.get("tenant_id")
            == row.get("tenantId")
            == fixture_key.get("tenantId")
            == expected.get("tenantId")
            and aad_value.get("object_id")
            == row.get("objectId")
            == expected.get("objectId")
            and aad_value.get("key_ref")
            == row.get("keyRef")
            == fixture_key.get("keyRef")
            == expected.get("keyRef")
            and aad_value.get("mime_type") == "text/plain"
            and aad_value.get("plaintext_byte_length") == 122
            and aad_value.get("plaintext_sha256") == EXPECTED_DOCUMENT_SHA256
            and row.get("keySha256")
            == fixture_key.get("keySha256")
            == expected.get("keySha256")
            and len(aad_bytes) == row.get("canonicalAadByteLength") == 379
            and sha256_bytes(aad_bytes)
            == row.get("canonicalAadSha256")
            == expected.get("aadSha256")
            and len(header) == row.get("algorithmInputByteLength") == 391
            and sha256_bytes(header)
            == row.get("algorithmInputSha256")
            == expected.get("headerSha256")
            and len(nonce) == 24
            and row.get("nonceHex") == expected.get("nonceHex")
            and len(ciphertext) == 122
            and row.get("ciphertextHex") == expected.get("ciphertextHex")
            and len(tag) == 16
            and row.get("tagHex") == expected.get("tagHex")
            and sha256_bytes(ciphertext + tag)
            == row.get("ciphertextAndTagSha256")
            == expected.get("ciphertextAndTagSha256")
            and envelope == header + nonce + ciphertext + tag
            and len(envelope) == row.get("completeEnvelopeByteLength") == 553
            and sha256_bytes(envelope)
            == row.get("completeEnvelopeSha256")
            == expected.get("envelopeSha256")
            and object_ref
            == {
                "schema_version": 1,
                "contract": "ObjectRef/v1",
                "object_id": expected.get("objectId"),
                "ciphertext_sha256": expected.get("envelopeSha256"),
                "plaintext_sha256": EXPECTED_DOCUMENT_SHA256,
                "byte_length": 122,
                "mime_type": "text/plain",
                "encryption_algorithm": "XChaCha20-Poly1305",
                "wrapped_key_ref": expected.get("keyRef"),
                "retention_until": "2026-09-19T12:00:00Z",
            }
            and row.get("expectedObjectRefCanonicalSha256")
            == sha256_bytes(canonical_json_bytes(object_ref))
            == expected.get("objectRefSha256"),
            f"XCha known-answer vector or AAD/ObjectRef scope is not exact for {identity}",
        )

    stores = fmt.get("objectStoreActivationRecords", {})
    _exact_keys(
        errors,
        stores,
        {
            "preCodeStatus",
            "integrityRootInputCanonicalJson",
            "integrityRootSha256",
            "records",
        },
        "ObjectStore activation records",
    )
    store_rows = stores.get("records", [])
    empty_manifest = stores.get("integrityRootInputCanonicalJson", "").encode("utf-8")
    _need(
        errors,
        stores.get("preCodeStatus") == "required_not_implemented"
        and empty_manifest == b'{"objects":[]}'
        and stores.get("integrityRootSha256") == sha256_bytes(empty_manifest)
        and len(store_rows) == 2,
        "ObjectStore activation record header is not exact",
    )
    _need(
        errors,
        [row.get("identity") for row in store_rows if isinstance(row, dict)]
        == ["Eva", "Mira"],
        "ObjectStore activation records are not unique Eva then Mira",
    )
    for row in store_rows:
        if not isinstance(row, dict):
            errors.append("ObjectStore activation row must be an object")
            continue
        identity = row.get("identity")
        expected = EXPECTED_XCHACHA_KATS.get(identity)
        payload = row.get("payload", {})
        _need(
            errors,
            expected is not None
            and set(row) == {"identity", "canonicalSha256", "payload"}
            and payload
            == {
                "schema_version": 1,
                "contract": "ObjectStore/v1",
                "object_store_id": f"object-store:noor-4d:{str(identity).lower()}",
                "installation_id": expected.get("installationId"),
                "backend": "encrypted_local_immutable_filesystem",
                "state": "active",
                "encryption_algorithm": "XChaCha20-Poly1305",
                "key_authority_ref": expected.get("keyAuthorityRef"),
                "maximum_object_bytes": 65536,
                "integrity_root_sha256": stores.get("integrityRootSha256"),
                "absolute_host_path_in_contract": False,
                "verified_at": "2026-08-20T12:00:00Z",
            }
            and row.get("canonicalSha256")
            == sha256_bytes(canonical_json_bytes(payload))
            == expected.get("objectStoreSha256"),
            f"ObjectStore/v1 activation record is not exact for {identity}",
        )
    physical = fmt.get("physicalContentAddressing", {})
    _need(
        errors,
        physical.get("physicalCasKey")
        == "lowercase hex ObjectRef.ciphertext_sha256 over the complete stored envelope"
        and physical.get("repositoryRelativeCasPath")
        == "sha256/{ciphertext_sha256[0:2]}/{ciphertext_sha256}.eva2xo"
        and physical.get("absoluteHostPathStoredInContract") is False
        and "filesystem name derived from object_id is forbidden"
        in physical.get("canonicalMapping", "")
        and physical.get("orphanCleanupClaim") is False,
        "physical object storage is not exact envelope-content-addressed CAS",
    )
    encryption = " ".join(fmt.get("encryptionProcedure", []))
    decryption = " ".join(fmt.get("decryptionProcedure", []))
    atomic = fmt.get("atomicCreateAndReadback", {})
    _need(
        errors,
        "EncryptedMessage.ciphertext" in encryption
        and "do not prepend the nonce or append the tag again" in encryption
        and "trailing bytes" in decryption
        and "exact length" in decryption
        and "create-if-absent by complete-envelope ciphertext_sha256"
        in atomic.get("createSemantics", "")
        and "object-ID filesystem addressing" in atomic.get("createSemantics", "")
        and "same reserved installation_id" in atomic.get("ambiguousCreateRecovery", "")
        and "different nonce" in atomic.get("ambiguousCreateRecovery", "")
        and atomic.get("temporaryObjectIsProof") is False
        and atomic.get("createReceiptWithoutReadbackIsProof") is False,
        "object create/readback/reconcile procedure is not fail closed",
    )
    _need(
        errors,
        atomic.get("orderedSteps")
        == [
            "commit the canonical object intent and immutable AAD fields",
            (
                "reserve the unique object create attempt, generate one random-"
                "nonce envelope, compute its ciphertextSha256, write it under an "
                "attempt-scoped non-final temporary name and durably flush its bytes"
            ),
            (
                "commit the candidate ciphertextSha256 and temporary-object binding "
                "before any final CAS installation"
            ),
            (
                "derive sha256/{first two digest hex}/{full 64 digest hex}.eva2xo, "
                "install that final CAS path with an atomic no-replace operation and "
                "durably flush the parent namespace"
            ),
            (
                "open the exact CAS path independently, recompute ciphertextSha256 "
                "and stored envelope length, authenticate and decrypt it, and match "
                "every AAD field plus plaintext hash and length"
            ),
            (
                "atomically select that verified digest in the canonical object "
                "record if no digest is selected; if the same logical object already "
                "selected another digest, read and authenticate the selected digest "
                "and adopt it only when the full reserved binding and plaintext are "
                "equal"
            ),
            (
                "only the DB-selected digest and its exact independent readback may "
                "populate ObjectRef and satisfy the document proof floor; an "
                "unselected candidate or cleanup is never proof"
            ),
        ],
        "object create/readback ordered CAS steps are not exact",
    )


def _check_lock_and_pyproject(
    root: Path, binding: dict[str, Any], errors: list[str]
) -> None:
    baseline_path = _path(root, "requirements.lock")
    lock_path = _path(root, LOCK_4D_PATH)
    baseline = baseline_path.read_text(encoding="utf-8")
    actual_lock = lock_path.read_text(encoding="utf-8")
    try:
        expected_lock = derive_4d_lock(baseline)
    except FreezeError as exc:
        errors.append(str(exc))
        expected_lock = ""
    _need(
        errors,
        actual_lock == expected_lock,
        "4D lock is not the exact baseline transformation",
    )
    _need(
        errors,
        sha256_bytes(actual_lock.encode("utf-8")) == EXPECTED_4D_LOCK_SHA256,
        "4D lock digest is not exact",
    )
    requirement_count = len(
        re.findall(r"(?m)^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==[^ ]+ \\\n", actual_lock)
    )
    _need(errors, requirement_count == 39, "4D lock does not contain exactly 39 pins")
    noncomment = "\n".join(
        line for line in actual_lock.splitlines() if not line.lstrip().startswith("#")
    )
    forbidden = ("\n-r ", "--index", "--extra-index", "--find-links", "://", " @ ")
    _need(
        errors,
        all(token not in "\n" + noncomment for token in forbidden),
        "4D lock contains an include, index, URL or direct reference",
    )

    pyproject_path = _path(root, PYPROJECT_PATH)
    _need(
        errors,
        sha256_file(pyproject_path) == EXPECTED_PYPROJECT_SHA256,
        "pyproject digest is not the sealed 4D adoption",
    )
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        errors.append(f"invalid pyproject TOML: {exc}")
        return
    project = pyproject.get("project", {})
    baseline_dependencies = [
        "alembic==1.19.1",
        "cryptography==46.0.5",
        "dbos==2.30.0",
        "fastapi==0.141.1",
        "psycopg[binary]==3.3.4",
        "pydantic==2.12.5",
        "pydantic-ai-slim==2.32.1",
        "SQLAlchemy==2.0.52",
        "uvicorn==0.41.0",
    ]
    _need(
        errors,
        project.get("dependencies") == baseline_dependencies,
        "4A project dependency authority was rewritten",
    )
    _need(
        errors,
        project.get("optional-dependencies")
        == {"tranche-4d": ["dbos==2.30.0+eva.1", "PyNaCl==1.6.2"]},
        "pyproject tranche-4d dependency adoption is not exact",
    )
    tool = pyproject.get("tool", {}).get("eva2", {})
    _need(
        errors,
        tool.get("dependency-lock") == "requirements.lock"
        and tool.get("tranche-4d-dependency-lock") == "requirements-4d.lock"
        and tool.get("tranche-4d-dependency-extra") == "tranche-4d"
        and tool.get("tranche-4d-dependency-source-binding")
        == "release/4d-dependency-source-binding.json",
        "pyproject 4D lock/profile binding is not exact",
    )
    adoption = binding.get("pyprojectAdoption", {})
    _need(
        errors,
        adoption.get("sha256") == EXPECTED_PYPROJECT_SHA256
        and adoption.get("baselineProjectDependenciesRemainAuthorityFor4A") is True
        and adoption.get("exactExtraDependencies")
        == ["dbos==2.30.0+eva.1", "PyNaCl==1.6.2"]
        and adoption.get("silentStockFallbackAllowed") is False,
        "dependency binding does not match pyproject adoption",
    )


def _check_dependency_binding(
    root: Path, binding: dict[str, Any], errors: list[str]
) -> None:
    root_keys = {
        "contract",
        "status",
        "decisionDate",
        "targetRuntime",
        "baselineImmutability",
        "pyprojectAdoption",
        "standaloneLock",
        "dependencies",
        "wheelhouseInstallProtocol",
        "licenseAndSbomRequirements",
        "nonClaims",
    }
    _exact_keys(errors, binding, root_keys, "dependency binding")
    _need(
        errors,
        binding.get("contract") == "Eva24DDependencySourceBinding/v1"
        and binding.get("status") == "sealed_pre_install_not_installability_evidence",
        "dependency binding contract/status is not exact",
    )
    target_runtime = binding.get("targetRuntime", {})
    tier1 = target_runtime.get("tier1", [])
    _need(
        errors,
        target_runtime.get("python") == "CPython 3.12.14"
        and tier1
        == [
            {
                "os": "Ubuntu Server 24.04.4 LTS",
                "architecture": "amd64",
                "pythonAbi": "cp312",
            },
            {
                "os": "Ubuntu Server 24.04.4 LTS",
                "architecture": "arm64",
                "pythonAbi": "cp312",
            },
        ],
        "dependency target runtime or tier-1 architecture set is not exact",
    )
    baseline = binding.get("baselineImmutability", {})
    rows = [
        baseline.get("requirementsLock", {}),
        baseline.get("dependencyRoots", {}),
        baseline.get("pythonWheelCoverage", {}),
        baseline.get("licenseInventory", {}),
        *baseline.get("historicalEvidence", []),
    ]
    expected_subset = {
        path: digest
        for path, digest in EXPECTED_IMMUTABLE_FILES.items()
        if path.startswith("requirements")
        or path.startswith("release/dependency-roots")
        or path.startswith("release/python-wheel")
        or path.startswith("release/license")
        or path.startswith("evidence/")
    }
    actual_subset = {
        row.get("path"): row.get("sha256") for row in rows if isinstance(row, dict)
    }
    _need(
        errors,
        actual_subset == expected_subset,
        "historical dependency/evidence bindings differ",
    )
    _need(
        errors,
        all(row.get("mutatedBy4D") is False for row in rows),
        "historical input is marked mutable by 4D",
    )

    standalone = binding.get("standaloneLock", {})
    _need(
        errors,
        standalone.get("path") == LOCK_4D_PATH
        and standalone.get("sha256") == EXPECTED_4D_LOCK_SHA256
        and standalone.get("packageCount") == 39
        and standalone.get("requirementsIncludesAllowed") is False
        and standalone.get("indexDirectivesAllowed") is False
        and standalone.get("urlRequirementsAllowed") is False
        and standalone.get("unhashedRequirementsAllowed") is False
        and standalone.get("sdistInstallationAllowed") is False
        and standalone.get("systemLibsodiumFallbackAllowed") is False
        and standalone.get("baselineLockConsumedDuring4DInstall") is False,
        "standalone 4D lock boundary is not exact",
    )

    dependencies = binding.get("dependencies", {})
    dbos = dependencies.get("dbos", {})
    _need(
        errors,
        dbos.get("selectedVersion") == "2.30.0+eva.1"
        and dbos.get("outputWheel", {}).get("filename")
        == "dbos-2.30.0+eva.1-py3-none-any.whl"
        and dbos.get("outputWheel", {}).get("sha256")
        == "e20ca9a038eb2ae195b40484f3fa43c34e3f4c48797cabe6e5e387f01875b464"
        and dbos.get("patch", {}).get("sha256")
        == "85c43d878eb5264fa344e8ea65471013251f26483ab0dcc39944185f061d77dc"
        and dbos.get("patch", {}).get("patchedMemberSha256")
        == "af7032940190792f98a4a923f1c3632b245518068d98680bac7206940f4f8b5f"
        and dbos.get("postInstallAttestation", {}).get("stockDistributionPresent")
        is False,
        "patched DBOS source/output binding is not exact",
    )
    builder_command = dbos.get("builder", {}).get("exactInvocation", "")
    _need(
        errors,
        builder_command
        == "python tools/build_dbos_4c_wheel.py /verified/upstream/dbos-2.30.0-py3-none-any.whl /verified/output/dbos-eva1"
        and dbos.get("builder", {}).get("networkAllowed") is False,
        "patched DBOS build source/path is not exact",
    )

    pynacl = dependencies.get("pynacl", {})
    wheel_rows = pynacl.get("officialWheels", [])
    actual_wheels = {
        row.get("platform"): (row.get("filename"), row.get("sha256"))
        for row in wheel_rows
        if isinstance(row, dict)
    }
    _need(
        errors,
        pynacl.get("version") == "1.6.2"
        and pynacl.get("wheelOnly") is True
        and actual_wheels == EXPECTED_PYNACL_WHEELS
        and pynacl.get("bundledNativeComponent", {}).get("version") == "1.0.20-stable"
        and pynacl.get("bundledNativeComponent", {}).get("systemFallbackAllowed")
        is False,
        "official PyNaCl wheel/native binding is not exact",
    )
    availability = {row["platform"]: row["availability"] for row in wheel_rows}
    _need(
        errors,
        availability
        == {
            "linux_amd64": "tier1_selected",
            "linux_arm64": "tier1_selected",
            "windows_x86_64": "artifact_only_no_support_claim",
            "macos_universal2": "artifact_only_no_support_claim",
        },
        "PyNaCl artifact availability inflates platform support",
    )

    coverage = load_json_exact(_path(root, "release/python-wheel-coverage.json"))
    install = binding.get("wheelhouseInstallProtocol", {})
    target_rows = {
        row.get("architecture"): row
        for row in install.get("targets", [])
        if isinstance(row, dict)
    }
    for target in coverage.get("targets", []):
        architecture = target.get("architecture")
        if architecture not in EXPECTED_TARGET_MANIFESTS:
            continue
        selected: list[list[str]] = []
        for artifact in target.get("artifacts", []):
            filename = artifact["filename"]
            digest = artifact["sha256"]
            if filename.startswith("dbos-"):
                filename = "dbos-2.30.0+eva.1-py3-none-any.whl"
                digest = (
                    "e20ca9a038eb2ae195b40484f3fa43c34e3f4c48797cabe6e5e387f01875b464"
                )
            selected.append([filename, digest])
        platform = "linux_amd64" if architecture == "amd64" else "linux_arm64"
        selected.append(list(EXPECTED_PYNACL_WHEELS[platform]))
        selected.sort(key=lambda row: row[0].lower().replace("_", "-"))
        manifest_digest = sha256_bytes(canonical_json_bytes(selected))
        row = target_rows.get(architecture, {})
        expected_digest = EXPECTED_TARGET_MANIFESTS[architecture]
        expected_directory = f"/opt/eva2/wheelhouse/sha256-{expected_digest}"
        expected_command = (
            "/opt/eva2/installer-4d/bin/python -I -m pip --isolated "
            "--no-cache-dir --disable-pip-version-check --python "
            "/opt/eva2/runtime-4d install --no-index --only-binary=:all: "
            f"--require-hashes --find-links={expected_directory} "
            "-r requirements-4d.lock"
        )
        _need(
            errors,
            len(selected) == 39
            and manifest_digest == expected_digest
            and row.get("wheelCount") == 39
            and row.get("selectedManifestSha256") == expected_digest
            and row.get("contentAddressedWheelhouse") == expected_directory
            and row.get("exactInstallCommand") == expected_command,
            f"content-addressed wheelhouse/install binding differs for {architecture}",
        )
    _need(
        errors,
        set(target_rows) == set(EXPECTED_TARGET_MANIFESTS)
        and install.get("status") == "specified_not_executed_by_this_freeze"
        and "exactly the 39" in install.get("wheelhouseExactness", "")
        and "created with --without-pip" in install.get("cleanEnvironment", "")
        and "no inherited PYTHONPATH" in install.get("cleanEnvironment", ""),
        "wheelhouse protocol is incomplete",
    )
    installer = install.get("installer", {})
    _need(
        errors,
        installer
        == {
            "distribution": "pip",
            "version": "26.0.1",
            "wheelFilename": "pip-26.0.1-py3-none-any.whl",
            "wheelSha256": "bdb1b08f4274833d62c1aa29e20907365a2ceb950410df15fc9521bad440122b",
            "oneWheelManifestSha256": "42886744461abb1d9ab6792632c3d1788e486ce245f83fb99b3a7fd8c2ddbb0e",
            "contentAddressedWheelhouse": "/opt/eva2/installer-wheelhouse/sha256-42886744461abb1d9ab6792632c3d1788e486ce245f83fb99b3a7fd8c2ddbb0e",
            "installerEnvironment": "/opt/eva2/installer-4d",
            "provisionRule": "an independently receipt-bound CPython 3.12.14 installer environment must end with exactly pip 26.0.1 from this pre-hashed one-wheel no-index wheelhouse before it may touch the target; bootstrap pip is installer-only and not runtime authority",
            "configurationRule": "the installer receipt proves no pip configuration files, no PIP_* variables, no cache and no extra find-links or candidate locations; the exact command also uses --isolated, --no-cache-dir and --disable-pip-version-check",
            "targetCreateCommand": "/opt/eva2/cpython-3.12.14/bin/python -I -m venv --without-pip /opt/eva2/runtime-4d",
            "installerExcludedFromRuntimeDistributionCount": True,
            "pipOrWheelDistributionAllowedInTarget": False,
        },
        "external pip installer and pip-free target boundary is not exact",
    )
    post_install = install.get("requiredPostInstallChecks", [])
    _need(
        errors,
        len(post_install) == 7
        and any(
            "pip and wheel are absent from the target" in item
            and "installer tooling is counted separately" in item
            for item in post_install
        )
        and any(
            "both sealed Eva and Mira known-answer vectors" in item
            and EXPECTED_XCHACHA_KATS["Eva"]["envelopeSha256"] in item
            and EXPECTED_XCHACHA_KATS["Mira"]["envelopeSha256"] in item
            for item in post_install
        ),
        "post-install distribution and PyNaCl KAT checks are not exact",
    )

    components = binding.get("licenseAndSbomRequirements", {}).get("components", [])
    by_name = {row.get("name"): row for row in components if isinstance(row, dict)}
    _need(
        errors,
        set(by_name) == {"dbos", "PyNaCl", "libsodium", "cffi"}
        and by_name["PyNaCl"].get("wheelLicenseSha256")
        == "d3174ad63e721d4c9dccb8ad4320848992d314369bc46319720b5802c9153fe9"
        and by_name["libsodium"].get("wheelLicenseSha256")
        == "508a76d186356c0dd807a670ef510964f8724557024796a2c426c6c0e19ab683"
        and "CONTAINED_BY" in by_name["libsodium"].get("relationship", ""),
        "PyNaCl, libsodium, DBOS and cffi license/SBOM rows are not separate and exact",
    )
    nonclaims = binding.get("nonClaims", [])
    _need(
        errors,
        len(nonclaims) == 6
        and any("no dependency was downloaded" in item for item in nonclaims)
        and any("no clean-install" in item for item in nonclaims)
        and any("Windows and macOS" in item for item in nonclaims)
        and any("not an official upstream DBOS release" in item for item in nonclaims),
        "dependency nonclaims are incomplete",
    )


def verify(root: Path) -> dict[str, Any]:
    """Verify the complete freeze and return a deterministic receipt."""

    root = root.resolve(strict=True)
    errors: list[str] = []
    for relative, expected in EXPECTED_IMMUTABLE_FILES.items():
        try:
            actual = sha256_file(_path(root, relative))
        except (OSError, FreezeError) as exc:
            errors.append(str(exc))
            continue
        if actual != expected:
            errors.append(f"immutable input digest mismatch: {relative}")

    profile: dict[str, Any] | None = None
    binding: dict[str, Any] | None = None
    vertical: dict[str, Any] | None = None
    for label, relative in (
        ("profile", PROFILE_PATH),
        ("dependency binding", BINDING_PATH),
        ("vertical contract", VERTICAL_PATH),
    ):
        try:
            value = load_json_exact(
                _path(root, relative), allow_floats=(label == "vertical contract")
            )
        except (OSError, FreezeError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if label == "profile":
            profile = value
        elif label == "dependency binding":
            binding = value
        else:
            vertical = value

    artifacts: dict[str, dict[str, Any]] = {}
    for relative in ARTIFACT_PATHS:
        try:
            path = _path(root, relative)
            actual = sha256_file(path)
            artifact = load_json_exact(path)
        except (OSError, FreezeError) as exc:
            errors.append(f"pre-runtime artifact {relative}: {exc}")
            continue
        artifacts[relative] = artifact
        if actual != EXPECTED_ARTIFACT_SHA256[relative]:
            errors.append(f"pre-runtime artifact digest mismatch: {relative}")
        if artifact.get("contract") != EXPECTED_ARTIFACT_CONTRACTS[relative]:
            errors.append(f"pre-runtime artifact contract mismatch: {relative}")

    if profile is not None and vertical is not None:
        try:
            _check_profile(profile, vertical, errors)
        except (IndexError, KeyError, TypeError, ValueError, StopIteration) as exc:
            errors.append(f"profile structure failed closed: {exc}")
        semantic_core = dict(profile)
        semantic_core.pop("preRuntimeRequiredArtifacts", None)
        _need(
            errors,
            sha256_bytes(canonical_json_bytes(semantic_core))
            == EXPECTED_PROFILE_SEMANTIC_CORE_SHA256,
            "execution profile semantic core changed outside the adoption gate",
        )
    if binding is not None:
        try:
            _check_dependency_binding(root, binding, errors)
            _check_lock_and_pyproject(root, binding, errors)
        except (
            OSError,
            FreezeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"dependency structure failed closed: {exc}")

    artifact_checks = (
        (SCHEMA_ARTIFACT_PATH, _check_schema_artifact),
        (CONNECTOR_ARTIFACT_PATH, _check_connector_artifact),
        (MODEL_ARTIFACT_PATH, _check_model_artifact),
    )
    for relative, check in artifact_checks:
        artifact = artifacts.get(relative)
        if artifact is None:
            continue
        try:
            check(artifact, errors)
        except (IndexError, KeyError, TypeError, ValueError, StopIteration) as exc:
            errors.append(
                f"pre-runtime artifact structure failed closed: {relative}: {exc}"
            )
    model = artifacts.get(MODEL_ARTIFACT_PATH)
    connector = artifacts.get(CONNECTOR_ARTIFACT_PATH)
    if model is not None and connector is not None:
        try:
            _check_model_connector_crosswalk(model, connector, errors)
        except (IndexError, KeyError, TypeError, ValueError, StopIteration) as exc:
            errors.append(f"model/connector crosswalk failed closed: {exc}")
    schedule = artifacts.get(SCHEDULE_ARTIFACT_PATH)
    schema = artifacts.get(SCHEMA_ARTIFACT_PATH)
    if (
        schedule is not None
        and schema is not None
        and connector is not None
        and model is not None
    ):
        try:
            _check_schedule_artifact(
                root, schedule, schema, connector, model, profile, errors
            )
        except (
            OSError,
            FreezeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            StopIteration,
        ) as exc:
            errors.append(f"schedule artifact structure failed closed: {exc}")

    missing_pre_runtime_artifacts: list[str] = []
    runtime_coding_allowed = False
    if profile is not None:
        pre_runtime = profile.get("preRuntimeRequiredArtifacts", {})
        rows_by_path = {
            row.get("path"): row
            for row in pre_runtime.get("artifacts", [])
            if isinstance(row, dict)
        }
        missing_pre_runtime_artifacts = [
            relative
            for relative in ARTIFACT_PATHS
            if relative not in artifacts
            or rows_by_path.get(relative, {}).get("status") != "sealed_verified"
            or rows_by_path.get(relative, {}).get("expectedSha256")
            != EXPECTED_ARTIFACT_SHA256[relative]
        ]
        runtime_coding_allowed = (
            pre_runtime.get("runtimeCodingAllowed") is True
            and not missing_pre_runtime_artifacts
        )

    hashes: dict[str, str] = {}
    for relative in (
        PROFILE_PATH,
        BINDING_PATH,
        LOCK_4D_PATH,
        PYPROJECT_PATH,
        CHECKER_PATH,
        TEST_PATH,
        *ARTIFACT_PATHS,
    ):
        try:
            hashes[relative] = sha256_file(_path(root, relative))
        except (OSError, FreezeError) as exc:
            errors.append(f"freeze receipt hash missing for {relative}: {exc}")
    runtime_coding_allowed = runtime_coding_allowed and not errors
    return {
        "contract": "Eva24DPreCodeFreezeCheck/v1",
        "status": "passed" if not errors else "failed",
        "networkUsed": False,
        "databaseUsed": False,
        "serviceStarted": False,
        "dependenciesInstalled": False,
        "productRuntimeImported": False,
        "runtimeCodingAllowed": runtime_coding_allowed,
        "missingPreRuntimeArtifacts": missing_pre_runtime_artifacts,
        "verifiedHashes": hashes,
        "errors": sorted(set(errors)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the checkout containing this checker)",
    )
    args = parser.parse_args(argv)
    try:
        receipt = verify(args.root)
    except (OSError, FreezeError) as exc:
        receipt = {
            "contract": "Eva24DPreCodeFreezeCheck/v1",
            "status": "failed",
            "networkUsed": False,
            "databaseUsed": False,
            "serviceStarted": False,
            "dependenciesInstalled": False,
            "productRuntimeImported": False,
            "runtimeCodingAllowed": False,
            "missingPreRuntimeArtifacts": [],
            "verifiedHashes": {},
            "errors": [str(exc)],
        }
    sys.stdout.buffer.write(canonical_json_bytes(receipt, final_lf=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
