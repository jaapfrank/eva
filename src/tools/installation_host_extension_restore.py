"""Prepare retained host-extension bytes without installing or starting them.

The existing installation package remains the source contract. This adapter only
owns explicitly selected host paths and expands original dependency captures into
an absent private inactive directory. Runtime/library/ABI/token admission is later.
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import stat
import tarfile
from uuid import uuid4

from tools.installation_recovery_package import read_installation_package
from tools.installation_recovery_materializer import _private_operator_directory, _rename_absent
from tools.portable_transport import (
    MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES, MAX_INSTALLATION_ARCHIVE_BYTES,
    TransportPolicy, _BoundedTransportTarInfo, _safe_relative_path, _unpack_plaintext_tar,
)
from tools.read_eva_portable_archive import _root_descriptor, _scan_regular_files, _read_regular

CONTRACT = "EvaInactiveHostExtension/v1"
_CAPTURE = "EvaExistingDependencyByteCapture/v1"
_RECEIPT = "host-preparation.json"
_MAX_METADATA = 16_777_216
_MAX_TAR = MAX_INSTALLATION_ARCHIVE_BYTES
_PREFIXES = ("documents/host-executor/", "documents/face-backend/",
             "documents/household-procedure/", "documents/runtime-images/",
             "config/host-executor/", "config/household-procedure/", "config/face-backend/")
_ROLES = {"document_artifact", "document_marker", "document_journal", "provider_binding"}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("host preparation metadata is invalid") from error


def _encoded(value):
    # This private receipt can list more than1024 SDK files. It is not a new
    # installation/transport format and carries no execution authority.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _configuration(entries, configuration):
    if (not isinstance(configuration, dict)
            or set(configuration) not in ({"retained_paths", "dependency_bundles"},
                {"retained_paths", "dependency_bundles", "sdk_assembly"})):
        raise ValueError("host preparation requires explicit retained paths and dependency bundles")
    paths = configuration["retained_paths"]
    if (not isinstance(paths, list) or not paths or len(paths) > MAX_ARCHIVE_FILES
            or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths))):
        raise ValueError("host retained inventory is invalid")
    for name in paths:
        _safe_relative_path(name)
        if (not name.startswith(_PREFIXES) or name not in entries
                or entries[name]["role"] not in _ROLES):
            raise ValueError("host ownership is outside the retained extension")
    expected = {name for name in entries if name.startswith(_PREFIXES)}
    if set(paths) != expected:
        raise ValueError("host ownership must cover its complete retained extension inventory")
    bundles = configuration["dependency_bundles"]
    if not isinstance(bundles, list) or len(bundles) > 8:
        raise ValueError("host dependency bundle inventory is invalid")
    seen, archives, inventories = set(), set(), set()
    for bundle in bundles:
        if not isinstance(bundle, dict) or set(bundle) != {"name", "archive", "inventory"}:
            raise ValueError("host dependency bundle fields differ")
        name = bundle["name"]
        if (not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None
                or name in seen):
            raise ValueError("host dependency destination is invalid")
        seen.add(name)
        for key, role in (("archive", "document_artifact"), ("inventory", "document_marker")):
            value = bundle[key]
            if value not in paths or "/dependencies/" not in value or entries[value]["role"] != role:
                raise ValueError("host dependency authority role differs")
        if bundle["archive"] in archives or bundle["inventory"] in inventories:
            raise ValueError("host dependency bundle authority is duplicated")
        archives.add(bundle["archive"]); inventories.add(bundle["inventory"])
    expected_archives = {p for p in paths if "/dependencies/" in p and p.endswith(".tar.gz")}
    expected_inventories = {p for p in paths if "/dependencies/" in p and p.endswith(".manifest.json")}
    if archives != expected_archives or inventories != expected_inventories:
        raise ValueError("host dependency bundles do not cover the retained captures")
    return frozenset(paths), bundles


def _inventory(raw, bundle, entries):
    data = _json(raw)
    if (not isinstance(data, dict) or data.get("contract") != _CAPTURE
            or not isinstance(data.get("archive"), dict) or not isinstance(data.get("files"), dict)):
        raise ValueError("original host dependency capture contract differs")
    archive = data["archive"]; files = data["files"]
    if (archive.get("packagePath") != bundle["archive"]
            or archive.get("sha256") != entries[bundle["archive"]]["sha256"]
            or archive.get("bytes") != entries[bundle["archive"]]["size"]
            or type(data.get("fileCount")) is not int or data["fileCount"] != len(files)
            or not 0 < len(files) <= MAX_ARCHIVE_FILES):
        raise ValueError("original host dependency capture provenance differs")
    total = 0
    for name, record in files.items():
        _safe_relative_path(name)
        if (not isinstance(record, dict) or type(record.get("bytes")) is not int
                or not 0 <= record["bytes"] <= MAX_ARCHIVE_BYTES
                or not isinstance(record.get("sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", record["sha256"]) is None):
            raise ValueError("original host dependency file inventory differs")
        total += record["bytes"]
    if (type(data.get("sourcePayloadBytes")) is not int
            or data["sourcePayloadBytes"] != total or total > MAX_INSTALLATION_ARCHIVE_BYTES):
        raise ValueError("original host dependency payload budget differs")
    return files


def _sdk_assembly(entries, configuration, read_metadata, inventories):
    """Bind each inactive SDK file to original RECORD bytes and one retained source."""
    if "sdk_assembly" not in configuration:
        return {}
    assembly = configuration["sdk_assembly"]
    if (not isinstance(assembly, dict)
            or set(assembly) != {"name", "reference_directory", "bundle_names"}):
        raise ValueError("SDK assembly requires its original metadata and bundle order")
    name, reference, bundles = (assembly[k] for k in ("name", "reference_directory", "bundle_names"))
    if (not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) is None
            or reference != "documents/host-executor/venv/distribution-metadata"
            or not isinstance(bundles, list) or not bundles
            or any(not isinstance(b, str) or b not in inventories for b in bundles)
            or len(bundles) != len(set(bundles))):
        raise ValueError("SDK assembly source selection is invalid")
    metadata = {p: entry for p, entry in entries.items() if p.startswith(reference + "/")}
    distributions = {}
    for path, entry in metadata.items():
        parts = path[len(reference) + 1:].split("/")
        if (len(parts) != 2 or not parts[0].endswith(".dist-info")
                or parts[1] not in {"METADATA", "WHEEL", "RECORD"}
                or path not in configuration["retained_paths"]):
            raise ValueError("SDK original distribution metadata is incomplete")
        distributions.setdefault(parts[0], set()).add(parts[1])
    if not distributions or any(parts != {"METADATA", "WHEEL", "RECORD"} for parts in distributions.values()):
        raise ValueError("SDK original distribution metadata is incomplete")
    site = "lib/python3.12/site-packages/"
    required, row_count = {}, 0
    for distribution in sorted(distributions):
        record_name = reference + "/" + distribution + "/RECORD"
        raw = read_metadata(record_name)
        if len(raw) != entries[record_name]["size"] or _sha(raw) != entries[record_name]["sha256"]:
            raise ValueError("SDK original RECORD changed")
        try:
            rows = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
            for row in rows:
                row_count += 1
                if row_count > MAX_ARCHIVE_FILES or len(row) != 3:
                    raise ValueError("SDK original RECORD inventory exceeds its closed bounds")
                relative, encoded_hash, size = row
                if not relative or relative.startswith("/") or "\\" in relative:
                    raise ValueError("SDK original RECORD path is invalid")
                relative = posixpath.normpath(site + relative)
                _safe_relative_path(relative)
                if "__pycache__" in relative.split("/") or relative.endswith(".pyc"):
                    continue
                self_record = relative == site + distribution + "/RECORD"
                if self_record and encoded_hash == "" and size == "":
                    item = {"sha256": _sha(raw), "bytes": len(raw)}
                else:
                    if (not encoded_hash.startswith("sha256=") or not size.isascii()
                            or not size.isdecimal() or len(size) > 10):
                        raise ValueError("SDK original RECORD has an unverified non-cache file")
                    value = encoded_hash[7:]
                    if re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
                        raise ValueError("SDK original RECORD digest is invalid")
                    decoded = base64.urlsafe_b64decode(value + "=")
                    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
                        raise ValueError("SDK original RECORD digest is not canonical")
                    item = {"sha256": decoded.hex(), "bytes": int(size)}
                    if item["bytes"] > MAX_ARCHIVE_BYTES:
                        raise ValueError("SDK original RECORD file exceeds its fixed limit")
                if relative in required and required[relative] != item:
                    raise ValueError("SDK original RECORD owners disagree")
                required[relative] = item
        except (UnicodeError, csv.Error) as error:
            raise ValueError("SDK original RECORD encoding is invalid") from error
        for leaf in ("METADATA", "WHEEL", "RECORD"):
            path = reference + "/" + distribution + "/" + leaf
            relative = site + distribution + "/" + leaf
            item = {"sha256": entries[path]["sha256"], "bytes": entries[path]["size"]}
            if relative in required and required[relative] != item:
                raise ValueError("SDK original RECORD differs from retained metadata")
            required[relative] = item
    selected = {}
    for relative, item in sorted(required.items()):
        source = None
        for bundle in bundles:
            captured = inventories[bundle].get(relative)
            if captured and all(captured[k] == item[k] for k in ("sha256", "bytes")):
                source = "expanded/" + bundle + "/" + relative
                break
        if source is None and relative.startswith(site):
            fallback = reference + "/" + relative[len(site):]
            entry = metadata.get(fallback)
            if entry and entry["sha256"] == item["sha256"] and entry["size"] == item["bytes"]:
                source = "retained/" + fallback
        if source is None:
            raise ValueError("SDK original RECORD payload is missing from retained bundles")
        selected["assembled/" + name + "/" + relative] = {**item, "mode": "0600", "source": source}
    return selected


def _expected_files(entries, configuration, read_inventory):
    paths, bundles = _configuration(entries, configuration)
    expected = {"retained/" + name: {"sha256": entries[name]["sha256"],
                "bytes": entries[name]["size"], "mode": entries[name]["mode"]}
                for name in paths}
    inventories = {}
    for bundle in bundles:
        files = _inventory(read_inventory(bundle["inventory"]), bundle, entries)
        inventories[bundle["name"]] = files
        for name, item in files.items():
            relative = "expanded/" + bundle["name"] + "/" + name
            expected[relative] = {"sha256": item["sha256"], "bytes": item["bytes"], "mode": "0600"}
    assembly = _sdk_assembly(entries, configuration, read_inventory, inventories)
    for name, item in assembly.items():
        expected[name] = {k: item[k] for k in ("sha256", "bytes", "mode")}
    if (len(expected) + 1 > MAX_ARCHIVE_FILES
            or sum(item["bytes"] for item in expected.values()) > MAX_INSTALLATION_ARCHIVE_BYTES):
        raise ValueError("inactive host inventory exceeds its fixed preparation budget")
    return paths, bundles, inventories, expected, assembly


def _write_new(path, raw, mode):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent); parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)


def _check_tar_inventory(path, expected):
    found = set()
    with tarfile.open(path, "r:", tarinfo=_BoundedTransportTarInfo) as archive:
        while True:
            member = archive.next()
            if member is None:
                break
            _safe_relative_path(member.name)
            if (not member.isfile() or member.name in found or member.name not in expected
                    or member.size != expected[member.name]["bytes"]):
                raise ValueError("dependency tar does not match its closed original inventory")
            found.add(member.name)
            if len(found) > MAX_ARCHIVE_FILES:
                raise ValueError("dependency tar member limit exceeded")
            archive.members.clear()
        # Only ordinary tar termination/padding may follow the last member.
        trailer = archive.fileobj.read(10_241)
        if len(trailer) > 10_240 or any(trailer):
            raise ValueError("dependency tar contains trailing unlisted payload")
    if found != set(expected):
        raise ValueError("dependency tar omits original files")


def _decompress(raw, tar_path, *, limit=_MAX_TAR):
    # Tests may select a smaller budget. No caller can raise the fixed ceiling.
    if type(limit) is not int or not 0 < limit <= _MAX_TAR:
        raise ValueError("dependency decompression budget is invalid")
    total, created = 0, False
    try:
        descriptor = os.open(tar_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as compressed:
                while chunk := compressed.read(1_048_576):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("dependency gzip exceeds its fixed decompression budget")
                    output.write(chunk)
            output.flush(); os.fsync(output.fileno())
    except BaseException:
        if created:
            tar_path.unlink(missing_ok=True)
        raise
    return total


def _expand(raw, tar_path, destination, expected):
    written = False
    try:
        total = _decompress(raw, tar_path)
        written = True
        _check_tar_inventory(tar_path, expected)
        destination.mkdir(mode=0o700)
        _unpack_plaintext_tar(tar_path, destination, transport_policy=TransportPolicy.INSTALLATION_RECOVERY)
        for current, directories, _ in os.walk(destination, followlinks=False):
            os.chmod(current, 0o700)
        with _root_descriptor(destination) as fd:
            if _scan_regular_files(fd) != set(expected):
                raise ValueError("expanded dependency inventory differs")
            for name, entry in expected.items():
                value = _read_regular(fd, name, maximum=MAX_ARCHIVE_BYTES)
                if len(value) != entry["bytes"] or _sha(value) != entry["sha256"]:
                    raise ValueError("expanded dependency bytes differ from original capture")
    finally:
        if written:
            tar_path.unlink(missing_ok=True)
    return total


def _validate_prepared(package, destination):
    """Internal handoff of this invocation's already validated parent package."""
    root = Path(destination)
    with _root_descriptor(root) as fd:
        _private_operator_directory(fd)
        raw = _read_regular(fd, _RECEIPT, maximum=_MAX_METADATA)
        receipt = _json(raw)
        fields = {"contract", "status", "sourceArchiveSha256", "configuration", "files", "expandedTarBytes",
                  "runtimeActivated", "importsOrInferencePerformed", "credentialsRebound",
                  "originalBindingsPreserved", "derivedBindings", "activationBlockers", "sdkAssembly"}
        if (not isinstance(receipt, dict) or set(receipt) != fields or receipt.get("contract") != CONTRACT
                or receipt.get("status") != "prepared_inactive"
                or receipt.get("sourceArchiveSha256") != package["archive_sha256"]
                or any(receipt.get(flag) is not False for flag in
                       ("runtimeActivated", "importsOrInferencePerformed", "credentialsRebound"))
                or receipt.get("originalBindingsPreserved") is not True or receipt.get("derivedBindings") != []
                or _encoded(receipt) != raw):
            raise ValueError("host preparation receipt differs")
        entries = package["manifest"]["files"]
        read_inventory = lambda name: _read_regular(fd, "retained/" + name, maximum=_MAX_METADATA)
        paths, _, _, expected, assembly = _expected_files(entries, receipt["configuration"], read_inventory)
        if receipt.get("sdkAssembly") != assembly:
            raise ValueError("SDK assembled source provenance differs")
        if receipt.get("files") != expected or _scan_regular_files(fd) != set(expected) | {_RECEIPT}:
            raise ValueError("host preparation closed inventory differs")
        for name, entry in expected.items():
            value = _read_regular(fd, name, maximum=MAX_ARCHIVE_BYTES)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (len(value) != entry["bytes"] or _sha(value) != entry["sha256"]
                    or info.st_uid != os.geteuid() or info.st_gid != os.getegid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != int(entry["mode"], 8)):
                raise ValueError("host prepared bytes or inactive ownership differ")
        for current, directories, _ in os.walk(root, followlinks=False):
            info = os.stat(current, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                    or info.st_uid != os.geteuid() or info.st_gid != os.getegid()):
                raise ValueError("host preparation directory is not private operator-owned data")
        if _read_regular(fd, _RECEIPT, maximum=_MAX_METADATA) != raw:
            raise ValueError("host preparation receipt changed")
    return paths


