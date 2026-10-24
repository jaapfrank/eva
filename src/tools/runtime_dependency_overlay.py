"""Offline, fixed-pair wheel overlay. No pip, hooks, network or imported candidate code."""
from __future__ import annotations
import base64
import csv
import hashlib
import importlib.metadata as md
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import sysconfig
import zipfile
from email.parser import BytesParser

PAIR = {"psycopg": "psycopg", "psycopg-binary": "psycopg_binary"}
VERSION = "3.3.6"
MAX_BYTES = 64 * 1024 * 1024
MAX_EXPANDED = 192 * 1024 * 1024

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def regular(path, maximum=MAX_BYTES):
    path = Path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise ValueError("one bounded regular file required")
        raw = stream.read(maximum + 1)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if len(raw) > maximum or identity(os.fstat(stream.fileno())) != identity(before) or identity(path.stat()) != identity(before):
            raise ValueError("artifact changed while reading")
    return raw

def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid wheel member")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in ("", ".", "..") for p in path.parts):
        raise ValueError("wheel path escapes distribution")
    return path

def _inside(site, relative):
    path = site.joinpath(*safe_relative(relative).parts)
    if any(p.is_symlink() for p in [path, *path.parents] if p != site.parent):
        raise ValueError("symlink in installed distribution path")
    if site not in path.parents:
        raise ValueError("distribution outside site packages")
    return path

def installed_pair(site):
    site = Path(site).resolve(strict=True)
    found = {}
    for distribution in md.distributions(path=[str(site)]):
        name = distribution.metadata["Name"].lower().replace("_", "-")
        if name not in PAIR:
            continue
        if name in found or distribution.version not in {"3.3.4", VERSION}:
            raise ValueError("unexpected or duplicate installed driver")
        files = {}
        for row in distribution.files or ():
            relative = str(row)
            path = _inside(site, relative)
            if path.is_file():
                files[relative] = digest(regular(path))
            else:
                raise ValueError("recorded distribution file missing")
        if not files or not any(n.endswith(".dist-info/RECORD") for n in files):
            raise ValueError("installed ownership manifest missing")
        found[name] = {"version": distribution.version, "files": files}
    if set(found) != set(PAIR):
        raise ValueError("both actual driver distributions required")
    if set(found["psycopg"]["files"]) & set(found["psycopg-binary"]["files"]):
        raise ValueError("shared driver file ownership")
    return found

def _tree(site, excluded):
    rows = {}
    for path in sorted(site.rglob("*")):
        relative = path.relative_to(site).as_posix()
        if path.is_symlink():
            raise ValueError("site packages contains unaccounted symlink")
        if path.is_file() and relative not in excluded:
            rows[relative] = digest(regular(path))
    return digest(canonical(rows))

def validate_wheel_delta(wheels, *, python_tag="cp312", platform_tags=()):
    """Validate exact retained bytes before touching any installed file."""
    if not isinstance(wheels, list) or len(wheels) != 2:
        raise ValueError("one exact driver pair required")
    result = {}
    for row in wheels:
        if set(row) != {"name", "version", "filename", "path", "sha256", "size"} or row["name"] not in PAIR or row["name"] in result or row["version"] != VERSION:
            raise ValueError("wheel identity differs")
        path = Path(row["path"])
        if path.name != row["filename"] or not path.name.endswith(".whl"):
            raise ValueError("wheel filename differs")
        raw = regular(path)
        if len(raw) != row["size"] or digest(raw) != row["sha256"]:
            raise ValueError("wheel bytes differ from official pin")
        prefix = PAIR[row["name"]]
        dist = prefix + "-" + VERSION + ".dist-info"
        files = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 4096 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise ValueError("wheel expanded size exceeded")
            for info in infos:
                if info.is_dir():
                    continue
                name = str(safe_relative(info.filename))
                kind = stat.S_IFMT(info.external_attr >> 16)
                allowed = {prefix, prefix + ".libs", dist}
                if (name in files or name.split("/")[0] not in allowed or kind not in (0, stat.S_IFREG)
                        or name.endswith((".pth", ".pyc")) or info.flag_bits & 1):
                    raise ValueError("unsupported wheel layout or member")
                files[name] = archive.read(info)
        metadata = BytesParser().parsebytes(files[dist + "/METADATA"])
        wheel = BytesParser().parsebytes(files[dist + "/WHEEL"])
        if metadata["Name"].lower().replace("_", "-") != row["name"] or metadata["Version"] != VERSION or wheel["Wheel-Version"] != "1.0":
            raise ValueError("wheel metadata differs")
        tags = wheel.get_all("Tag", [])
        if not tags or not any(t in {"py3-none-any", "py2.py3-none-any"} or
            (t.split("-")[0] == python_tag and t.split("-")[1] == python_tag and any(p in platform_tags for p in t.split("-")[2].split("."))) for t in tags):
            raise ValueError("wheel is not compatible with actual interpreter")
        record = dist + "/RECORD"
        recorded = set()
        for entry in csv.reader(io.StringIO(files[record].decode())):
            if len(entry) != 3:
                raise ValueError("invalid RECORD row")
            name, hashvalue, size = entry
            if name in recorded or name not in files:
                raise ValueError("RECORD missing or duplicate member")
            recorded.add(name)
            if name == record:
                if hashvalue or size:
                    raise ValueError("RECORD self hash must be empty")
                continue
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).decode().rstrip("=")
            if hashvalue != expected or size != str(len(files[name])):
                raise ValueError("RECORD content hash mismatch")
        if recorded != set(files):
            raise ValueError("wheel contains unrecorded bytes")
        result[row["name"]] = {"files": files, "row": row, "requirements": metadata.get_all("Requires-Dist", []), "requires_python":metadata.get("Requires-Python"), "license": {"expression":metadata.get("License-Expression"),"text":metadata.get("License"),"classifiers":[v for v in metadata.get_all("Classifier",[]) if v.startswith("License ::")]}}
    return result

