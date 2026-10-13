"""Validate preserved gateway registrations for trusted read-only provider aliases.

No registration, database update, service start or activation is performed.
Only the approved provider IDs and endpoint/transport columns are inspected.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.portable_transport import MAX_ARCHIVE_BYTES

_SOURCE = "gateway/contextforge.sqlite"


class GatewayBindingValidationError(ValueError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("preserved gateway binding validation failed; no registrations changed")


def _configuration(configuration, connections, preparations):
    if (not isinstance(configuration, dict) or set(configuration) != {"target_path", "providers"}
            or not isinstance(configuration["target_path"], str)
            or not isinstance(configuration["providers"], list)):
        raise ValueError("gateway binding configuration differs")
    target = Path(configuration["target_path"])
    if (not target.is_absolute() or str(target) != configuration["target_path"]
            or target.resolve() != target or not target.name.endswith(".sqlite")):
        raise ValueError("gateway binding target must be absolute without links")
    if (not isinstance(connections, dict) or not isinstance(connections.get("providers"), list)
            or not isinstance(preparations, dict) or not isinstance(preparations.get("providers"), list)
            or not 1 <= len(preparations["providers"]) <= 16):
        raise ValueError("gateway binding provider configurations differ")
    expected = set(range(len(preparations["providers"])))
    indices, identifiers, aliases = set(), set(), set()
    for item in configuration["providers"]:
        if not isinstance(item, dict) or set(item) != {"preparation_index", "gateway_id", "provider_alias"}:
            raise ValueError("gateway provider binding fields differ")
        index, identifier, alias = item["preparation_index"], item["gateway_id"], item["provider_alias"]
        if type(index) is not int or index not in expected or index in indices:
            raise ValueError("gateway binding preparation coverage differs")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,35}", identifier)
                or identifier in identifiers):
            raise ValueError("gateway binding needs distinct exact gateway IDs")
        if (not isinstance(alias, str) or not 1 <= len(alias) <= 253 or alias in aliases
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in alias.split("."))):
            raise ValueError("gateway provider alias is not a distinct DNS hostname")
        try:
            ipaddress.ip_address(alias)
        except ValueError:
            pass
        else:
            raise ValueError("gateway provider alias must not be an IP address")
        indices.add(index); identifiers.add(identifier); aliases.add(alias)
    connection_indices = [item.get("preparation_index") for item in connections["providers"] if isinstance(item, dict)]
    if (indices != expected or len(connection_indices) != len(connections["providers"])
            or any(type(index) is not int for index in connection_indices)
            or len(connection_indices) != len(expected) or set(connection_indices) != expected):
        raise ValueError("gateway bindings require exact connection and preparation coverage")
    for item in configuration["providers"]:
        for connection in connections["providers"]:
            if (connection["preparation_index"] != item["preparation_index"]
                    and str(connection.get("container_name", "")).casefold() == item["provider_alias"]):
                raise ValueError("gateway alias overlaps another configured container name")
    return target


def validate_gateway_binding_configuration(configuration, *, connections, preparations):
    """Validate trusted launcher shape before any recovery import or connection."""
    _configuration(configuration, connections, preparations)


def _private_target_bytes(target):
    parent = target.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("restored gateway target parent must remain private")
    with _root_descriptor(target.parent) as fd:
        info = os.stat(target.name, dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}):
            raise ValueError("restored gateway target must remain a private regular file")
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                os.stat(target.name + suffix, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError("restored gateway has an active SQLite sidecar")
        return _read_regular(fd, target.name, maximum=MAX_ARCHIVE_BYTES)


def validate_gateway_bindings(package_root, *, configuration, connections, preparations, guard):
    receipt = {"contract": "EvaGatewayProviderBindings/v1", "providers": [],
               "database_modified": False, "registrations_changed": False,
               "services_started": False, "core_activated": False}
    try:
        target = _configuration(configuration, connections, preparations)
        if not callable(guard):
            raise ValueError("gateway bindings require a current restore guard")
        guard()
        root = Path(package_root).resolve()
        if target == root or root in target.parents:
            raise ValueError("restored gateway target must be separate from the archive")
        package = read_installation_package(root)
        entry = package["manifest"]["files"][_SOURCE]
        with _root_descriptor(root) as fd:
            source = _read_regular(fd, _SOURCE, maximum=MAX_ARCHIVE_BYTES)
        if len(source) != entry["size"] or hashlib.sha256(source).hexdigest() != entry["sha256"]:
            raise ValueError("gateway archive snapshot changed")
        restored = _private_target_bytes(target)
        if restored != source:
            raise ValueError("restored gateway bytes differ from approved archive")
        receipt.update(archive_sha256=package["archive_sha256"], gateway_sha256=entry["sha256"],
                       target_binding_sha256=hashlib.sha256(str(target).encode()).hexdigest())
        mapping = {}
        # SQLite reads an immutable private copy of exactly the authenticated
        # bytes, not a path that another process can replace after verification.
        with tempfile.TemporaryDirectory(prefix="eva-gateway-binding-") as temporary:
            snapshot = Path(temporary) / "verified.sqlite"
            snapshot.write_bytes(restored)
            snapshot.chmod(0o400)
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
                database.execute("PRAGMA query_only=ON")
                database.execute("PRAGMA trusted_schema=OFF")
                database.set_progress_handler(lambda: 1, 1_000_000)
                table = database.execute("SELECT type FROM sqlite_master WHERE name=?", ("gateways",)).fetchall()
                columns = {row[1] for row in database.execute("PRAGMA table_info(gateways)")}
                if table != [("table",)] or not {"id", "url", "transport"} <= columns:
                    raise ValueError("preserved ContextForge gateway schema differs")
                for item in configuration["providers"]:
                    endpoint = "http://" + item["provider_alias"] + ":9000/mcp"
                    # The OR rejects duplicate registrations of the same exact
                    # endpoint, even when the requested ID itself is unique.
                    rows = database.execute("SELECT id,url,transport FROM gateways WHERE id=? OR url=? LIMIT 3",
                                            (item["gateway_id"], endpoint)).fetchall()
                    if rows != [(item["gateway_id"], endpoint, "STREAMABLEHTTP")]:
                        raise ValueError("preserved gateway ID, unique endpoint or transport differs")
                    mapping[item["preparation_index"]] = item["provider_alias"]
                    receipt["providers"].append({"preparation_index": item["preparation_index"],
                        "gateway_id": item["gateway_id"],
                        "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest()})
        guard()
        if _private_target_bytes(target) != restored:
            raise ValueError("restored gateway changed during binding validation")
        with _root_descriptor(root) as fd:
            if _read_regular(fd, _SOURCE, maximum=MAX_ARCHIVE_BYTES) != source:
                raise ValueError("gateway archive changed during binding validation")
        guard()
        receipt["status"] = "validated_unchanged"
        return {"aliases": mapping, "receipt": receipt}
    except Exception as error:
        receipt["status"] = "validation_failed"
        raise GatewayBindingValidationError(receipt) from error