def read_host_preparation(package_root, destination, *, identity_trust=None, managed_household=None):
    package = read_installation_package(Path(package_root), identity_trust=identity_trust)
    result = _validate_prepared(package, Path(destination))
    if managed_household is not None:
        _managed_household(Path(package_root), package, managed_household, materialize=False)
    return result


def prepare_host_extension(package_root, destination, *, configuration, verify_guard, identity_trust=None,
                           managed_household=None):
    if not callable(verify_guard):
        raise ValueError("host preparation requires a current trusted operator guard")
    destination = Path(destination)
    if not destination.is_absolute() or destination.resolve() != destination:
        raise ValueError("host preparation target must be an absolute new operator path")
    verify_guard()
    package = read_installation_package(Path(package_root), identity_trust=identity_trust)
    entries = package["manifest"]["files"]
    with _root_descriptor(Path(package_root)) as source_fd, _root_descriptor(destination.parent) as parent_fd:
        _private_operator_directory(parent_fd)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("host preparation target already exists")
        paths, bundles, inventories, expected, assembly = _expected_files(entries, configuration,
            lambda name: _read_regular(source_fd, name, maximum=_MAX_METADATA))
        payload_bytes = sum(item["bytes"] for item in expected.values())
        if shutil.disk_usage(destination.parent).free < payload_bytes + _MAX_TAR + _MAX_METADATA:
            raise ValueError("insufficient disk capacity for inactive host preparation")
        staging_name = ".host-inactive-" + uuid4().hex
        os.mkdir(staging_name, mode=0o700, dir_fd=parent_fd)
        staging = Path(f"/proc/self/fd/{parent_fd}/{staging_name}")
        published = False
        try:
            for name in sorted(paths):
                raw = _read_regular(source_fd, name, maximum=MAX_ARCHIVE_BYTES)
                if len(raw) != entries[name]["size"] or _sha(raw) != entries[name]["sha256"]:
                    raise ValueError("retained host source changed")
                _write_new(staging / "retained" / name, raw, int(entries[name]["mode"], 8))
            (staging / "expanded").mkdir(mode=0o700)
            expanded = {}
            for bundle in bundles:
                verify_guard()
                raw = _read_regular(source_fd, bundle["archive"], maximum=MAX_ARCHIVE_BYTES)
                if _sha(raw) != entries[bundle["archive"]]["sha256"]:
                    raise ValueError("retained dependency archive changed")
                expanded[bundle["name"]] = _expand(raw, staging / ("." + bundle["name"] + ".tar"),
                    staging / "expanded" / bundle["name"], inventories[bundle["name"]])
            with _root_descriptor(staging) as staging_fd:
                for name, item in assembly.items():
                    raw = _read_regular(staging_fd, item["source"], maximum=MAX_ARCHIVE_BYTES)
                    if len(raw) != item["bytes"] or _sha(raw) != item["sha256"]:
                        raise ValueError("SDK assembly source changed")
                    _write_new(staging / name, raw, 0o600)
            receipt = {"contract": CONTRACT, "status": "prepared_inactive",
                "sourceArchiveSha256": package["archive_sha256"], "configuration": configuration,
                "files": expected, "expandedTarBytes": expanded, "runtimeActivated": False,
                "importsOrInferencePerformed": False, "credentialsRebound": False,
                "originalBindingsPreserved": True, "derivedBindings": [], "sdkAssembly": assembly,
                "activationBlockers": ["host_sdk_payload_closure_not_admitted", "sdk_launchers_and_pth_not_activated", "pinned_eva2_library_not_admitted",
                    "host_cpython_abi_not_admitted", "service_tokens_not_provisioned", "runtime_bindings_not_authorized"]}
            raw = _encoded(receipt)
            if len(raw) > _MAX_METADATA or payload_bytes + len(raw) > MAX_INSTALLATION_ARCHIVE_BYTES:
                raise ValueError("host preparation receipt exceeds complete byte budget")
            _write_new(staging / _RECEIPT, raw, 0o400)
            _validate_prepared(package, staging)
            verify_guard()
            _rename_absent(parent_fd, staging_name, destination.name)
            published = True
            os.fsync(parent_fd)
        finally:
            if not published:
                shutil.rmtree(staging)
    result = {"status": "prepared_inactive", "sourceArchiveSha256": package["archive_sha256"],
              "retainedFileCount": len(paths), "preparedFileCount": len(expected),
              "preparedBytes": payload_bytes, "receiptSha256": _sha(raw),
              "runtimeActivated": False, "functionalHostRestoreProven": False}
    if managed_household is not None:
        # Trusted operator destination/owner are deliberately NOT archive authority.
        # Failure leaves the already prepared extension inactive, never started.
        result["managedHousehold"] = _managed_household(Path(package_root), package,
            managed_household, materialize=True, verify_guard=verify_guard)
    return result



