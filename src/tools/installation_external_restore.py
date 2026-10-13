"""Import external recovery components into explicitly isolated empty targets.

Configuration is trusted launcher input, never package content. Checks are
point-in-time observations, not a lock against other operators or unknown
non-Docker clients. A failure preserves target data and requires inspection;
this module never retries, starts services, releases guards or overwrites files.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile

from tools.installation_recovery_package import read_installation_package
from tools.read_eva_portable_archive import _root_descriptor, _read_regular
from tools.portable_transport import MAX_ARCHIVE_BYTES


class ExternalRestoreError(RuntimeError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def _pin(value):
    if (not isinstance(value, dict) or set(value) != {"name", "id", "image"}
            or not isinstance(value["name"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value["name"])
            or not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["id"])
            or not isinstance(value["image"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value["image"])):
        raise ValueError("external restore container pin is invalid")


def _configuration(config):
    if not isinstance(config, dict) or set(config) != {"memory", "gateway", "writers"}:
        raise ValueError("external restore configuration differs")
    memory, gateway, writers = config["memory"], config["gateway"], config["writers"]
    if not isinstance(memory, dict) or set(memory) != {"container", "database", "user"}:
        raise ValueError("memory restore target is invalid")
    _pin(memory["container"])
    db = memory["database"]
    if (not isinstance(db, dict) or set(db) != {"name", "oid", "system_identifier"}
            or type(db["oid"]) is not int or db["oid"] <= 0
            or not isinstance(db["system_identifier"], str)
            or not re.fullmatch(r"[1-9][0-9]*", db["system_identifier"])):
        raise ValueError("memory database identity is invalid")
    for value in (db["name"], memory["user"]):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
            raise ValueError("memory database or role name is invalid")
    if (not isinstance(gateway, dict) or set(gateway) != {"target_path"}
            or not isinstance(gateway["target_path"], str)):
        raise ValueError("gateway restore target is invalid")
    path = Path(gateway["target_path"])
    if not path.is_absolute() or path.resolve() != path or not path.name.endswith(".sqlite"):
        raise ValueError("gateway target must be an absolute regular SQLite path without links")
    if not isinstance(writers, list) or not 1 <= len(writers) <= 64:
        raise ValueError("external writer pins are required")
    for writer in writers:
        _pin(writer)
    pins = [memory["container"], *writers]
    if len({p["id"] for p in pins}) != len(pins) or len({p["name"] for p in pins}) != len(pins):
        raise ValueError("external target pins overlap")
    return path


def validate_external_configuration(configuration):
    """Validate trusted launcher shape without inspecting or mutating targets."""
    _configuration(configuration)


def _run(argv, *, raw=None, timeout=30):
    # Output remains in temporary files; only a bounded stdout excerpt is read.
    # Raw command output is never included in exceptions.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        done = subprocess.run(argv, input=raw, stdout=out, stderr=err, timeout=timeout, check=False)
        if done.returncode != 0:
            raise RuntimeError("external restore command failed")
        out.seek(0)
        data = out.read(65537)
        if len(data) > 65536:
            raise RuntimeError("external restore command output exceeds bound")
        return data


def _pins_current(config):
    for pin in [config["memory"]["container"], *config["writers"]]:
        rows = json.loads(_run(["docker", "inspect", pin["id"]]))
        if len(rows) != 1:
            raise ValueError("external restore container is unavailable")
        row = rows[0]
        if (row.get("Id") != pin["id"] or row.get("Name") != "/" + pin["name"]
                or row.get("Image") != pin["image"]):
            raise ValueError("external restore container identity changed")
        state = row.get("State", {})
        if pin == config["memory"]["container"]:
            if state.get("Running") is not True or state.get("Paused") or state.get("Restarting"):
                raise ValueError("memory database target is not running normally")
        elif (state.get("Running") is not False or state.get("Paused") is not False
                or state.get("Restarting") is not False or state.get("Pid") != 0
                or state.get("Status") not in {"created", "exited"}
                or row.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") != "no"):
            raise ValueError("external restore writer is not stopped with restart disabled")


_DATABASE_QUERY = """
SELECT json_build_object('name',current_database(),
 'oid',(SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
 'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
 'nonempty',EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%')
 OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public')
 OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public')
 OR EXISTS(SELECT 1 FROM pg_extension WHERE extname<>'plpgsql')
 OR EXISTS(SELECT 1 FROM pg_largeobject_metadata),
 'other_sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()));
"""


def _database_current(config, *, empty):
    memory = config["memory"]
    raw = _run(["docker", "exec", memory["container"]["id"], "psql", "--no-psqlrc",
                "--no-password", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
                "--username", memory["user"], "--dbname", memory["database"]["name"],
                "--command", _DATABASE_QUERY])
    actual = json.loads(raw)
    if {key: actual[key] for key in ("name", "oid", "system_identifier")} != memory["database"]:
        raise ValueError("memory target database lineage differs")
    if actual["other_sessions"] != 0 or (empty and actual["nonempty"] is not False):
        raise ValueError("memory target is not empty and isolated")


def restore_external_components(package_root: Path, *, configuration: dict, guard: callable) -> dict:
    receipt = {"contract": "EvaExternalComponentRestore/v1", "memory": {"status": "not_started"},
               "gateway": {"status": "not_started"}, "services_started": False, "released": False}
    try:
        target = _configuration(configuration)
        if not callable(guard):
            raise ValueError("external restore requires current core guard")
        validated = read_installation_package(package_root)
        receipt["archive_sha256"] = validated["archive_sha256"]
        manifest = validated["manifest"]
        with _root_descriptor(package_root) as fd:
            content = {name: _read_regular(fd, name, maximum=MAX_ARCHIVE_BYTES)
                       for name in ("memory/hindsight.dump", "gateway/contextforge.sqlite")}
        for name, raw in content.items():
            entry = manifest["files"][name]
            if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("external restore source bytes changed")
        with tempfile.TemporaryDirectory(prefix="eva-external-restore-") as directory:
            snapshot = Path(directory) / "source.sqlite"
            snapshot.write_bytes(content["gateway/contextforge.sqlite"])
            snapshot.chmod(0o400)
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
                if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("gateway source SQLite integrity check failed")
        parent_info = target.parent.stat()
        if parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) != 0o700:
            raise ValueError("gateway target parent must be a private operator directory")
        with _root_descriptor(target.parent) as target_fd:
            def check_target():
                current = target.parent.stat()
                opened = os.fstat(target_fd)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("gateway target parent changed")
                for name in (target.name, target.name + "-wal", target.name + "-shm", target.name + "-journal"):
                    try:
                        os.stat(name, dir_fd=target_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise ValueError("gateway target or sidecar already exists")
            check_target()
            memory = configuration["memory"]
            if read_installation_package(package_root)["archive_sha256"] != receipt["archive_sha256"]:
                raise ValueError("external restore package changed before import")
            guard()
            _pins_current(configuration)
            _database_current(configuration, empty=True)
            receipt["memory"] = {"status": "outcome_unknown",
                "target_binding_sha256": hashlib.sha256(json.dumps(memory, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "source_sha256": manifest["files"]["memory/hindsight.dump"]["sha256"]}
            _run(["docker", "exec", "--interactive", memory["container"]["id"], "pg_restore",
                "--no-password", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                "--username", memory["user"], "--dbname", memory["database"]["name"]],
                raw=content["memory/hindsight.dump"], timeout=900)
            receipt["memory"]["status"] = "imported"
            guard()
            _pins_current(configuration)
            _database_current(configuration, empty=False)
            check_target()
            guard()
            _pins_current(configuration)
            receipt["gateway"] = {"status": "outcome_unknown",
                "target_binding_sha256": hashlib.sha256(str(target).encode()).hexdigest(),
                "source_sha256": manifest["files"]["gateway/contextforge.sqlite"]["sha256"]}
            out = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_fd)
            with os.fdopen(out, "wb") as stream:
                stream.write(content["gateway/contextforge.sqlite"])
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(target_fd)
            restored = _read_regular(target_fd, target.name, maximum=MAX_ARCHIVE_BYTES)
            if restored != content["gateway/contextforge.sqlite"]:
                raise ValueError("gateway restored bytes differ")
            receipt["gateway"]["status"] = "imported"
            guard()
            _pins_current(configuration)
            _database_current(configuration, empty=False)
        receipt["status"] = "imported_not_activated"
        return receipt
    except Exception as error:
        receipt["status"] = "inspection_required_no_retry"
        raise ExternalRestoreError("external restore stopped; partial external imports may already exist; inspect preserved inactive targets before any further action", receipt) from error
