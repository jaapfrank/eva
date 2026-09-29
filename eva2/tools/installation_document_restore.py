"""Prepare existing native document relocation authority without activation.

Trusted launcher configuration chooses only pinned images and inactive package
paths. The native prepareRelocation implementation remains the journal parser;
this wrapper neither translates journals nor starts a provider. Partial new
output is preserved on failure for inspection, never retried or overwritten.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sqlite3
import tempfile
from contextlib import closing
import uuid

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_evaluation import _capture
from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular

_FIELDS = {"original_binding", "catalog_path", "document_directory", "journal_directory",
           "logical_catalog_sha256", "project_keys", "runtime_uid", "runtime_gid", "image", "output_directory"}
_HEX = re.compile(r"[a-f0-9]{64}")


class DocumentRelocationPreparationError(RuntimeError):
    def __init__(self, receipt):
        super().__init__("document relocation preparation stopped; preserve partial inactive outputs for inspection")
        self.receipt = receipt


def _relative(value, prefix):
    if (not isinstance(value, str) or len(value) > 1024 or not value.startswith(prefix + "/")
            or PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(c in value for c in ("\x00", ",", "\\"))):
        raise ValueError("document relocation package path is invalid")


def validate_document_configuration(configuration):
    if (not isinstance(configuration, dict) or set(configuration) != {"providers"}
            or not isinstance(configuration["providers"], list)
            or not 1 <= len(configuration["providers"]) <= 16):
        raise ValueError("document relocation configuration differs")
    outputs, source_pairs, journals = set(), set(), set()
    for config in configuration["providers"]:
        if not isinstance(config, dict) or set(config) not in (_FIELDS, _FIELDS | {"catalog_gateway_id"}):
            raise ValueError("document relocation provider fields differ")
        _relative(config["original_binding"], "config")
        if "catalog_gateway_id" in config:
            if (config["catalog_path"] != "gateway/contextforge.sqlite"
                    or not isinstance(config["catalog_gateway_id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,35}", config["catalog_gateway_id"])):
                raise ValueError("document preserved gateway catalog selection differs")
        else:
            _relative(config["catalog_path"], "config")
        for key in ("document_directory", "journal_directory"):
            _relative(config[key], "documents")
        a, b = config["document_directory"], config["journal_directory"]
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise ValueError("document and journal directory scopes overlap")
        if any(b == prior or b.startswith(prior + "/") or prior.startswith(b + "/") for prior in journals):
            raise ValueError("document journal directories overlap")
        journals.add(b)
        if (not isinstance(config["logical_catalog_sha256"], str)
                or not _HEX.fullmatch(config["logical_catalog_sha256"])
                or not isinstance(config["image"], str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", config["image"])
                or not isinstance(config["project_keys"], list) or not 0 <= len(config["project_keys"]) <= 16
                or any(not isinstance(key, str) or not _HEX.fullmatch(key) for key in config["project_keys"])
                or len(set(config["project_keys"])) != len(config["project_keys"])):
            raise ValueError("document relocation image/catalog/project pin differs")
        for key in ("runtime_uid", "runtime_gid"):
            if type(config[key]) is not int or not 0 <= config[key] < 4294967295:
                raise ValueError("document relocation runtime owner is invalid")
        value = config["output_directory"]
        if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).resolve() != Path(value):
            raise ValueError("document relocation output must be absolute without links")
        if value in outputs or (a, b) in source_pairs:
            raise ValueError("document relocation configuration duplicates a target")
        outputs.add(value)
        source_pairs.add((a, b))


_PREPARE = """
import fs from 'node:fs';
import {prepareRelocation} from '/opt/eva2/atomic/relocation.mjs';
const flags=fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW;
const rootFd=fs.openSync('/workspace',flags),journalFd=fs.openSync('/journal',flags);
try {
 const result=prepareRelocation({executable:'/opt/eva2/atomic/workspace-writer-broker',rootFd,journalFd,
  originalBindingBytes:fs.readFileSync('/run/original-binding.json'),
  logicalCatalogIdentity:process.argv[1],projectKeys:JSON.parse(process.argv[2])});
 console.log(JSON.stringify({binding:result.bindingBytes.toString('base64'),
  snapshots:Object.fromEntries(Object.entries(result.snapshots).map(([k,v])=>[k,v.toString('base64')]))}));
} finally {fs.closeSync(rootFd);fs.closeSync(journalFd);}
"""


def _empty_journal(root, relative, config):
    """Restore an explicitly empty journal directory, never invent journal files."""
    with _root_descriptor(root) as root_fd:
        fd = os.dup(root_fd)
        try:
            parts = relative.split("/")
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.fchown(child, config["runtime_uid"], config["runtime_gid"])
                os.close(fd)
                fd = child
            if os.listdir(fd):
                raise ValueError("empty journal declaration would omit existing entries")
        finally:
            os.close(fd)


def _preserved_gateway_catalog(raw: bytes, gateway_id: str) -> list[dict]:
    """Read native tool definitions from authenticated ContextForge snapshot bytes."""
    with tempfile.TemporaryDirectory(prefix="eva-document-catalog-") as temporary:
        snapshot = Path(temporary) / "catalog.sqlite"
        snapshot.write_bytes(raw)
        snapshot.chmod(0o400)
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
            database.execute("PRAGMA query_only=ON")
            database.execute("PRAGMA trusted_schema=OFF")
            database.set_progress_handler(lambda: 1, 1_000_000)
            if database.execute("SELECT id FROM gateways WHERE id=?", (gateway_id,)).fetchall() != [(gateway_id,)]:
                raise ValueError("document catalog gateway is missing or ambiguous")
            rows = database.execute(
                "SELECT original_name,original_description,description,input_schema,output_schema,annotations "
                "FROM tools WHERE gateway_id=? ORDER BY original_name", (gateway_id,)).fetchall()
    if not rows or len(rows) > 256:
        raise ValueError("preserved document catalog inventory differs")
    tools, names = [], set()
    for name, original_description, description, input_schema, output_schema, annotations in rows:
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("preserved document tool name is missing or duplicated")
        names.add(name)
        tool = {"name": name, "inputSchema": json.loads(input_schema)}
        description = original_description if original_description is not None else description
        if description is not None:
            tool["description"] = description
        # ContextForge persists absent MCP optional fields as null/empty objects.
        for key, value in (("outputSchema", output_schema), ("annotations", annotations)):
            decoded = json.loads(value) if value is not None else None
            if decoded is not None and not (key == "annotations" and decoded == {}):
                tool[key] = decoded
        tools.append(tool)
    return tools


def _validate_sources(root, package, config):
    entries = package["manifest"]["files"]
    catalog_role = "gateway_database" if "catalog_gateway_id" in config else "provider_catalog"
    for key, role in (("original_binding", "provider_binding"), ("catalog_path", catalog_role)):
        if entries.get(config[key], {}).get("role") != role:
            raise ValueError("document relocation authority file role differs")
    with _root_descriptor(root) as fd:
        original = _read_regular(fd, config["original_binding"])
        catalog = _read_regular(fd, config["catalog_path"])
    for name, raw in ((config["original_binding"], original), (config["catalog_path"], catalog)):
        if len(raw) != entries[name]["size"] or hashlib.sha256(raw).hexdigest() != entries[name]["sha256"]:
            raise ValueError("document authority source bytes changed")
    catalog_value = (_preserved_gateway_catalog(catalog, config["catalog_gateway_id"])
                     if "catalog_gateway_id" in config else json.loads(catalog))
    if canonical_sha256(catalog_value) != config["logical_catalog_sha256"]:
        raise ValueError("document relocation logical catalog pin differs")
    binding = json.loads(original)
    flat = binding.get("version") == 1
    if not flat and not (binding.get("version") == 2 and binding.get("journal_mode") == "per_path_prefix_v1"):
        raise ValueError("document source journal layout is unsupported")
    if flat and config["project_keys"]:
        raise ValueError("flat journals cannot declare project directories")
    for key, roles in (("document_directory", {"document_artifact"}),
                       ("journal_directory", {"document_journal", "document_marker"})):
        prefix = config[key] + "/"
        names = [name for name in entries if name.startswith(prefix)]
        if any(entries[name]["role"] not in roles for name in names):
            raise ValueError("document relocation directory roles differ")
        if not names:
            if key != "journal_directory" or config["project_keys"]:
                raise ValueError("document relocation directory inventory is empty")
            _empty_journal(root, config[key], config)
        directory = root / config[key]
        info = directory.stat()
        if (directory.resolve() != directory or info.st_uid != config["runtime_uid"]
                or info.st_gid != config["runtime_gid"] or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError("document relocation source directory owner differs")
    journal = config["journal_directory"] + "/"
    names = {name[len(journal):] for name in entries if name.startswith(journal)}
    if flat:
        if names not in (set(), {"workspace-writer.journal"}):
            raise ValueError("flat journal inventory differs")
    elif {name.split("/")[0] for name in names} != set(config["project_keys"]):
        raise ValueError("document relocation project coverage differs")
    return original


def prepare_document_relocations(package_root: Path, *, configuration: dict, guard: callable) -> dict:
    receipt = {"contract": "EvaDocumentRelocationPreparation/v1", "providers": [],
               "connected": False, "provider_verified": False, "services_started": False}
    try:
        validate_document_configuration(configuration)
        if not callable(guard):
            raise ValueError("document relocation requires a current restore guard")
        root = package_root.resolve()
        package = read_installation_package(root)
        receipt["archive_sha256"] = package["archive_sha256"]
        for config in configuration["providers"]:
            original = _validate_sources(root, package, config)
            output = Path(config["output_directory"])
            if output == root or root in output.parents or output in root.parents:
                raise ValueError("relocation output overlaps the installation package")
            parent = output.parent.stat()
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700 or output.exists():
                raise ValueError("relocation output parent is not private or target already exists")
            guard()
            current = read_installation_package(root)
            if current["archive_sha256"] != package["archive_sha256"]:
                raise ValueError("document source package changed")
            name = "eva-document-relocation-" + uuid.uuid4().hex
            command = ["docker", "run", "--rm", "--name", name, "--pull", "never", "--network", "none",
                "--read-only", "--no-healthcheck", "--log-driver", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", "256m", "--memory-swap", "256m",
                "--cpus", "1", "--pids-limit", "32", "--user", f"{config['runtime_uid']}:{config['runtime_gid']}",
                "--mount", f"type=bind,src={root / config['document_directory']},dst=/workspace,readonly",
                "--mount", f"type=bind,src={root / config['journal_directory']},dst=/journal,readonly",
                "--mount", f"type=bind,src={root / config['original_binding']},dst=/run/original-binding.json,readonly",
                "--entrypoint", "node", config["image"], "--input-type=module", "-e", _PREPARE,
                config["logical_catalog_sha256"], json.dumps(config["project_keys"])]
            try:
                result = _capture(command, deadline=20 + 10 * len(config["project_keys"]), limit=96 * 1024 * 1024)
                if result["exit_code"] != 0 or result["timed_out"] or result["stdout_truncated"]:
                    raise RuntimeError("native document relocation preparation failed")
            finally:
                cleanup = _capture(["docker", "rm", "--force", name], deadline=10, limit=2048)
                if cleanup["timed_out"] or (cleanup["exit_code"] != 0 and "No such container" not in cleanup["stderr"]):
                    raise RuntimeError("native relocation preparation cleanup uncertain")
            wire = json.loads(result["stdout"])
            flat = json.loads(original).get("version") == 1
            expected_snapshots = ({"flat"} if (root / config["journal_directory"] / "workspace-writer.journal").exists() else set()) if flat else set(config["project_keys"])
            if set(wire) != {"binding", "snapshots"} or set(wire["snapshots"]) != expected_snapshots:
                raise ValueError("native relocation output inventory differs")
            files = {"binding.json": base64.b64decode(wire["binding"], validate=True)}
            files.update({key + ".relocation": base64.b64decode(value, validate=True)
                          for key, value in wire["snapshots"].items()})
            binding = json.loads(files["binding.json"])
            expected_kind = "host_authorized_flat_relocation_v1" if flat else "host_authorized_relocation_v1"
            if (binding.get("version") != (4 if flat else 3) or binding.get("kind") != expected_kind
                    or binding.get("logical_provider_identity") != hashlib.sha256(original).hexdigest()
                    or binding.get("logical_catalog_identity") != config["logical_catalog_sha256"]):
                raise ValueError("native relocation authority binding differs")
            if (base64.b64decode(binding.get("original_binding_base64", ""), validate=True) != original
                    or set(binding.get("projects", {})) != set(config["project_keys"])):
                raise ValueError("native relocation original authority or projects differ")
            for key in expected_snapshots:
                snapshot = binding["flat"] if flat else binding["projects"][key]
                if hashlib.sha256(files[key + ".relocation"]).hexdigest() != snapshot.get("snapshot_sha256"):
                    raise ValueError("native relocation snapshot bytes differ")
            guard()
            if read_installation_package(root)["archive_sha256"] != package["archive_sha256"]:
                raise ValueError("document source package changed during preparation")
            item = {"status": "preparation_partial", "journal_layout": "flat" if flat else "per_project", "image_id": config["image"],
                    "output_binding_sha256": hashlib.sha256(str(output).encode()).hexdigest(), "files": {}}
            receipt["providers"].append(item)
            with _root_descriptor(output.parent) as parent_fd:
                guard()
                os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
                out_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    for filename, raw in files.items():
                        guard()
                        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=out_fd)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(raw); stream.flush()
                            os.fchown(stream.fileno(), config["runtime_uid"], config["runtime_gid"])
                            os.fchmod(stream.fileno(), 0o400); os.fsync(stream.fileno())
                        item["files"][filename] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
                    os.fchown(out_fd, config["runtime_uid"], config["runtime_gid"])
                    os.fsync(out_fd); os.fsync(parent_fd)
                finally:
                    os.close(out_fd)
            guard()
            item["status"] = "prepared_not_connected"
        receipt["status"] = "prepared_not_connected"
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise DocumentRelocationPreparationError(receipt) from error
