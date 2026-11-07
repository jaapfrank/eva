"""Recognize one maintained filesystem public contract behind unchanged grants.

The old authority hash is never rewritten. Only the six already admitted Eva
workspace operations can recognize the exact public 2.2.0 signatures. The
maintained deployment separately pins the release; gateway configuration version
is not treated as an upstream package version. Runtime
argument/output validation continues to use the real new provider schemas.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from eva.contracts.canonical_json import canonical_sha256

UPSTREAM_FILESYSTEM_VERSION = "2.2.0"
_ACTIVE_TOOLS = frozenset({"write_file", "read_text_file", "list_directory", "search_files", "get_file_info", "create_directory"})
_LEGACY_TOOL_NAMES = frozenset(['create_directory', 'directory_tree', 'edit_file', 'get_file_info', 'list_allowed_directories', 'list_directory', 'list_directory_with_sizes', 'move_file', 'read_file', 'read_media_file', 'read_multiple_files', 'read_text_file', 'search_files', 'write_file'])
_LEGACY_SIGNATURES = {'create_directory': {'annotations': {'destructiveHint': False,
                                      'idempotentHint': True,
                                      'openWorldHint': False,
                                      'readOnlyHint': False},
                      'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                       'properties': {'path': {'type': 'string'}},
                                       'required': ['path'],
                                       'type': 'object'},
                      'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                        'additionalProperties': False,
                                        'properties': {'content': {'type': 'string'}},
                                        'required': ['content'],
                                        'type': 'object'}},
 'get_file_info': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                   'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                    'properties': {'path': {'type': 'string'}},
                                    'required': ['path'],
                                    'type': 'object'},
                   'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'additionalProperties': False,
                                     'properties': {'content': {'type': 'string'}},
                                     'required': ['content'],
                                     'type': 'object'}},
 'list_directory': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                    'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'properties': {'path': {'type': 'string'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'read_text_file': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                    'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                     'properties': {'head': {'description': 'If provided, returns '
                                                                            'only the first N '
                                                                            'lines of the file',
                                                             'type': 'number'},
                                                    'path': {'type': 'string'},
                                                    'tail': {'description': 'If provided, returns '
                                                                            'only the last N lines '
                                                                            'of the file',
                                                             'type': 'number'}},
                                     'required': ['path'],
                                     'type': 'object'},
                    'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                      'additionalProperties': False,
                                      'properties': {'content': {'type': 'string'}},
                                      'required': ['content'],
                                      'type': 'object'}},
 'search_files': {'annotations': {'openWorldHint': False, 'readOnlyHint': True},
                  'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                   'properties': {'excludePatterns': {'default': [],
                                                                      'items': {'type': 'string'},
                                                                      'type': 'array'},
                                                  'path': {'type': 'string'},
                                                  'pattern': {'type': 'string'}},
                                   'required': ['path', 'pattern'],
                                   'type': 'object'},
                  'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                    'additionalProperties': False,
                                    'properties': {'content': {'type': 'string'}},
                                    'required': ['content'],
                                    'type': 'object'}},
 'write_file': {'annotations': {'destructiveHint': True,
                                'idempotentHint': True,
                                'openWorldHint': False,
                                'readOnlyHint': False},
                'input_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                 'properties': {'content': {'type': 'string'},
                                                'path': {'type': 'string'}},
                                 'required': ['path', 'content'],
                                 'type': 'object'},
                'output_schema': {'$schema': 'http://json-schema.org/draft-07/schema#',
                                  'additionalProperties': False,
                                  'properties': {'content': {'type': 'string'}},
                                  'required': ['content'],
                                  'type': 'object'}}}
_CURRENT_SIGNATURE_SHA256 = {'read_file': '201cfecb47a6db9eb34bd86c1c55724d63636ea651e9e2ab83df96427c666839', 'read_text_file': '201cfecb47a6db9eb34bd86c1c55724d63636ea651e9e2ab83df96427c666839', 'read_media_file': 'e0e3c8dc5ba8c5e38a998a4cdfe7c9d641bb5d1a0af4e4787ec5669dd0572ec8', 'read_multiple_files': 'e0f28dbfe4478234bfaf5043c22469c1b923e5e0fa1c8e35da4c70057ec5c924', 'write_file': '0be237edb57e32a6c04e0543eb309821d706caf10c9ee42f32fae9c3e13381a2', 'append_file': 'e209822e4c452f92c28d5e7093ec6359390c337d078c0883886644be8cdbdce7', 'edit_file': '4fee72b83b2033ce073ec516f29b2f162cd07facea8c31cc0607c1c192acff5a', 'create_directory': '53fb5789607b9aebd220aef5b627afb7a99e11ebf299bdb9ba64fecbda64e120', 'list_directory': '7146ed553bef574ba5dfb9014864130729a29503cc5e21c3f812fab7c7000d50', 'list_directory_with_sizes': 'fe378055d3feb20bd5ece2b5f569b4a4d9f5ffa91c9fbbb42ca42c3794b6dd00', 'directory_tree': '0588f73d7db222d6e39667722c77898ca5431db63afa0c90b53d5f53250a884a', 'move_file': '6b32d21cc4015eb8558dd7a39f75d5d3d60f955e7b17a46feaa19404011056e4', 'copy_file': 'f4b4b54bfd0299bc8d060294cbc7c5bf6cefc21764440e9a77471f685aba4e21', 'search_files': '366485d5005a71d46d82bfb752f6b6011b68c450fb6dd980fb6fde096b92acbb', 'get_file_info': '7146ed553bef574ba5dfb9014864130729a29503cc5e21c3f812fab7c7000d50', 'list_allowed_directories': 'b17c74c6026127917536099588bd7fbe6f69ab4b918d036a23c8979f657b1087'}


def _gateway(row: dict[str, Any]) -> str | None:
    value = row.get("gateway_id", row.get("gatewayId"))
    return value if isinstance(value, str) and value else None


def _original(row: dict[str, Any]) -> str | None:
    value = row.get("original_name", row.get("originalName"))
    return value if isinstance(value, str) and value else None


def _signature(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "annotations": row.get("annotations") or {},
        "input_schema": row.get("input_schema", row.get("inputSchema")),
        "output_schema": row.get("output_schema", row.get("outputSchema")),
    }


def _known_current_group(row: dict[str, Any], rows: list[Any]) -> bool:
    gateway = _gateway(row)
    if gateway is None:
        return False
    group = [item for item in rows if isinstance(item, dict) and _gateway(item) == gateway]
    names = [_original(item) for item in group]
    legacy = set(_LEGACY_TOOL_NAMES)
    current = _CURRENT_SIGNATURE_SHA256
    # ContextForge may retain the original fourteen registered routes. The two
    # newly advertised upstream routes confer no Eva authority in either case.
    if len(names) != len(set(names)) or set(names) not in (legacy, set(current)):
        return False
    return all(canonical_sha256(_signature(item)) == current[_original(item)] for item in group)


def _admitted_domains_are_compatible(schema: dict[str, Any], bindings: tuple[Any, ...]) -> bool:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or schema.get("additionalProperties") is not False:
        return False
    for binding in bindings:
        if binding.provider_name not in {"head", "tail"}:
            continue
        value = properties.get(binding.argument_name)
        if (not isinstance(value, dict) or value.get("type") != "integer"
                or not isinstance(value.get("minimum"), int) or isinstance(value["minimum"], bool)
                or value["minimum"] < 0
                or not isinstance(value.get("maximum"), int) or isinstance(value["maximum"], bool)
                or value["maximum"] > 9_007_199_254_740_991):
            return False
    return True


def compatible_filesystem_admission(
    *, row: dict[str, Any], rows: list[Any], selected: dict[str, Any],
    expected_sha256: str, admitted_schema: dict[str, Any], bindings: tuple[Any, ...],
) -> dict[str, Any] | None:
    """Return the identical old admission only for a known compatible domain.

    This is a recognition rule, not a new catalog, version negotiation, or an
    authority grant. Unknown vendor signatures and partially refreshed groups
    remain rejected by the caller's existing sealed-hash check.
    """
    name = _original(row)
    if (name not in _ACTIVE_TOOLS or not _known_current_group(row, rows)
            or not _admitted_domains_are_compatible(admitted_schema, bindings)):
        return None
    legacy = deepcopy(selected)
    legacy.update(deepcopy(_LEGACY_SIGNATURES[name]))
    if canonical_sha256(legacy) != expected_sha256:
        return None
    return legacy
