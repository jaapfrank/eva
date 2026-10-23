"""Fixed policy, private file intake and the unchanged authenticated restore gate."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import portable_transport as transport
from tools.installer_portable_restore import InstallerPortableRestore
from tools.installer_restore_wizard import restore_operator_file

INSTALLATION = transport.TransportPolicy.INSTALLATION_RECOVERY

def header(length, policy):
    return transport._header(salt=b's'*transport.SALT_BYTES, nonce=b'n'*transport.NONCE_BYTES,
        plaintext_bytes=length, plaintext_sha256='a'*64, transport_policy=policy)

def test_large_header_requires_trusted_policy_and_cannot_escalate_itself():
    length=1_500_000_000
    with pytest.raises(transport.PortableTransportError):
        header(length, transport.TransportPolicy.PORTABLE)
    raw=header(length, INSTALLATION)
    assert transport._parse_header(raw, transport_policy=INSTALLATION)[2]==length
    with pytest.raises(transport.PortableTransportError):
        transport._parse_header(raw)
    value=json.loads(raw);value['transport_policy']='installation_recovery'
    from eva.application.portable_archive import canonical_json_bytes
    with pytest.raises(transport.PortableTransportError):
        transport._parse_header(canonical_json_bytes(value),transport_policy=INSTALLATION)
    with pytest.raises(transport.PortableTransportError):
        header(transport.MAX_INSTALLATION_ARCHIVE_BYTES+transport.MAX_ARCHIVE_FILES*1024+10_241,INSTALLATION)

@pytest.mark.parametrize('policy',[True,False,None,2_147_483_648,'installation_recovery',object()])
def test_invalid_policy_rejected_before_validation_or_output(tmp_path,policy):
    seen=[]
    with pytest.raises(transport.PortableTransportError):
        transport.seal_portable_archive(tmp_path,tmp_path/'out',passphrase='not used',
            validator=lambda p:seen.append(p),transport_policy=policy)
    assert not seen and not (tmp_path/'out').exists()

def test_cumulative_and_individual_file_budgets_use_metadata_not_allocations(tmp_path,monkeypatch):
    first=tmp_path/'first';second=tmp_path/'second';first.write_bytes(b'a');second.write_bytes(b'b')
    sizes={first:600_000_000,second:600_000_000};actual=os.scandir
    def measured(directory):
        with actual(directory) as entries:
            result=[]
            for entry in entries:
                value=entry.stat(follow_symlinks=False)
                if Path(entry.path) in sizes:
                    fields=list(value);fields[6]=sizes[Path(entry.path)];value=os.stat_result(fields)
                result.append(SimpleNamespace(name=entry.name,path=entry.path,stat=lambda follow_symlinks=False,v=value:v))
            return iter(result)
    monkeypatch.setattr(os,'scandir',measured)
    with pytest.raises(transport.PortableTransportError):list(transport._source_files(tmp_path))
    assert len(list(transport._source_files(tmp_path,transport_policy=INSTALLATION)))==2
    sizes[first]=transport.MAX_ARCHIVE_BYTES+1
    with pytest.raises(transport.PortableTransportError):list(transport._source_files(tmp_path,transport_policy=INSTALLATION))
    sizes[first]=transport.MAX_ARCHIVE_BYTES;sizes[second]=transport.MAX_ARCHIVE_BYTES
    assert len(list(transport._source_files(tmp_path,transport_policy=INSTALLATION)))==2
    third=tmp_path/'third';third.write_bytes(b'c')
    with pytest.raises(transport.PortableTransportError):list(transport._source_files(tmp_path,transport_policy=INSTALLATION))

def workflow(tmp_path,policy=transport.TransportPolicy.PORTABLE):
    workspace=tmp_path/'workspace';workspace.mkdir(mode=0o700)
    return InstallerPortableRestore(engine=object(),authority_reader=lambda:b'',workspace=workspace,
        object_store_root=tmp_path/'objects',transport_policy=policy,
        recovery_materialization={'destination':str(tmp_path/'inactive'),'owners':{c:{'uid':os.geteuid(),'gid':os.getegid()} for c in ['core','memory','gateway','documents','config']}})

@pytest.mark.parametrize('kind',['relative','symlink','hardlink','public'])
def test_private_file_boundary_precedes_staging(tmp_path,kind):
    w=workflow(tmp_path);source=tmp_path/'input';source.write_bytes(b'not decrypted');source.chmod(0o600)
    if kind=='relative':source=Path('relative')
    elif kind=='symlink':link=tmp_path/'link';link.symlink_to(source);source=link
    elif kind=='hardlink':os.link(source,tmp_path/'alias')
    else:source.chmod(0o644)
    with pytest.raises((ValueError,OSError)):w.prepare_file(source,'unused')
    assert not list(w.workspace.iterdir()) and w._pending is None

def test_file_is_copied_from_same_fd_exclusively_and_late_change_is_rejected(tmp_path,monkeypatch):
    w=workflow(tmp_path);source=tmp_path/'input';source.write_bytes(b'abc');source.chmod(0o600)
    def stage(copy,password):
        target=w.workspace/'copy';copy(target)
        assert target.read_bytes()==b'abc' and target.stat().st_mode&0o777==0o600
        return {'confirmation':'internal','summary':{}}
    monkeypatch.setattr(w,'_prepare_staged_source',stage)
    assert w.prepare_file(source,'unused')['confirmation']=='internal'
    (w.workspace/'copy').unlink()
    read=os.read;changed=[]
    def mutate(fd,n):
        data=read(fd,n)
        if data and not changed:source.write_bytes(b'def');changed.append(True)
        return data
    monkeypatch.setattr(os,'read',mutate)
    with pytest.raises(ValueError,match='changed'):w.prepare_file(source,'unused')
    assert w._pending is None

def test_space_shortfall_rejects_before_staging(tmp_path,monkeypatch):
    import tools.installer_portable_restore as installer
    w=workflow(tmp_path);source=tmp_path/'input';source.write_bytes(b'abc');source.chmod(0o600)
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ValueError,match='capacity'):w.prepare_file(source,'unused')
    assert not list(w.workspace.iterdir())

def test_operator_session_keeps_confirmation_and_passphrase_out_of_receipt(tmp_path):
    password=tmp_path/'password';password.write_text('a sufficiently long passphrase');password.chmod(0o600)
    calls=[]
    class Session:
        def prepare_file(self,path,secret):
            calls.append(('prepare',path,secret));return {'confirmation':'private-token','summary':{'ready':True}}
        def execute(self,token):calls.append(('execute',token));return {'status':'restored'}
    preview=restore_operator_file(Session(),tmp_path/'archive',password)
    assert preview=={'status':'preview','summary':{'ready':True}}
    done=restore_operator_file(Session(),tmp_path/'archive',password,execute=True)
    assert done=={'status':'executed','result':{'status':'restored'}}
    assert calls[-1]==('execute','private-token')
    assert 'private-token' not in json.dumps([preview,done])

def test_installation_opt_in_rejects_ordinary_authenticated_contract(tmp_path):
    w=workflow(tmp_path,INSTALLATION);archive=tmp_path/'ordinary';archive.mkdir();(archive/'archive.json').write_text('{"contract":"EvaPortableArchive/v3"}')
    with pytest.raises(ValueError,match='installation package'):w._validate_archive(archive)

# Reuse the existing genuine core fixture; its DBs are fresh isolated targets.
from tests.portable.test_installation_recovery_package import staged, current_archive

def test_writer_reuses_only_same_call_semantics_and_tamper_invalidates(staged,monkeypatch):
    from tools import installation_recovery_package as package
    from eva.application.portable_archive import canonical_json_bytes
    root,inputs=staged;calls=[];original=package._core
    def checked(*args,**kwargs):calls.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(package,'_core',checked)
    with package.installation_identity_trust(None):
        package.write_installation_package(root,**inputs)
        package.read_installation_package(root)
        assert len(calls)==1
        target=root/'core/current/profile.json';target.write_bytes(b'{}')
        manifest=json.loads((root/'archive.json').read_bytes())
        manifest['files']['core/current/profile.json']['sha256']=hashlib.sha256(b'{}').hexdigest()
        manifest['files']['core/current/profile.json']['size']=2
        (root/'archive.json').write_bytes(canonical_json_bytes(manifest))
        with pytest.raises(ValueError):package.read_installation_package(root)
        assert len(calls)==2
    with package.installation_identity_trust(None):
        with pytest.raises(ValueError):package.read_installation_package(root)
        assert len(calls)==3

def test_outer_manifest_counts_against_installation_budget_before_publication(staged,monkeypatch):
    from tools import installation_recovery_package as package
    root,inputs=staged;original=package._entries
    def measured(*args,**kwargs):
        entries=original(*args,**kwargs)
        first=next(iter(entries));total=sum(v['size'] for v in entries.values())
        entries[first]['size']+=transport.MAX_INSTALLATION_ARCHIVE_BYTES-total
        return entries
    monkeypatch.setattr(package,'_entries',measured)
    with pytest.raises(ValueError,match='complete transport limits'):
        package.write_installation_package(root,**inputs)
    assert not (root/'archive.json').exists()


def test_installation_unpack_keeps_individual_file_limit_without_large_allocation(tmp_path):
    import tarfile
    source=tmp_path/'large-declaration.tar'
    with tarfile.open(source,'w') as archive:
        item=tarfile.TarInfo('oversize');item.size=transport.MAX_ARCHIVE_BYTES+1;archive.addfile(item)
    assert source.stat().st_size<16_384
    target=tmp_path/'output';target.mkdir(mode=0o700)
    with pytest.raises(transport.PortableTransportError,match='byte limit'):
        transport._unpack_plaintext_tar(source,target,transport_policy=INSTALLATION)
    assert not list(target.iterdir())

@pytest.mark.parametrize('choice',[True,False,2_147_483_648,'auto',None])
def test_operator_config_cannot_invent_policy_before_engine_creation(tmp_path,monkeypatch,choice):
    from tools import installer_restore_wizard as launcher
    monkeypatch.setattr(launcher,'create_engine',lambda *a,**k:pytest.fail('no engine before policy'))
    config={'dsn':'not opened','authority':'unused','workspace':str(tmp_path),'objects':str(tmp_path/'objects'),
        'keyring':None,'traffic_closed':True,'runtime_guard':{},'installation_transport_policy':choice}
    with pytest.raises(ValueError,match='policy'):
        with launcher.configured_restore_workflow(config):pytest.fail('invalid policy entered')

def test_http_and_bytes_intake_remain_64_mib(tmp_path):
    from eva.adapters.api.installer_restore_api import MAX_ARCHIVE_BYTES
    assert MAX_ARCHIVE_BYTES==64*1024*1024
    class Oversize(bytes):
        def __len__(self):return 64*1024*1024+1
    w=workflow(tmp_path)
    with pytest.raises(ValueError,match='64 MiB'):w.prepare(Oversize(b'x'),'unused')
    assert not list(w.workspace.iterdir())


def test_operator_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    w=workflow(tmp_path);source=tmp_path/'fifo';os.mkfifo(source,0o600)
    with pytest.raises(ValueError,match='single-link'):w.prepare_file(source,'unused')
    assert not list(w.workspace.iterdir()) and w._pending is None


from tests.portable.test_portable_runtime_archive import runtime_archive

def test_authenticated_file_rejects_missing_authority_without_pending_or_effects(runtime_archive,tmp_path):
    from tests.portable.test_installer_runtime_archive import _workflow
    from tools.portable_runtime_archive import read_runtime_archive
    args,_=runtime_archive;w=_workflow(args,tmp_path);w.authority_reader=lambda:b'{}'
    blob=tmp_path/'source.eva'
    transport.seal_portable_archive(args['destination'],blob,passphrase='a sufficiently long passphrase',
        validator=lambda p:read_runtime_archive(p,identity_trust=args['identity_trust']))
    with pytest.raises(ValueError):w.prepare_file(blob,'a sufficiently long passphrase')
    assert w._pending is None and not list(w.workspace.iterdir())
    assert not w.identity_destination.exists()

def test_file_preview_tamper_rejects_before_restore(runtime_archive,tmp_path,monkeypatch):
    from tests.portable.test_installer_runtime_archive import _workflow
    from tools.portable_runtime_archive import read_runtime_archive
    import tools.portable_v2_restore as current
    import tools.portable_runtime_restore as restore
    args,_=runtime_archive;w=_workflow(args,tmp_path)
    monkeypatch.setattr(current,'verify_current_target',lambda c:None)
    monkeypatch.setattr(restore,'restore_runtime_archive',lambda *a,**k:pytest.fail('tampered preview reached effects'))
    blob=tmp_path/'source.eva'
    transport.seal_portable_archive(args['destination'],blob,passphrase='a sufficiently long passphrase',
        validator=lambda p:read_runtime_archive(p,identity_trust=args['identity_trust']))
    preview=w.prepare_file(blob,'a sufficiently long passphrase')
    (Path(w._pending[0].name)/'archive/runtime/identity.json').write_bytes(b'{}')
    with pytest.raises(ValueError):w.execute(preview['confirmation'])
    assert w._pending is None and not w.identity_destination.exists()


@pytest.mark.parametrize('selected',[False,True])
def test_only_trusted_configuration_selects_installation_policy(tmp_path,monkeypatch,selected):
    from tools import installer_restore_wizard as launcher
    from tests.portable.test_installer_runtime_archive import Engine
    monkeypatch.setattr(launcher,'create_engine',lambda *a,**k:Engine())
    private=tmp_path/'workspace';private.mkdir(mode=0o700)
    config={'dsn':'not opened','authority':str(tmp_path/'authority'),'workspace':str(private),
        'objects':str(tmp_path/'objects'),'keyring':None,'traffic_closed':True,'runtime_guard':{},
        'recovery_materialization':{'destination':str(tmp_path/'inactive'),'owners':{c:{'uid':os.geteuid(),'gid':os.getegid()} for c in ['core','memory','gateway','documents','config']}}}
    if selected:config['installation_transport_policy']='installation_recovery'
    with launcher.configured_restore_workflow(config) as current:
        assert current.transport_policy is (INSTALLATION if selected else transport.TransportPolicy.PORTABLE)


def test_operator_cli_rejects_workspace_override_before_config_or_secret_read(tmp_path):
    from tools.installer_restore_wizard import main
    with pytest.raises(SystemExit) as rejected:
        main(['--restore-config',str(tmp_path/'config'),'--restore-archive',str(tmp_path/'archive'),
            '--passphrase-file',str(tmp_path/'passphrase'),'--workspace',str(tmp_path/'other')])
    assert rejected.value.code==2


@pytest.mark.parametrize('kind',[b'x',b'g',b'L',b'K',b'X'])
def test_oversize_metadata_rejected_before_stdlib_allocation(tmp_path,kind):
    import tarfile
    source=tmp_path/'metadata.tar';item=tarfile.TarInfo('metadata');item.type=kind;item.size=transport.MAX_ARCHIVE_BYTES+1
    source.write_bytes(item.tobuf(format=tarfile.GNU_FORMAT)+b'\0'*1024)
    target=tmp_path/'output';target.mkdir(mode=0o700)
    with pytest.raises(transport.PortableTransportError,match='metadata exceeds'):
        transport._unpack_plaintext_tar(source,target,transport_policy=INSTALLATION)
    assert source.stat().st_size==1536 and not list(target.iterdir())

def _pax_record(key,value):
    body=f'{key}={value}\n'.encode();length=len(body)+2
    while True:
        result=str(length).encode()+b' '+body
        if len(result)==length:return result
        length=len(result)

@pytest.mark.parametrize('kind',[b'x',b'g',b'L',b'K'])
def test_metadata_header_chains_are_bounded_before_file_count(tmp_path,kind):
    import tarfile
    data=bytearray()
    for _ in range(20):
        payload=_pax_record('path','ordinary') if kind in {b'x',b'g'} else b'ordinary\0'
        item=tarfile.TarInfo('metadata');item.type=kind;item.size=len(payload)
        data.extend(item.tobuf(format=tarfile.GNU_FORMAT));data.extend(payload);data.extend(b'\0'*((-len(payload))%512))
    item=tarfile.TarInfo('ordinary');data.extend(item.tobuf());data.extend(b'\0'*1024)
    source=tmp_path/'chain.tar';source.write_bytes(data);target=tmp_path/'out';target.mkdir(mode=0o700)
    with pytest.raises(transport.PortableTransportError,match='metadata exceeds'):
        transport._unpack_plaintext_tar(source,target,transport_policy=INSTALLATION)
    assert not list(target.iterdir())

@pytest.mark.parametrize('policy',[transport.TransportPolicy.PORTABLE,INSTALLATION])
def test_own_writer_pax_unicode_long_paths_roundtrip(tmp_path,policy):
    source=tmp_path/'source';source.mkdir(mode=0o700)
    relative='/'.join(['lang'+('x'*70)]*4)+'/één.txt';path=source/relative;path.parent.mkdir(parents=True);path.write_bytes(b'original payload')
    blob=tmp_path/'sealed';opened=tmp_path/'opened'
    def valid(root):assert (root/relative).read_bytes()==b'original payload'
    transport.seal_portable_archive(source,blob,passphrase='a sufficiently long passphrase',validator=valid,transport_policy=policy)
    transport.open_portable_transport(blob,opened,passphrase='a sufficiently long passphrase',validator=valid,transport_policy=policy)
    assert (opened/relative).read_bytes()==b'original payload'


@pytest.mark.parametrize('kind',['global_fields','sparse'])
def test_global_mapping_and_pax_sparse_expansion_are_bounded(tmp_path,kind):
    import tarfile
    headers=({f'field{i}':'v' for i in range(1100)} if kind=='global_fields' else
        {'GNU.sparse.major':'1','GNU.sparse.minor':'0','GNU.sparse.realsize':str(transport.MAX_ARCHIVE_BYTES)})
    source=tmp_path/'metadata.tar'
    with tarfile.open(source,'w',format=tarfile.PAX_FORMAT,pax_headers=headers) as archive:
        archive.addfile(tarfile.TarInfo('ordinary'))
    target=tmp_path/'out';target.mkdir(mode=0o700)
    with pytest.raises(transport.PortableTransportError,match='metadata|sparse'):
        transport._unpack_plaintext_tar(source,target,transport_policy=INSTALLATION)
    assert not list(target.iterdir())


def test_global_metadata_budget_counts_utf8_bytes_across_headers(tmp_path):
    import tarfile
    data=bytearray()
    for key in ['first','second']:
        payload=_pax_record(key,'😀'*10_000)
        assert len(payload)<65_536
        item=tarfile.TarInfo('global');item.type=tarfile.XGLTYPE;item.size=len(payload)
        data.extend(item.tobuf(format=tarfile.GNU_FORMAT));data.extend(payload);data.extend(b'\0'*((-len(payload))%512))
    data.extend(tarfile.TarInfo('ordinary').tobuf());data.extend(b'\0'*1024)
    source=tmp_path/'utf8.tar';source.write_bytes(data);target=tmp_path/'out';target.mkdir(mode=0o700)
    with pytest.raises(transport.PortableTransportError,match='global metadata exceeds'):
        transport._unpack_plaintext_tar(source,target,transport_policy=INSTALLATION)
    assert not list(target.iterdir())
