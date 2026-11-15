"""Bounded artifact transfer from actual native programming workspaces.

No candidate path enters the host. Directory descriptors and O_NOFOLLOW protect
all path components; a copied immutable host receipt, not a mutable workspace,
is subsequently evaluated. This is not an execution or authority adapter.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES

_MAX_SUPPORT_FILES = 48
_MAX_SUPPORT_TOTAL_BYTES = 2 * 1024 * 1024
_SUBMODULE_NAME = re.compile(r'[a-z_][a-z0-9_]*')
_PACKAGE_EXTEND_SHIM = (
    b"import os as _os\n"
    b"from pathlib import Path as _Path\n"
    b"from pkgutil import extend_path as _extend_path\n"
    b"__path__ = _extend_path(__path__, __name__)\n"
    b"_saved_path = list(__path__)\n"
    b"for _p in _saved_path[1:]:\n"
    b"    _td = _Path(_p) / 'adapters/model/tokenizer_data'\n"
    b"    if _td.is_dir():\n"
    b"        _os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(_td))\n"
    b"for _p in _saved_path[1:]:\n"
    b"    _init = _Path(_p) / '__init__.py'\n"
    b"    if _init.is_file():\n"
    b"        exec(compile(_init.read_bytes(), str(_init), 'exec'), globals())\n"
    b"        break\n"
    b"__path__ = _saved_path\n"
)


def _eva_import_specs(relative_path: str, raw: bytes) -> tuple[list[str], list[tuple[str, tuple[str, ...]]]]:
    try:
        tree = ast.parse(raw, filename=relative_path)
    except SyntaxError:
        return [], []
    package = relative_path.rsplit('/', 1)[0] if '/' in relative_path else ''
    direct: list[str] = []
    from_specs: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith('eva.'):
                    mod = alias.name.removeprefix('eva.').replace('.', '/')
                    if mod:
                        direct.append(mod + '.py')
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if node.level > 0:
                parts = package.split('/') if package else []
                ascend = node.level - 1
                if ascend > len(parts):
                    continue
                base = '/'.join(parts[:len(parts) - ascend])
                prefix = (base + '/' + module.replace('.', '/')).strip('/') if module else base
            elif module == 'eva' or module.startswith('eva.'):
                prefix = module.removeprefix('eva').lstrip('.').replace('.', '/')
            else:
                continue
            names = tuple(a.name for a in node.names if a.name != '*')
            if prefix:
                direct.append(prefix + '.py')
            from_specs.append((prefix, names))
    return list(dict.fromkeys(direct)), from_specs


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
    def __init__(self,programming,*,support_source=None):
        self.programming=programming
        self.support_source=support_source
        self.root=Path(programming.config.workspace_root)
        if not self.root.is_absolute() or self.root.resolve()!=self.root:
            raise ValueError('trusted existing programming workspace root required')

    def read(self,metadata,name,*,maximum=1048576):
        return self.read_snapshot(metadata,name,maximum=maximum)[0]

    def read_snapshot(self,metadata,name,*,maximum=1048576):
        """Return parsed content and its exact guarded bytes from the same read."""
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
            return value,raw
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
        from eva.operations.runtime_repair_publisher import supportable_source
        from eva.operations.runtime_repair_target import repair_services
        image=target['installation'][repair_services(target)[0]]['image']
        files={}
        support={}
        for path,digest in target['sources'].items():
            if not repairable_source(path):raise ValueError('protected source snapshot')
            raw=source(image,path)
            if not isinstance(raw,bytes) or len(raw)>MAX_REPAIR_SOURCE_BYTES or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError('exact installed repair source snapshot required')
            files['repair/source/eva/'+path]=raw
            support[path]=raw

        allowed=supportable_source if self.support_source is not None else repairable_source
        def read_batch(candidates):
            batch=[p for p in dict.fromkeys(candidates) if allowed(p)]
            if not batch:
                return {}
            resolved={}
            for offset in range(0,len(batch),64):
                chunk=batch[offset:offset+64]
                if self.support_source is not None:
                    resolved.update(self.support_source(image,chunk))
                else:
                    for p in chunk:
                        item=source(image,p)
                        if item is not None and (not isinstance(item,bytes) or not 1<=len(item)<=MAX_REPAIR_SOURCE_BYTES):
                            raise ValueError('bounded support repair source required')
                        resolved[p]=item
            return resolved

        queue=list(support)
        probed=set(support)
        added_count=0
        added_bytes=0
        while queue and added_count<_MAX_SUPPORT_FILES and added_bytes<_MAX_SUPPORT_TOTAL_BYTES:
            current_batch=queue
            queue=[]
            direct_candidates=[]
            deferred_from=[]
            for rel_path in current_batch:
                direct,from_specs=_eva_import_specs(rel_path,support[rel_path])
                for cand in direct:
                    if cand not in probed and allowed(cand):
                        direct_candidates.append(cand)
                if from_specs:
                    deferred_from.extend(from_specs)
            probed.update(direct_candidates)
            fetched=read_batch(direct_candidates) if direct_candidates else {}
            package_candidates=[]
            for prefix,names in deferred_from:
                mod_file=(prefix+'.py') if prefix else ''
                if mod_file and (mod_file in support or fetched.get(mod_file) is not None):
                    continue
                if prefix:
                    init_file=prefix+'/__init__.py'
                    if init_file not in probed and allowed(init_file):
                        package_candidates.append(init_file)
                for name in names:
                    if _SUBMODULE_NAME.fullmatch(name):
                        sub=(prefix+'/'+name+'.py').lstrip('/')
                        if sub not in probed and allowed(sub):
                            package_candidates.append(sub)
            probed.update(package_candidates)
            if package_candidates:
                fetched.update(read_batch(package_candidates))
            for cand,raw in fetched.items():
                if raw is None or cand in support:
                    continue
                if added_count>=_MAX_SUPPORT_FILES or added_bytes+len(raw)>_MAX_SUPPORT_TOTAL_BYTES:
                    break
                support[cand]=raw
                added_count+=1
                added_bytes+=len(raw)
                queue.append(cand)

        package_dirs={''}
        for rel_path,raw in support.items():
            parts=rel_path.split('/')
            for depth in range(1,len(parts)):
                package_dirs.add('/'.join(parts[:depth]))
            if rel_path.endswith('/__init__.py') or rel_path=='__init__.py':
                files['repair/support/eva/'+rel_path]=(
                    b"import os as _eva_os\n"
                    b"from pathlib import Path as _eva_Path\n"
                    b"from pkgutil import extend_path as _eva_extend_path\n"
                    b"__path__ = _eva_extend_path(__path__, __name__)\n"
                    b"_eva_saved_path = list(__path__)\n"
                    b"for _eva_p in _eva_saved_path[1:]:\n"
                    b"    _eva_td = _eva_Path(_eva_p) / 'adapters/model/tokenizer_data'\n"
                    b"    if _eva_td.is_dir():\n"
                    b"        _eva_os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(_eva_td))\n"
                    + f"exec(compile({raw!r}, __file__, 'exec'), globals())\n".encode('ascii')
                    + b"__path__ = _eva_saved_path\n"
                )
            else:
                files['repair/support/eva/'+rel_path]=raw
        for pkg in sorted(package_dirs):
            init_rel=(pkg+'/__init__.py').lstrip('/')
            key='repair/support/eva/'+init_rel
            if key not in files:
                files[key]=_PACKAGE_EXTEND_SHIM

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