def _managed_owner(value):
    if (not isinstance(value, dict) or set(value) != {'uid', 'gid'}
            or any(type(number) is not int or not 0 <= number < 4294967295 for number in value.values())
            or (os.geteuid() != 0 and value != {'uid': os.geteuid(), 'gid': os.getegid()})):
        raise ValueError('managed household requires a trusted numeric runtime owner')
    return value


def _managed_payload(package_root, package, configuration):
    """Check exact original references; this does not grant source or PG adoption rights."""
    from eva.adapters.postgres.household_provisions import PostgresHouseholdProvisions, procedure_provision_id
    from eva.contracts.canonical_json import canonical_sha256
    fields = {'destination', 'runtime_owner', 'files', 'configuration', 'review', 'enrollment'}
    if not isinstance(configuration, dict) or set(configuration) != fields:
        raise ValueError('managed household operator selection differs')
    owner = _managed_owner(configuration['runtime_owner'])
    target = Path(configuration['destination'])
    if (not target.is_absolute() or target.resolve() != target
            or target == package_root or package_root in target.parents or target in package_root.parents):
        raise ValueError('managed household target must be an independent absent private path')
    mapping = configuration['files']
    if (not isinstance(mapping, dict) or not 4 <= len(mapping) <= MAX_ARCHIVE_FILES
            or len(set(mapping.values())) != len(mapping)):
        raise ValueError('managed household selected file inventory differs')
    entries = package['manifest']['files']; payload = {}
    with _root_descriptor(package_root) as source:
        if _read_regular(source, 'archive.json') != package['manifest_raw']:
            raise ValueError('managed household package manifest changed')
        for relative, archived in mapping.items():
            _safe_relative_path(relative); _safe_relative_path(archived)
            if (not archived.startswith(('documents/household-procedure/', 'config/household-procedure/'))
                    or archived not in entries or entries[archived]['role'] not in _ROLES):
                raise ValueError('managed household source is outside the retained procedure')
            raw = _read_regular(source, archived, maximum=65536)
            if len(raw) != entries[archived]['size'] or _sha(raw) != entries[archived]['sha256']:
                raise ValueError('managed household retained source changed')
            payload[relative] = raw
    names = [configuration[key] for key in ('configuration', 'review', 'enrollment')]
    if len(set(names)) != 3 or any(name not in payload for name in names):
        raise ValueError('managed household exact configuration, review and enrollment required')
    config_path, review_path, enrollment_path = map(Path, names)
    if (config_path.parent != review_path.parent or config_path.name != 'current.json'
            or review_path.name != 'adoption-review.json'):
        raise ValueError('managed household source and review must remain siblings')
    for key, role in (('configuration', 'provider_binding'), ('review', 'document_marker'),
                      ('enrollment', 'provider_binding')):
        if entries[mapping[configuration[key]]]['role'] != role:
            raise ValueError('managed household source role differs')
    config, review, enrollment = (_json(payload[name]) for name in names)
    def check_source(bound, code, review_raw):
        if (not isinstance(bound, dict) or bound.get('contract') not in
                {'EvaHouseholdProcedureBinding/v1', 'EvaHouseholdProcedureBinding/v2'}
                or not isinstance(bound.get('household_binding'), dict)
                or not isinstance(bound.get('source_principal_id'), str)
                or not isinstance(bound.get('reference_id'), str)):
            raise ValueError('managed household source binding differs')
        name = bound.get('source_file')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.py'):
            raise ValueError('managed household source must be a sibling Python file')
        parsed = _json(review_raw)
        if (not isinstance(parsed, dict) or parsed.get('contract') != 'EvaIndependentReview/v1'
                or parsed.get('verdict') != 'GO' or parsed.get('source', {}).get('sha256') != bound.get('source_sha256')
                or _sha(code) != bound.get('source_sha256') or _sha(review_raw) != bound.get('review_sha256')):
            raise ValueError('managed household source or review hashes differ')
        return name
    if not isinstance(config, dict):
        raise ValueError('managed household source configuration differs')
    current_name = config.get('source_file')
    current_path = str(config_path.parent / current_name) if isinstance(current_name, str) else ''
    if current_path not in payload:
        raise ValueError('managed household current source is missing')
    check_source(config, payload[current_path], payload[names[1]])
    if entries[mapping[current_path]]['role'] != 'document_artifact':
        raise ValueError('managed household current source role differs')
    provision_id = procedure_provision_id(config)
    pointer = enrollment.get('household_provision_enrollment') if isinstance(enrollment, dict) else None
    if (not isinstance(enrollment, dict) or set(enrollment) != {'household_provision_enrollment'}):
        raise ValueError('managed household enrollment differs from the same source')
    # An explicit null is the actual unregistered installation state. Preserve
    # it byte-for-byte; materialization must never register/adopt on its behalf.
    if pointer is not None and (not isinstance(pointer, dict) or set(pointer) != {
                'contract', 'provision_id', 'owner_principal_id', 'binding_sha256'}
            or pointer['contract'] != 'EvaHouseholdProvisionEnrollment/v1'
            or pointer['provision_id'] != provision_id
            or pointer['owner_principal_id'] != config['source_principal_id']
            or pointer['binding_sha256'] != canonical_sha256(config)):
        raise ValueError('managed household enrollment differs from the same source')
    allowed = set(names) | {current_path}; version_count = 0
    for relative, raw in payload.items():
        path = Path(relative)
        if path.parent != config_path.parent / 'versions':
            continue
        if (re.fullmatch(r'[a-f0-9]{64}\.json', path.name) is None
                or entries[mapping[relative]]['role'] != 'document_marker'):
            raise ValueError('managed household retained version name or role differs')
        bundle = _json(raw)
        if (not isinstance(bundle, dict) or set(bundle) != {'contract', 'configuration', 'source', 'review', 'origin'}
                or bundle['contract'] != 'EvaHouseholdProvisionVersion/v1'
                or not isinstance(bundle['source'], str) or not isinstance(bundle['review'], str)):
            raise ValueError('managed household retained version differs')
        bound = bundle['configuration']; code = bundle['source'].encode(); review_raw = bundle['review'].encode()
        source_name = check_source(bound, code, review_raw)
        if (procedure_provision_id(bound) != provision_id or bound['source_principal_id'] != config['source_principal_id']
                or bound['household_binding'] != config['household_binding']):
            raise ValueError('managed household version changes owner, household or provision')
        evidence = {key: bound[key] for key in ('source_sha256', 'review_sha256', 'reference_id')}
        evidence['binding_sha256'] = canonical_sha256(bound)
        if PostgresHouseholdProvisions._version_id(evidence) != path.stem:
            raise ValueError('managed household retained version identity differs')
        source_path = str(config_path.parent / source_name)
        if (payload.get(source_path) != code or entries[mapping[source_path]]['role'] != 'document_artifact'):
            raise ValueError('managed household retained version source is missing or changed')
        allowed.update((relative, source_path)); version_count += 1
    lock = str(enrollment_path.with_name(enrollment_path.name + '.lock'))
    if lock in payload and payload[lock] == b'' and entries[mapping[lock]]['role'] == 'document_journal':
        allowed.add(lock)  # New inode only; an archived lock conveys no process ownership.
    retained_versions = {name for name in entries if name.startswith(('documents/household-procedure/',
        'config/household-procedure/')) and '/versions/' in name}
    selected_versions = {mapping[name] for name in payload
        if Path(name).parent == config_path.parent / 'versions'}
    if retained_versions != selected_versions:
        raise ValueError('managed household retained version inventory is incomplete')
    if set(payload) != allowed:
        raise ValueError('managed household inventory contains unrelated files')
    return target, owner, payload, provision_id, version_count


