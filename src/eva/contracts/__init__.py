"""Strict Eva 2 language-neutral contracts.

Import contract types from their focused modules.  The closed registry and
canonical reader are exported here for wire boundaries and schema generation.
"""

from .base import (
    CANONICAL_JSON_PROFILE,
    JSON_SAFE_INTEGER_MAX,
    MAX_CONTRACT_BYTES,
    ContractModel,
    Version1Contract,
    Version2Contract,
    Version3Contract,
)
from .canonical_json import (
    DuplicateKeyError,
    canonical_json_bytes,
    canonical_sha256,
    contract_reader,
    decode_json_bytes,
    parse_contract_bytes,
    require_canonical_bytes,
)
from .registry import CONTRACT_REGISTRY, schema_filename

read_contract_bytes = contract_reader(CONTRACT_REGISTRY)

__all__ = [
    "CANONICAL_JSON_PROFILE",
    "CONTRACT_REGISTRY",
    "JSON_SAFE_INTEGER_MAX",
    "MAX_CONTRACT_BYTES",
    "ContractModel",
    "DuplicateKeyError",
    "Version1Contract",
    "Version2Contract",
    "Version3Contract",
    "canonical_json_bytes",
    "canonical_sha256",
    "decode_json_bytes",
    "parse_contract_bytes",
    "read_contract_bytes",
    "require_canonical_bytes",
    "schema_filename",
]
