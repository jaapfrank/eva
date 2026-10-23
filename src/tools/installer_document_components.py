"""Build existing restore callback settings from saved document layouts.

Layouts select archive-relative data, never executable images or host paths.
Runtime images, owners, credentials and network are trusted installer inputs.
This builder neither creates resources nor imports data.
"""
from pathlib import Path
import json

from eva.contracts.canonical_json import canonical_sha256
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.installation_document_restore import validate_document_configuration, _relative, _preserved_gateway_catalog
from tools.installation_document_connection import validate_document_connection_configuration
from tools.installation_document_promotion import validate_document_promotion_configuration
from tools.installation_gateway_binding import validate_gateway_binding_configuration

_LAYOUT_FIELDS = {"original_binding", "catalog_path", "document_directory",
                  "journal_directory", "gateway_id", "provider_alias"}


def build_document_components(*, package_root, layouts, runtime_image, runtime_uid,
                              runtime_gid, credentials, network_id, namespace,
                              workspace, gateway_database, host_preparation=None):
    """Generate all four document callback configurations for one target.

    Provider layouts must come from the saved installation inventory. Ambiguous
    associations are rejected instead of guessing which journal belongs to a
    provider. Existing callback validators remain the execution contract.
    """
    root = Path(package_root)
    target = Path(workspace)
    if not target.is_absolute() or target.resolve() != target:
        raise ValueError("document target workspace must be absolute without links")
    if not isinstance(layouts, list) or len(layouts) > 16 or (not layouts and host_preparation is None):
        raise ValueError("saved document provider layouts required")
    if set(credentials) != {"jwt_secret_file", "encryption_secret_file", "admin_password_file"}:
        raise ValueError("explicit document runtime credentials required")
    package = read_installation_package(root)
    entries = package["manifest"]["files"]
    host_owned = frozenset()
    if host_preparation is not None:
        from tools.installation_host_extension_restore import _validate_prepared
        host_owned = _validate_prepared(package, Path(host_preparation))
    preparation, connections, bindings, promotion = [], [], [], []
    covered = set()
    document_roots = set()
    for index, layout in enumerate(layouts):
        if not isinstance(layout, dict) or set(layout) != _LAYOUT_FIELDS:
            raise ValueError("saved document layout differs")
        if layout["original_binding"] in host_owned or layout["catalog_path"] in host_owned:
            raise ValueError("host and document authority ownership overlaps")
        _relative(layout["original_binding"], "config")
        gateway_catalog = layout["catalog_path"] == "gateway/contextforge.sqlite"
        if not gateway_catalog:
            _relative(layout["catalog_path"], "config")
        for key in ("document_directory", "journal_directory"):
            _relative(layout[key], "documents")
        catalog_role = "gateway_database" if gateway_catalog else "provider_catalog"
        for key, role in (("original_binding", "provider_binding"), ("catalog_path", catalog_role)):
            if entries.get(layout[key], {}).get("role") != role:
                raise ValueError("saved document authority role differs")
        with _root_descriptor(root) as fd:
            binding = json.loads(_read_regular(fd, layout["original_binding"]))
        flat = binding.get("version") == 1
        if not flat and not (binding.get("version") == 2 and binding.get("journal_mode") == "per_path_prefix_v1"):
            raise ValueError("saved document journal layout is unsupported")
        project_keys = set()
        for key, roles in (("document_directory", {"document_artifact"}),
                           ("journal_directory", {"document_journal", "document_marker"})):
            prefix = layout[key] + "/"
            selected = {name for name in entries if name.startswith(prefix)}
            shared_documents = key == "document_directory" and layout[key] in document_roots
            if selected & host_owned:
                raise ValueError("host and document data ownership overlaps")
            if ((not selected and key != "journal_directory")
                    or (selected & covered and not shared_documents)
                    or any(entries[name]["role"] not in roles for name in selected)):
                raise ValueError("document layout coverage differs")
            covered.update(selected)
            if key == "document_directory":
                document_roots.add(layout[key])
            elif flat:
                if {name[len(prefix):] for name in selected} not in (set(), {"workspace-writer.journal"}):
                    raise ValueError("saved flat document journal inventory differs")
            else:
                project_keys = {name[len(prefix):].split("/")[0] for name in selected}
        with _root_descriptor(root) as fd:
            raw_catalog = _read_regular(fd, layout["catalog_path"])
            catalog = (_preserved_gateway_catalog(raw_catalog, layout["gateway_id"])
                       if gateway_catalog else json.loads(raw_catalog))
        preparation.append({**{key: layout[key] for key in ("original_binding", "catalog_path", "document_directory", "journal_directory")},
            **({"catalog_gateway_id": layout["gateway_id"]} if gateway_catalog else {}),
            "logical_catalog_sha256": canonical_sha256(catalog), "project_keys": sorted(project_keys),
            "runtime_uid": runtime_uid, "runtime_gid": runtime_gid, "image": runtime_image,
            "output_directory": str(target / f"document-{index}-relocation")})
        connections.append({"preparation_index": index, "container_name": f"{namespace}-documents-{index}-ro",
                            "network_id": network_id, **credentials})
        bindings.append({"preparation_index": index, "gateway_id": layout["gateway_id"],
                         "provider_alias": layout["provider_alias"]})
        promotion.append({"preparation_index": index, "container_name": f"{namespace}-documents-{index}-rw"})
    expected = {name for name, item in entries.items() if item["role"] in
                {"document_artifact", "document_journal", "document_marker"}}
    host_documents = host_owned & expected
    if covered & host_documents or covered | host_documents != expected:
        raise ValueError("saved layouts do not cover all retained documents")
    if not layouts:
        return {}
    result = {"document_recovery": {"providers": preparation},
              "document_connection": {"providers": connections},
              "document_gateway_binding": {"target_path": str(gateway_database), "providers": bindings},
              "document_write_access": {"providers": promotion}}
    validate_document_configuration(result["document_recovery"])
    validate_document_connection_configuration(result["document_connection"])
    validate_gateway_binding_configuration(result["document_gateway_binding"],
        connections=result["document_connection"], preparations=result["document_recovery"])
    validate_document_promotion_configuration(result["document_write_access"],
        connection_configuration=result["document_connection"], preparation_configuration=result["document_recovery"])
    return result
