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


PROFILE_PATH = "eva2/design/vertical/noor-4d-execution-profile-v1.json"
BINDING_PATH = "eva2/release/4d-dependency-source-binding.json"
LOCK_4D_PATH = "eva2/requirements-4d.lock"
PYPROJECT_PATH = "eva2/pyproject.toml"
CHECKER_PATH = "eva2/tools/check_4d_freeze.py"
TEST_PATH = "eva2/tests/design/test_4d_freeze.py"
VERTICAL_PATH = (
    "design/evidence/eva2-first-vertical-contract-"
    "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1.json"
)

EXPECTED_IMMUTABLE_FILES = {
    VERTICAL_PATH: "2c9ffb7a2ae6fd9b34cc4c29f73e6b3b2212f6a96232090ddbdbcfb02ad5b9a1",
    "design/fixtures/noor-client-ack-key-v1.json": "330476faf5f2317d0edfaa991cc7dd344711353188d6c504f9629a780d5f4e93",
    "design/evidence/eva2-step3-design-freeze-cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33.json": "cac255b606224a65c9c14906dca4f2b1257a688edbc45c631688529bf9adcb33",
    "docs/adr-0001-eva2-living-core.md": "73b53e392b8e3b65d1241bff46bb92144960a2620dbd45f3c9d3526d9f07910d",
    "docs/eva2-build-map-core-contracts.md": "877a0d25b637ef0f3bbfe3286ab40b5c40d7a0e50dbc3bf4af14504426bf0d63",
    "docs/eva2-build-map-platform.md": "244b2f2e397d4ce11e245d573b266dee443f77b85d0bd456d7e4b9cd66fa98f1",
    "docs/eva2-build-map-presence-evidence.md": "69d391ba4e3b3363c4d1d3cb6471c1510b68e974f5d8e61088356b6020328ed8",
    "eva2/requirements.lock": "89555d14acd8bceb8786dfa8539af29672899e8259e6a2b5352e27ef9219e8dc",
    "eva2/release/dependency-roots.in": "f6645e2710a3263c1d989caac095783abc30d9fe2bfc6a6fc85446b2a21334f4",
    "eva2/release/python-wheel-coverage.json": "9edd29074bd3967d2a3f1b7865f2d1f851c79b16169e3a03f3904fa3d9a0b64e",
    "eva2/release/license-inventory.json": "57bf2654176f170e75e91079faaea44e414ae1a7e380be413ae125ed76b94f86",
    "eva2/evidence/4a/4a-index-sha256-b7870f121c8da63b39d577bfbc9c8f3b91150d3ccef6a9484440d9d50ebfcc6f.json": "b7870f121c8da63b39d577bfbc9c8f3b91150d3ccef6a9484440d9d50ebfcc6f",
    "eva2/evidence/4b/postgresql-core-gate.json": "67952e3cba0729b4026078957913472668fd5842aae58194d6beac16ef837b76",
    "eva2/evidence/4c/postgresql-durability-gate.json": "8e89ca955a556f66316cc0480c0966f1bcaa217ee70f45eb532e7035be86f156",
    "eva2/vendor/dbos-2.30.0-eva1/manifest.json": "17f2089bd2c416a858c30109812f6c236ba32bf0c8e30ba9926e3ea009f2f939",
    "eva2/vendor/dbos-2.30.0-eva1/0001-bounded-sync-datasource.patch": "85c43d878eb5264fa344e8ea65471013251f26483ab0dcc39944185f061d77dc",
    "eva2/tools/build_dbos_4c_wheel.py": "284b0f8ca88691eb9a2fb4841608bbfa88b9d6d5afbf56a0db91a5cc91b8ed49",
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
        r"    # via -r eva2/release/dependency-roots\.in\n"
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
        {"runtimeCodingAllowed", "gateRule", "artifacts"},
        "pre-runtime required artifacts",
    )
    expected = [
        (
            "schema_export_mapping",
            "eva2/design/vertical/noor-4d-schema-export-freeze-v1.json",
            "Eva24DSchemaExportFreeze/v1",
            ("table/column", "JSONL", "DBOS-free generic-reader"),
        ),
        (
            "connector_provider_fixtures",
            "eva2/design/vertical/noor-4d-connector-provider-fixtures-v1.json",
            "Eva24DConnectorProviderFixtures/v1",
            (
                "five workers and six bindings",
                "poisoned-mail and ambiguous-provider bytes",
                "ExperienceSourceEvent/PresenceContext",
            ),
        ),
        (
            "deterministic_model_vectors",
            "eva2/design/vertical/noor-4d-model-vectors-v1.json",
            "Eva24DModelVectors/v1",
            ("PlannerRequest/model_input", "provider-429", "zero-hidden-retry"),
        ),
        (
            "concurrency_crash_schedules",
            "eva2/design/vertical/noor-4d-concurrency-crash-schedules-v1.json",
            "Eva24DConcurrencyCrashSchedules/v1",
            ("duplicate-overlap barrier", "fifteen crash", "terminal replay"),
        ),
    ]
    rows = dependency_gate.get("artifacts", [])
    _need(
        errors,
        dependency_gate.get("runtimeCodingAllowed") is False
        and "remains false while any listed artifact has expectedSha256 null"
        in dependency_gate.get("gateRule", "")
        and len(rows) == 4,
        "runtime coding is not fail-closed on the four missing design artifacts",
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
            and row.get("expectedSha256") is None
            and row.get("status") == "missing_blocking_dependency_not_implemented"
            and all(term in row.get("requiredContents", "") for term in terms),
            f"pre-runtime artifact is not exact and missing: {artifact_id}",
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
        and ack_fixture.get("path") == "design/fixtures/noor-client-ack-key-v1.json"
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
        == "sha256/{ciphertext_sha256[0:2]}/{ciphertext_sha256[2:64]}"
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


def _check_lock_and_pyproject(
    root: Path, binding: dict[str, Any], errors: list[str]
) -> None:
    baseline_path = _path(root, "eva2/requirements.lock")
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
        if path.startswith("eva2/requirements")
        or path.startswith("eva2/release/dependency-roots")
        or path.startswith("eva2/release/python-wheel")
        or path.startswith("eva2/release/license")
        or path.startswith("eva2/evidence/")
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
        == "python eva2/tools/build_dbos_4c_wheel.py /verified/upstream/dbos-2.30.0-py3-none-any.whl /verified/output/dbos-eva1"
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

    coverage = load_json_exact(_path(root, "eva2/release/python-wheel-coverage.json"))
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
            "-r eva2/requirements-4d.lock"
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

    if profile is not None and vertical is not None:
        try:
            _check_profile(profile, vertical, errors)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            errors.append(f"profile structure failed closed: {exc}")
    if binding is not None:
        try:
            _check_dependency_binding(root, binding, errors)
            _check_lock_and_pyproject(root, binding, errors)
        except (OSError, FreezeError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"dependency structure failed closed: {exc}")

    missing_pre_runtime_artifacts: list[str] = []
    runtime_coding_allowed = False
    if profile is not None:
        pre_runtime = profile.get("preRuntimeRequiredArtifacts", {})
        missing_pre_runtime_artifacts = [
            row.get("path", "")
            for row in pre_runtime.get("artifacts", [])
            if isinstance(row, dict)
            and (
                row.get("expectedSha256") is None
                or row.get("status") == "missing_blocking_dependency_not_implemented"
            )
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
    ):
        try:
            hashes[relative] = sha256_file(_path(root, relative))
        except (OSError, FreezeError) as exc:
            errors.append(f"freeze receipt hash missing for {relative}: {exc}")
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
        default=Path(__file__).resolve().parents[2],
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