def apply_owned_pair(site, wheels, *, expected_before, python_tag="cp312", platform_tags=()):
    site = Path(site).resolve(strict=True)
    current = installed_pair(site)
    if current != expected_before or any(v["version"] != "3.3.4" for v in current.values()):
        raise ValueError("installed original ownership changed")
    validated = validate_wheel_delta(wheels, python_tag=python_tag, platform_tags=platform_tags)
    old = {n for d in current.values() for n in d["files"]}
    new = {n for d in validated.values() for n in d["files"]}
    # Unrecorded import caches belong to the exact old package roots only.
    caches = {p.relative_to(site).as_posix() for root in PAIR.values() for p in (site/root).rglob("*.pyc")}
    old |= caches
    for distribution in md.distributions(path=[str(site)]):
        if distribution.metadata['Name'].lower().replace('_','-') not in PAIR:
            if old & {str(f) for f in distribution.files or ()}:
                raise ValueError('driver files shared with another distribution')
    for name in old:
        allowed={root for root in PAIR.values()} | {root+'.libs' for root in PAIR.values()} | {root+'-3.3.4.dist-info' for root in PAIR.values()}
        if name.split('/')[0] not in allowed:
            raise ValueError('old ownership escapes exact driver roots')
    for name in new - old:
        if _inside(site, name).exists():
            raise ValueError("wheel overwrites unrelated installed file")
    before_other = _tree(site, old | new)
    for name in old:
        path = _inside(site, name)
        if path.exists():
            path.unlink()
    for distribution in validated.values():
        for name, raw in distribution["files"].items():
            path = _inside(site, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
            path.chmod(0o644)
    # Empty old dist-info directories still show up as distributions to metadata.
    for name in PAIR.values():
        for directory in site.glob(name + "-3.3.4.dist-info"):
            for child in sorted(directory.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                if child.is_dir():child.rmdir()
            directory.rmdir()
    after = installed_pair(site)
    if any(d["version"] != VERSION for d in after.values()) or _tree(site, old | new) != before_other:
        raise ValueError("overlay changed unrelated files or wrong driver version")
    receipt = {"contract": "EvaRuntimeWheelDelta/v1", "before": current, "after": after,
               "wheels": [v["row"] for v in validated.values()], "unrelated_tree_sha256": before_other}
    receipt["delta_sha256"] = digest(canonical(receipt))
    return receipt

def verify_effective_inventory(site, receipt):
    if receipt.get("contract") != "EvaRuntimeWheelDelta/v1" or installed_pair(site) != receipt["after"]:
        raise ValueError("effective dependency provenance differs")
    if digest(canonical({k:v for k,v in receipt.items() if k != "delta_sha256"})) != receipt["delta_sha256"]:
        raise ValueError("dependency delta receipt changed")
    return receipt["delta_sha256"]

if __name__ == "__main__":
    # This fixed program is invoked only during an offline disposable image build.
    manifest = json.loads(regular(Path(sys.argv[1]), maximum=1024*1024))
    site = Path(sysconfig.get_paths()["purelib"])
    receipt = apply_owned_pair(site, manifest["wheels"], expected_before=manifest["before"],
        python_tag=manifest["python_tag"], platform_tags=manifest["platform_tags"])
    Path(sys.argv[2]).write_bytes(canonical(receipt))