def _verify_managed_tree(target, owner, payload):
    with _root_descriptor(target) as root:
        if _scan_regular_files(root) != set(payload):
            raise ValueError('managed household restored inventory differs')
        for name, expected in payload.items():
            info = os.stat(name, dir_fd=root, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner['uid'] or info.st_gid != owner['gid']
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                    or _read_regular(root, name, maximum=65536) != expected):
                raise ValueError('managed household restored bytes or runtime ownership differ')
        for directory in [target, *[p for p in target.rglob('*') if p.is_dir()]]:
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner['uid'] or info.st_gid != owner['gid']
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise ValueError('managed household restored directory is not private to runtime')


def _managed_household(package_root, package, configuration, *, materialize, verify_guard=None):
    """Existing host preparation's optional operator-selected managed-state output.

    Retained bytes never activate a provision or synthesize a PG row or review.
    The caller reauthorizes the current restored owner/controller before release.
    """
    target, owner, payload, provision_id, versions = _managed_payload(package_root, package, configuration)
    parent = target.parent
    info = parent.lstat()
    if (parent.resolve() != parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != owner['uid'] or info.st_gid != owner['gid'] or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('managed household target parent must be private to the trusted runtime owner')
    if materialize:
        if target.exists() or target.is_symlink():
            raise FileExistsError('managed household target already exists')
        verify_guard()
        staging = parent / ('.managed-household-' + uuid4().hex)
        staging.mkdir(mode=0o700)
        published = False
        try:
            for name, raw in payload.items():
                _write_new(staging / name, raw, 0o600)
            for item in [*staging.rglob('*'), staging]:
                os.chown(item, owner['uid'], owner['gid'], follow_symlinks=False)
                if item.is_dir(): item.chmod(0o700)
                descriptor = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
            _verify_managed_tree(staging, owner, payload)
            verify_guard()
            # Recheck selected source bytes after the blocking guard, before publication.
            if _managed_payload(package_root, package, configuration)[2] != payload:
                raise ValueError('managed household source changed during preparation')
            _verify_managed_tree(staging, owner, payload)
            after = parent.lstat()
            if ((after.st_dev, after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode))
                    != (info.st_dev, info.st_ino, owner['uid'], owner['gid'], 0o700)):
                raise ValueError('managed household destination parent changed during preparation')
            with _root_descriptor(parent) as descriptor:
                _rename_absent(descriptor, staging.name, target.name)
                published = True
                os.fsync(descriptor)
        finally:
            if not published: shutil.rmtree(staging)
    _verify_managed_tree(target, owner, payload)
    return {'status': 'materialized_inactive', 'provision_id': provision_id,
            'runtime_owner': dict(owner), 'files': len(payload), 'retained_versions': versions,
            'runtimeActivated': False, 'adoptionOrPauseChanged': False}
