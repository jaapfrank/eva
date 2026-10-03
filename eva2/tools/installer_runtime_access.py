"""Prepare reader-owned copies for new installer targets without changing sources."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_identity_storage import atomic_write_private
from eva.operations.model_credentials import (
    MODEL_LEASE_OWNER_UID, EVA_CORE_LEASE_READER_GID, EVA_CORE_LEASE_DIRECTORY_MODE,
    EVA_CORE_LEASE_FILE_MODE, CODEX_LEASE_READER_GID, CODEX_LEASE_DIRECTORY_MODE, CODEX_LEASE_FILE_MODE,
)
from tools.installer_target_bindings import TargetGraphBinding, _private_root


@dataclass(frozen=True, slots=True)
class RuntimeAccessResult:
    graph: dict
    receipt: dict


class RuntimeAccessError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = deepcopy(receipt)
        super().__init__("runtime file access incomplete; preserve new target files")


def _identity(service, image_users):
    value = service.get("user") or image_users.get(service["image"])
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+:[0-9]+",value):
        raise ValueError("verified numeric effective reader identity required")
    return tuple(map(int,value.split(":")))


def _inventory(source):
    """Retain only regular, singly linked input files; never follow tree links."""
    paths = [source] if source.is_file() else [source,*source.rglob("*")]
    if len(paths)>1024:
        raise ValueError("runtime materialization tree is too large")
    result=[]
    for path in paths:
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("runtime materialization contains unsupported entries")
        if stat.S_ISREG(info.st_mode) and (info.st_nlink!=1 or info.st_size>1048576):
            raise ValueError("runtime materialization file is not bounded and private")
        result.append((path,info))
    return result


def establish_installer_runtime_access(*, binding, materialized_bind_root, target_root, image_users,
                                      source_authority_directories=()):
    """Map a fully bound candidate to new reader-owned files; never start services.

    image_users is trusted inspected effective reader identity, including any
    entrypoint privilege drop. Docker Config.User alone does not establish it.
    The installer must have ownership-setting privilege; failure retains outputs.
    Source authority directories stay at their original paths and permissions.
    """
    if not isinstance(binding,TargetGraphBinding) or binding.graph is None or binding.pending:
        raise ValueError("complete candidate mount binding required")
    material=Path(materialized_bind_root);_private_root(material)
    root=Path(target_root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValueError("new access directory must be absolute without links")
    graph=deepcopy(binding.graph)
    authorities = {Path(directory): Path(directory) / filename
                   for directory, filename in source_authority_directories}
    for directory, selected in authorities.items():
        if selected.parent != directory or selected.resolve() != selected:
            raise ValueError("explicit original authority file required")
    groups={}
    for name,service in graph["services"].items():
        uid,gid=_identity(service,image_users)
        for mount in service.get("volumes",[]):
            if mount.get("type")!="bind":continue
            source=Path(mount["source"])
            if not source.is_absolute() or source.resolve()!=source or not source.exists():
                raise ValueError("runtime source bind is unavailable or linked")
            if mount.get("read_only") is not True and not source.is_relative_to(material):
                raise ValueError("writable authority source cannot be reused")
            if root == source or (source.is_dir() and root.is_relative_to(source)):
                raise ValueError("new access directory overlaps a source input")
            groups.setdefault(source,[]).append((name,mount,uid,gid))
    root.mkdir(mode=0o700,exist_ok=False)
    receipt={"contract":"EvaInstallerRuntimeAccess/v1","status":"preparing", "files":[],
             "bindings":[],"runtime_started":False,"operator_uid":os.geteuid(),
             "ownership_authority":"OS permission to chown new target files is required"}
    def save():atomic_write_private(root/"access-receipt.json",canonical_json_bytes(receipt))
    def assign(path,uid,gid,mode):
        os.chown(path,uid,gid,follow_symlinks=False)
        os.chmod(path,mode,follow_symlinks=False)
        info=path.lstat()
        if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(uid,gid,mode):
            raise ValueError("runtime ownership was not established")
    def copy(source,target,uid,gid,directory_mode=0o700,file_mode=0o600):
        entries=_inventory(source)
        for path,info in entries:
            output=target if path==source else target/path.relative_to(source)
            if stat.S_ISDIR(info.st_mode):output.mkdir(mode=0o700)
            else:
                raw=path.read_bytes()
                fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,"wb") as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                receipt["files"].append({"path":str(output),"sha256":hashlib.sha256(raw).hexdigest(),
                                         "uid":uid,"gid":gid,"mode":file_mode})
                save()
        # Assign directories last so the operator can finish children first.
        for path,info in reversed(entries):
            output=target if path==source else target/path.relative_to(source)
            assign(output,uid,gid,directory_mode if stat.S_ISDIR(info.st_mode) else file_mode)
    save()
    try:
        counter=0
        for source,consumers in groups.items():
            if (source.is_dir() and not source.is_relative_to(material)
                    and not all(mount["target"] == "/run/eva/voice" and mount.get("read_only") is True
                                for _, mount, _, _ in consumers)):
                # The maintained owner credential producer reads its existing
                # private authority; never change or duplicate refresh authority.
                for _,_,uid,_ in consumers:
                    # A deterministic credential producer reads one original
                    # auth pathname. Preserve its directory mount so os.replace
                    # remains visible; other Codex sessions are not inventoried.
                    inventory = ([(source,source.lstat()), * _inventory(authorities[source])]
                                 if source in authorities else _inventory(source))
                    for path,info in inventory:
                        required = 0o500 if stat.S_ISDIR(info.st_mode) else 0o400
                        mode = stat.S_IMODE(info.st_mode)
                        forbidden = 0o022 if source in authorities and path == source else 0o077
                        if info.st_uid!=uid or mode&forbidden or mode&required != required:
                            raise ValueError("source authority is not privately readable by runtime reader")
                for name,mount,uid,gid in consumers:
                    receipt["bindings"].append({"service":name,"target":mount["target"],
                        "source":str(source),"uid":uid,"gid":gid,"source_preserved":True})
                save();continue
            writers=[item for item in consumers if item[1].get("read_only") is not True]
            if writers:
                identities={(item[2],item[3]) for item in writers}
                if len(identities)!=1:raise ValueError("shared runtime writers have incompatible identities")
                uid,gid=next(iter(identities));directory_mode,file_mode=0o700,0o600
                destinations={item[1]["target"] for item in consumers}
                if "/run/eva/model-lease" in destinations:
                    if (uid,gid)!=(MODEL_LEASE_OWNER_UID,EVA_CORE_LEASE_READER_GID):
                        raise ValueError("model lease producer differs from maintained contract")
                    directory_mode,file_mode=EVA_CORE_LEASE_DIRECTORY_MODE,EVA_CORE_LEASE_FILE_MODE
                elif "/run/eva/hindsight-access" in destinations:
                    if (uid,gid)!=(MODEL_LEASE_OWNER_UID,CODEX_LEASE_READER_GID):
                        raise ValueError("Hindsight producer differs from maintained contract")
                    directory_mode,file_mode=CODEX_LEASE_DIRECTORY_MODE,CODEX_LEASE_FILE_MODE
                if any(reader_uid!=uid and not (reader_gid==gid and file_mode&0o040)
                       for _,_,reader_uid,reader_gid in consumers):
                    raise ValueError("shared runtime readers have incompatible identities")
                batches=[(consumers,uid,gid,directory_mode,file_mode)]
            else:
                identities={(item[2],item[3]) for item in consumers}
                batches=[([item for item in consumers if item[2:]==(uid,gid)],uid,gid,0o700,0o600)
                         for uid,gid in identities]
            for items,uid,gid,directory_mode,file_mode in batches:
                target=root/("input-"+str(counter));counter+=1
                copy(source,target,uid,gid,directory_mode,file_mode)
                for name,mount,_,_ in items:
                    mount["source"]=str(target)
                    receipt["bindings"].append({"service":name,"target":mount["target"],
                        "source":str(target),"uid":uid,"gid":gid,"source_preserved":False})
                save()
        receipt["status"]="access_established"
        save()
        return RuntimeAccessResult(graph,deepcopy(receipt))
    except Exception as error:
        receipt["status"]="incomplete";receipt["failure_type"]=type(error).__name__;save()
        raise RuntimeAccessError(receipt) from error
