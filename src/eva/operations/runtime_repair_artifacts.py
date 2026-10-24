"""Bounded artifact transfer from actual native programming workspaces.

No candidate path enters the host. Directory descriptors and O_NOFOLLOW protect
all path components; a copied immutable host receipt, not a mutable workspace,
is subsequently evaluated. This is not an execution or authority adapter.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def _key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):
        raise ValueError('exact programming workspace identity required')
    return value


def _object(pairs):
    value={}
    for key,item in pairs:
        if key in value:
            raise ValueError('duplicate repair artifact field')
        value[key]=item
    return value


class RuntimeRepairArtifacts:
    def __init__(self,programming):
        self.programming=programming
        self.root=Path(programming.config.workspace_root)
        if not self.root.is_absolute() or self.root.resolve()!=self.root:
            raise ValueError('trusted existing programming workspace root required')

    def read(self,metadata,name,*,maximum=1048576):
        if name not in {'candidate.json','oracle.json','review.json','blocked.json'}:
            raise ValueError('unknown repair artifact')
        # A running builder explicitly requests handoff only when its processes
        # are idle; completed stages have already released their session.
        if metadata['state']=='call_started':
            status=self.programming.processes_idle(metadata['session_key'],
                workspace_key=metadata['workspace_key'])
            if status.get('idle') is not True:
                raise ValueError('repair programming process is still active')
        descriptors=[]
        try:
            fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);descriptors.append(fd)
            for part in (_key(metadata['workspace_key']),'repair'):
                fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);descriptors.append(fd)
            file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd);descriptors.append(file)
            before=os.fstat(file)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                    or not 1<=before.st_size<=maximum):
                raise ValueError('bounded regular repair artifact required')
            with os.fdopen(os.dup(file),'rb') as stream:
                raw=stream.read(maximum+1)
            after=os.fstat(file)
            if (len(raw)>maximum or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,
                    before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,
                    after.st_mtime_ns,after.st_ctime_ns)):
                raise ValueError('repair artifact changed during snapshot')
            value=json.loads(raw,object_pairs_hook=_object,parse_constant=lambda _:(_ for _ in ()).throw(
                ValueError('non-finite repair artifact')))
            if not isinstance(value,dict):
                raise ValueError('repair artifact must be an object')
            return value
        finally:
            for fd in reversed(descriptors):os.close(fd)

    def _seed(self,metadata,files):
        from eva.operations.programming_workspace import _prepare_workspace
        workspace=_prepare_workspace(self.root,_key(metadata['workspace_key']))
        for relative,raw in files.items():
            descriptors=[]
            try:
                fd=os.open(workspace,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);descriptors.append(fd)
                parts=relative.split('/')
                for part in parts[:-1]:
                    if not part or part in {'.','..'}:raise ValueError('invalid host snapshot path')
                    try:os.mkdir(part,mode=0o755,dir_fd=fd)
                    except FileExistsError:pass
                    fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);descriptors.append(fd)
                    if part=='repair':os.fchown(fd,10001,10001)
                name=parts[-1]
                try:
                    file=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode=0o444,dir_fd=fd)
                except FileExistsError:
                    file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    with os.fdopen(file,'rb') as stream:
                        info=os.fstat(stream.fileno())
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                                or info.st_size!=len(raw) or stream.read(len(raw)+1)!=raw):
                            raise ValueError('host-bound repair input changed')
                else:
                    with os.fdopen(file,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                os.fsync(fd)
            finally:
                for fd in reversed(descriptors):os.close(fd)

    def seed_sources(self,metadata,target,*,source):
        from eva.application.self_repair import repairable_source
        files={}
        for path,digest in target['sources'].items():
            if not repairable_source(path):raise ValueError('protected source snapshot')
            raw=source(target['installation']['core']['image'],path)
            if not isinstance(raw,bytes) or len(raw)>262144 or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError('exact installed repair source snapshot required')
            files['repair/source/eva/'+path]=raw
        if metadata.get('role') is None:
            # The builder receives its actual source/format handoff, without
            # candidate content or independent cases being manufactured here.
            from eva.operations.runtime_repair_target import repair_builder_handoff
            from eva.contracts.canonical_json import canonical_json_bytes
            files['repair/handoff.json'] = canonical_json_bytes(repair_builder_handoff(target))
        self._seed(metadata,files)

    def seed_review(self,metadata,inputs):
        """Prepare this independent review's inputs, never the builder workspace."""
        if metadata.get('role')!='independent_review' or set(inputs)!={
                'target','candidate','oracle','comparison'}:
            raise ValueError('exact independent review inputs required')
        from eva.contracts.canonical_json import canonical_json_bytes
        self._seed(metadata,{'repair/review-input/'+name+'.json':canonical_json_bytes(value)
            for name,value in inputs.items()})
