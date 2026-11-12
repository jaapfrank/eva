"""Host-only original import closure for a candidate-blind scheduled-failure oracle."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile

from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.reviewed_component_application import _artifact
from eva.operations.runtime_repair_development import _put
from eva.operations.runtime_repair_diagnostics import (
    _CALLER, _LOADER, _READ_ONLY_SOURCES, _WORKSPACE, _bound_installation, _current_binding,
    _generation, _manifest_identity, _target_binding, _validate_facts,
)
from eva.operations.runtime_repair_target import _sha


_CONTRACT = "EvaScheduledFailureBaselineImportClosure/v1"
_CONTEXT_FILES = (_CALLER, "adapters/postgres/scheduled_work.py", "adapters/postgres/private_work_return.py")
# This is a separate read-only transport budget, including the duplicate original
# module used for import inspection. It does not expand the editable target budget.
_MAX_SNAPSHOT_BYTES = 512 * 1024
_MAX_SOURCE_FILES = 64
_MAX_REPLY_BYTES = 4 * ((_MAX_SNAPSHOT_BYTES + 2) // 3) + 32768
_MODULE_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_/]*\.py")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
_WORKSPACE_IMPORT_ROOT = "/workspace/" + _WORKSPACE
_IMPORT_DIAGNOSTIC = r'''
import base64
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import stat
import sys

mode = sys.argv[1]
if mode not in ("collect", "verify"):
    raise ValueError("unknown original import diagnostic")
root = Path("/opt/eva/src" if mode == "collect" else "/workspace/repair/diagnostics")
source_root = root / "eva"
maximum, max_files = int(sys.argv[2]), int(sys.argv[3])
sys.path.insert(0, str(root))
before = set(sys.modules)
importlib.import_module("eva.adapters.postgres.scheduled_work_failure")
loaded = dict(sys.modules)
sources, packages = {}, set()

def read(path):
    if (not path.is_relative_to(source_root) or path.resolve(strict=True) != path
            or not stat.S_ISREG(path.stat().st_mode) or not 1 <= path.stat().st_size <= maximum):
        raise ValueError("bounded immutable original source required")
    return path.read_bytes()

for name, module in sorted(loaded.items()):
    if module is None:
        continue
    filename = getattr(module, "__file__", None)
    if name == "eva" or name.startswith("eva."):
        path = Path(filename)
        raw = read(path)
        sources[path.relative_to(source_root).as_posix()] = raw
    elif name not in before and filename and ("site-packages" in filename or "dist-packages" in filename):
        packages.add(name.split(".")[0])
import_files = sorted(sources)
if mode == "collect":
    for relative in ("bootstrap/self_repair.py", "adapters/postgres/scheduled_work.py",
                     "adapters/postgres/private_work_return.py"):
        sources[relative] = read(source_root / relative)
if not 1 <= len(sources) <= max_files or sum(map(len, sources.values())) > maximum:
    raise ValueError("original import snapshot exceeds its separate source bound")
distributions = importlib.metadata.packages_distributions()
versions = {}
for package in sorted(packages):
    names = distributions.get(package)
    if not names:
        raise ValueError("installed original import distribution unavailable")
    for name in names:
        versions[name] = importlib.metadata.version(name)
result = {"python": sys.version.split()[0], "distributions": versions, "import_files": import_files,
          "sources": {relative: {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
                      for relative, raw in sorted(sources.items())}}
if mode == "collect":
    result["source_bytes"] = {relative: base64.b64encode(raw).decode("ascii")
                              for relative, raw in sorted(sources.items())}
print(json.dumps(result, sort_keys=True))
'''


def validate_import_blocked_diagnostic(target, blocked, previous_diagnostics_sha256):
    """Recognize the measured second refusal without following its freeform text."""
    _, observed = _target_binding(target)
    flags = {"oracle_created", "candidate_reviewed_or_submitted", "installation_modified"}
    prose = {"missing_required_control", "reproduction_probe", "probe_limits", "assessment", "resume_requirement"}
    fields = flags | prose | {"status", "target_id", "role", "input_binding", "verified_sources",
                              "new_evidence", "observed_import_failures"}
    failures = [
        {"module": "eva.adapters.postgres.scheduled_work_failure", "dependency": "eva.contracts.timestamps",
         "error": "ModuleNotFoundError", "via": "eva.adapters.postgres.operational_timeline"},
        {"module": "eva.contracts.canonical_json", "dependency": "eva.contracts.base", "error": "ModuleNotFoundError"},
    ]
    if (not isinstance(blocked, dict) or set(blocked) != fields
            or blocked["status"] != "blocked" or blocked["role"] != "original_behavior_oracle"
            or blocked["target_id"] != _sha(target["target_id"])
            or blocked["input_binding"] != _sha(previous_diagnostics_sha256)
            or any(blocked[key] is not False for key in flags)
            or blocked["observed_import_failures"] != failures
            or any(not isinstance(blocked[key], str) or not 1 <= len(blocked[key]) <= 4000 for key in prose)
            or not isinstance(blocked["new_evidence"], list) or not 1 <= len(blocked["new_evidence"]) <= 24
            or any(not isinstance(item, str) or not 1 <= len(item) <= 4000 for item in blocked["new_evidence"])):
        raise ValueError("exact original import blocker evidence required")
    sources = blocked["verified_sources"]
    if (not isinstance(sources, dict) or set(sources) != set(observed) | {"diagnostic_manifest_sources"}
            or any(sources[path] != digest for path, digest in observed.items())
            or not isinstance(sources["diagnostic_manifest_sources"], str)
            or not 1 <= len(sources["diagnostic_manifest_sources"]) <= 4000):
        raise ValueError("original import blocker source binding differs")


def _trusted_image(image):
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        raise ValueError("exact immutable import image required")
    return image


def _run_import(publisher, image, mode, *, workspace=None):
    argv = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001",
            "--pids-limit", "64", "--memory", "256m"]
    if workspace is not None:
        argv.extend(["--workdir", "/workspace", "--mount", f"type=bind,src={workspace},dst=/workspace,readonly"])
    argv.extend(["--entrypoint", "python", _trusted_image(image), "-I", "-B", "-c", _IMPORT_DIAGNOSTIC,
                 mode, str(_MAX_SNAPSHOT_BYTES), str(_MAX_SOURCE_FILES)])
    result = publisher.run(argv, text=True, capture_output=True, timeout=45)
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > _MAX_REPLY_BYTES:
        raise ValueError("isolated original import diagnostic failed")
    value = json.loads(result.stdout)
    expected = {"python", "distributions", "import_files", "sources"}
    if not isinstance(value, dict) or set(value) != (expected | {"source_bytes"} if mode == "collect" else expected):
        raise ValueError("original import diagnostic response differs")
    return value


def _validate_import_report(report):
    if (not isinstance(report, dict) or set(report) != {"python", "distributions", "import_files", "sources"}
            or not isinstance(report["python"], str) or not re.fullmatch(r"3\.[0-9]+\.[0-9]+", report["python"])
            or not isinstance(report["distributions"], dict) or not 1 <= len(report["distributions"]) <= 32
            or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key)
                   or not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.+!-]{1,80}", value)
                   for key, value in report["distributions"].items())
            or not isinstance(report["import_files"], list) or not 1 <= len(report["import_files"]) <= _MAX_SOURCE_FILES
            or any(not isinstance(name, str) or not _MODULE_PATH.fullmatch(name)
                   for name in report["import_files"])
            or report["import_files"] != sorted(set(report["import_files"]))
            or _LOADER not in report["import_files"] or "__init__.py" not in report["import_files"]
            or not isinstance(report["sources"], dict)):
        raise ValueError("closed original import inventory required")


def _source_bytes(encoded, manifest):
    if (not isinstance(encoded, dict) or set(encoded) != set(manifest) or len(encoded) > _MAX_SOURCE_FILES):
        raise ValueError("original import source inventory differs")
    raw = {}
    for path, value in encoded.items():
        if (not isinstance(path, str) or not _MODULE_PATH.fullmatch(path) or "//" in path
                or not isinstance(value, str) or len(value) > _MAX_REPLY_BYTES):
            raise ValueError("bounded original import source required")
        source = base64.b64decode(value, validate=True)
        if (not 1 <= len(source) <= _MAX_SNAPSHOT_BYTES
                or manifest[path] != {"sha256": hashlib.sha256(source).hexdigest(), "size": len(source)}):
            raise ValueError("original import source receipt differs")
        source.decode("utf-8")
        raw[path] = source
    if sum(map(len, raw.values())) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("original import snapshot exceeds its separate source bound")
    return raw


def _previous_context(target, binding, receipt):
    _, previous = _artifact(receipt)
    if set(previous) != {"manifest", "facts", "source_bytes"}:
        raise ValueError("previous original diagnostics receipt differs")
    manifest, facts = previous["manifest"], previous["facts"]
    _validate_facts(facts)
    identity = _manifest_identity(target, binding, manifest["blocked_sha256"], manifest["parent_task_sha256"])
    if (set(manifest) != set(identity) | {"facts_sha256", "read_only_sources"}
            or any(manifest[key] != value for key, value in identity.items())
            or manifest["facts_sha256"] != canonical_sha256(facts)
            or set(manifest["read_only_sources"]) != set(_READ_ONLY_SOURCES)):
        raise ValueError("previous original diagnostics binding differs")
    _source_bytes(previous["source_bytes"], manifest["read_only_sources"])
    return facts, manifest["read_only_sources"]


def _identity(target, binding, previous_sha256, failed_work_sha256, blocked_sha256):
    return {"contract": _CONTRACT, "target_id": _sha(target["target_id"]), "role": "original_behavior_oracle",
            "baseline": target["installation"]["core"], "scope_sha256": binding["scope_sha256"],
            "incident_event_sha256": binding["incident_event_sha256"], "source_sha256": binding["source_sha256"],
            "mandate_sha256": binding["mandate_sha256"], "previous_diagnostics_sha256": _sha(previous_sha256),
            "failed_work_sha256": _sha(failed_work_sha256), "blocked_sha256": _sha(blocked_sha256)}


def _verify_closure(baseline, programming, observed):
    _validate_import_report(baseline)
    _validate_import_report(programming)
    imported = baseline["import_files"]
    sources = baseline["sources"]
    if (set(sources) != set(imported) | set(_CONTEXT_FILES)
            or programming["import_files"] != imported
            or programming["sources"] != {name: sources[name] for name in imported}
            or any(sources[name]["sha256"] != digest for name, digest in observed.items())):
        raise ValueError("programming import did not read the exact original closure")


def _verify_programming_import(publisher, image, files):
    # Temporary source-only proof: the existing sandbox user cannot write this
    # mount and sees no model workspace, credentials, candidate, or oracle.
    with tempfile.TemporaryDirectory(prefix="eva-baseline-import-") as private:
        workspace = Path(private) / "workspace"
        workspace.mkdir(mode=0o755)
        root = workspace / _WORKSPACE / "eva"
        for relative, raw in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o444)
        return _run_import(publisher, image, "verify", workspace=workspace)


def prepare_scheduled_failure_import_closure(publisher, runtime, target, directory, *,
                                             programming_image, previous_diagnostics,
                                             failed_work_sha256, blocked_receipt):
    """Seal exact original code and an import-only proof outside the editable scope."""
    binding, observed = _target_binding(target)
    _, blocked = _artifact(blocked_receipt)
    validate_import_blocked_diagnostic(target, blocked, previous_diagnostics["sha256"])
    facts, previous_sources = _previous_context(target, binding, previous_diagnostics)
    identity = _identity(target, binding, previous_diagnostics["sha256"], failed_work_sha256, blocked_receipt["sha256"])
    image = identity["baseline"]["image"]
    directory = Path(directory)
    _trusted_image(programming_image)
    _bound_installation(publisher, directory, target)
    generation = _generation(publisher, runtime, directory, image)
    _current_binding(runtime, binding)
    baseline = _run_import(publisher, image, "collect")
    encoded = baseline.pop("source_bytes")
    files = _source_bytes(encoded, baseline["sources"])
    if any(baseline["sources"].get(path) != proof for path, proof in previous_sources.items()):
        raise ValueError("original diagnostic context source changed")
    programming = _verify_programming_import(publisher, programming_image, files)
    _verify_closure(baseline, programming, observed)
    _current_binding(runtime, binding)
    _bound_installation(publisher, directory, target)
    if _generation(publisher, runtime, directory, image) != generation:
        raise ValueError("original import diagnostic core generation changed")
    manifest = {**identity, "facts_sha256": canonical_sha256(facts), "programming_image": programming_image,
                "baseline_import": baseline, "programming_import": programming, "import_only": True}
    return _put(directory / "baseline-import-closure.private.json", {
        "manifest": manifest, "facts": facts, "source_bytes": encoded,
    })


def seed_scheduled_failure_import_closure(artifacts, metadata, target, receipt, *,
                                          previous_diagnostics_sha256, failed_work_sha256, blocked_sha256):
    """Seed a candidate-blind authoring snapshot, separate from comparison execution."""
    binding, observed = _target_binding(target)
    if (metadata.get("role") != "original_behavior_oracle" or metadata.get("target_id") != target["target_id"]
            or metadata.get("incident_id") != binding["incident_id"]
            or metadata.get("candidate_sha256") is not None or metadata.get("comparison_sha256") is not None):
        raise ValueError("original import closure requires exact candidate-blind work")
    _, value = _artifact(receipt)
    if set(value) != {"manifest", "facts", "source_bytes"}:
        raise ValueError("original import closure receipt differs")
    manifest, facts = value["manifest"], value["facts"]
    _validate_facts(facts)
    identity = _identity(target, binding, previous_diagnostics_sha256, failed_work_sha256, blocked_sha256)
    if (set(manifest) != set(identity) | {"facts_sha256", "programming_image", "baseline_import",
                                        "programming_import", "import_only"}
            or any(manifest[key] != item for key, item in identity.items())
            or manifest["facts_sha256"] != canonical_sha256(facts) or manifest["import_only"] is not True
            or manifest["programming_image"] != _trusted_image(artifacts.programming.config.image)):
        raise ValueError("original import closure manifest differs")
    _verify_closure(manifest["baseline_import"], manifest["programming_import"], observed)
    sources = _source_bytes(value["source_bytes"], manifest["baseline_import"]["sources"])
    files = {_WORKSPACE + "/manifest.json": canonical_json_bytes(manifest),
             _WORKSPACE + "/context.json": canonical_json_bytes(facts)}
    files.update({_WORKSPACE + "/eva/" + path: raw for path, raw in sources.items()})
    artifacts._seed(metadata, files)
    return {
        "diagnostic_manifest": _WORKSPACE_IMPORT_ROOT + "/manifest.json",
        "diagnostic_context": _WORKSPACE_IMPORT_ROOT + "/context.json",
        "diagnostic_read_only_sources": _WORKSPACE_IMPORT_ROOT + "/eva",
        "comparison_module_path": "/opt/eva/src/eva/" + _LOADER,
        "import_context_only": True,
        "driver_import_policy": (
            "Use this snapshot only to inspect the original source and its imports. In the submitted driver, "
            "import eva.adapters.postgres.scheduled_work_failure normally from the shared installed module path. "
            "The evaluator supplies the selected baseline or candidate there. Do not import or copy the "
            "workspace snapshot into the comparison driver."
        ),
    }
