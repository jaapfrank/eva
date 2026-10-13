from __future__ import annotations

from tools.postgres_portable_export import CANONICAL_EXPORT_SPECS


def test_portable_v1_is_not_a_generic_database_dump() -> None:
    """Secrets are deliberately redacted, so a restore needs its own adapter."""

    non_column_mappings = [
        (spec["table"], mapping["portableProperty"], mapping["source"]["kind"])
        for spec in CANONICAL_EXPORT_SPECS
        for mapping in spec["transform_spec"]["propertyMappings"]
        if mapping["source"]["kind"] != "column"
    ]

    assert non_column_mappings == [
        (
            "eva_core.model_attempts",
            "credential_grant_payload",
            "redactedContractColumn",
        ),
        (
            "eva_core.model_attempts",
            "credential_reauthorization_required",
            "credentialGrantPresence",
        ),
        ("eva_core.connector_bindings", "credential_reference", "literal"),
        (
            "eva_core.connector_bindings",
            "credential_reference_sha256",
            "sha256Utf8Column",
        ),
        (
            "eva_core.connector_bindings",
            "credential_reauthorization_required",
            "literal",
        ),
        ("eva_core.connector_bindings", "restore_status", "literal"),
        (
            "eva_core.connector_bindings",
            "payload",
            "redactedContractColumn",
        ),
        (
            "eva_core.connector_bindings",
            "redacted_payload_sha256",
            "sha256CanonicalPortableProperty",
        ),
        (
            "eva_effects.read_attempts",
            "credential_grant_payload",
            "redactedContractColumn",
        ),
        (
            "eva_effects.read_attempts",
            "credential_reauthorization_required",
            "credentialGrantPresence",
        ),
        (
            "eva_effects.effect_attempts",
            "credential_grant_payload",
            "redactedContractColumn",
        ),
        (
            "eva_effects.effect_attempts",
            "credential_reauthorization_required",
            "credentialGrantPresence",
        ),
    ]

    redactions = [
        mapping
        for spec in CANONICAL_EXPORT_SPECS
        for mapping in spec["transform_spec"]["propertyMappings"]
        if mapping["source"]["kind"] == "redactedContractColumn"
    ]
    assert len(redactions) == 4
    assert all(
        mapping["source"]["secretReplacement"] is None
        and mapping["source"]["credentialReauthorizationRequired"] is True
        for mapping in redactions
    )
