"""Inactive host ownership and closed dependency extraction, no runtime claims."""
from copy import deepcopy
import base64
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import pytest
from tools import installation_host_extension_restore as host
from tools.installation_recovery_package import write_installation_package
from tools.installer_document_components import build_document_components
from tests.portable.test_installation_recovery_package import staged, current_archive


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bundle(files, *, extra=None):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w',format=tarfile.PAX_FORMAT) as archive:
        for name,raw in list(files.items())+list((extra or {}).items()):
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o7777;item.uid=9999;item.gid=9999
            archive.addfile(item,io.BytesIO(raw))
    return gzip.compress(output.getvalue(),mtime=0)


@pytest.fixture
def source(staged):
    root,inputs=staged
    archive_path='documents/host-executor/dependencies/sdk.tar.gz'
    inventory_path='documents/host-executor/dependencies/sdk.manifest.json'
    binding_path='config/host-executor/service.json'
    payloads={'lib/python3.12/site-packages/example/__init__.py':b'raise RuntimeError("never import inactive payload")\n',
              'bin/example':b'#!/old/reference/bin/python\n'}
    raw=bundle(payloads)
    inventory={'contract':'EvaExistingDependencyByteCapture/v1','archive':{'packagePath':archive_path,'sha256':digest(raw),'bytes':len(raw)},
        'fileCount':len(payloads),'sourcePayloadBytes':sum(map(len,payloads.values())),
        'files':{name:{'sha256':digest(value),'bytes':len(value),'mode':'07777','uid':9999,'gid':9999,'distributionOwners':['synthetic'],'originalRecordHashMatched':True} for name,value in payloads.items()}}
    retained={archive_path:raw,inventory_path:json.dumps(inventory).encode(),binding_path:b'{"synthetic":"unchanged original binding"}'}
    for name,value in retained.items():
        path=root/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(value)
        inputs['file_roles'][name]='document_artifact' if name==archive_path else ('document_marker' if name==inventory_path else 'provider_binding')
    config={'retained_paths':sorted(retained),'dependency_bundles':[{'name':'sdk','archive':archive_path,'inventory':inventory_path}]}
    return root,inputs,config,payloads


def prepare(source,tmp_path):
    root,inputs,config,payloads=source
    write_installation_package(root,**inputs)
    guards=[];target=tmp_path/'prepared-host'
    result=host.prepare_host_extension(root,target,configuration=config,verify_guard=lambda:guards.append(True))
    return root,target,result,guards


def test_exact_inactive_bytes_permissions_and_original_bindings(source,tmp_path):
    root,target,result,guards=prepare(source,tmp_path)
    assert result['status']=='prepared_inactive' and result['runtimeActivated'] is False
    assert result['functionalHostRestoreProven'] is False and len(guards)>=3
    config=source[2]
    assert host.read_host_preparation(root,target)==frozenset(config['retained_paths'])
    for name,value in source[3].items():
        path=target/'expanded/sdk'/name
        assert path.read_bytes()==value and path.stat().st_mode&0o777==0o600
        assert path.stat().st_uid==os.geteuid()
    for name in config['retained_paths']:assert (target/'retained'/name).read_bytes()==(root/name).read_bytes()
    assert (target/'retained/config/host-executor/service.json').stat().st_mode&0o777==0o400
    for current,_,_ in os.walk(target):assert Path(current).stat().st_mode&0o777==0o700
    assert not list(target.glob('*.tar'))
    receipt=json.loads((target/'host-preparation.json').read_bytes())
    assert receipt['importsOrInferencePerformed'] is False and receipt['credentialsRebound'] is False
    assert receipt['derivedBindings']==[] and receipt['activationBlockers']


def test_repeat_refuses_without_changing_published_or_source_bytes(source,tmp_path):
    root,target,result,guards=prepare(source,tmp_path)
    before={str(p.relative_to(target)):digest(p.read_bytes())for p in target.rglob('*')if p.is_file()}
    with pytest.raises(FileExistsError):
        host.prepare_host_extension(root,target,configuration=source[2],verify_guard=lambda:None)
    assert before=={str(p.relative_to(target)):digest(p.read_bytes())for p in target.rglob('*')if p.is_file()}


@pytest.mark.parametrize('change',['bytes','mode','extra','symlink'])
def test_prepared_owner_cannot_claim_changed_or_unlisted_payload(source,tmp_path,change):
    root,target,_,_=prepare(source,tmp_path)
    item=target/'expanded/sdk/bin/example'
    if change=='bytes':item.write_bytes(b'changed')
    elif change=='mode':item.chmod(0o755)
    elif change=='extra':(target/'unexpected').write_bytes(b'extra')
    else:item.unlink();item.symlink_to(root/'archive.json')
    with pytest.raises((ValueError,OSError)):host.read_host_preparation(root,target)


def test_guard_failure_keeps_destination_absent_and_originals_unchanged(source,tmp_path):
    root,inputs,config,_=source;write_installation_package(root,**inputs)
    calls=[]
    def guard():
        calls.append(True)
        if len(calls)==3:raise ValueError('fresh guard changed')
    with pytest.raises(ValueError,match='guard changed'):
        host.prepare_host_extension(root,tmp_path/'target',configuration=config,verify_guard=guard)
    assert not (tmp_path/'target').exists() and not list(tmp_path.glob('.host-inactive-*'))


def test_real_gzip_expansion_has_hard_bound_without_gib_allocation(tmp_path):
    value=gzip.compress(b'x'*8192)
    with pytest.raises(ValueError,match='decompression budget'):host._decompress(value,tmp_path/'plain',limit=1024)
    assert not (tmp_path/'plain').exists()
    with pytest.raises(ValueError):host._decompress(value,tmp_path/'plain',limit=host._MAX_TAR+1)
    old=tmp_path/'old';old.write_bytes(b'original')
    with pytest.raises(FileExistsError):host._decompress(value,old,limit=1024)
    assert old.read_bytes()==b'original'


@pytest.mark.parametrize('change',['extra','traversal','hash','trailing'])
def test_closed_original_tar_names_sizes_hashes_and_trailing_bytes(tmp_path,change):
    values={'ordinary':b'payload'};raw=bundle(values)
    expected={'ordinary':{'bytes':7,'sha256':digest(b'payload')}}
    if change=='extra':raw=bundle(values,extra={'unlisted':b'extra'})
    elif change=='traversal':raw=bundle({'../escape':b'payload'})
    elif change=='hash':expected['ordinary']['sha256']='0'*64
    else:raw=gzip.compress(gzip.decompress(raw)+b'not listed trailing payload')
    with pytest.raises(ValueError):host._expand(raw,tmp_path/'work.tar',tmp_path/'out',expected)
    assert not (tmp_path/'escape').exists() and not (tmp_path/'work.tar').exists()


def builder(root,target,prepared):
    return build_document_components(package_root=root,layouts=[],runtime_image='sha256:'+'a'*64,
        runtime_uid=os.geteuid(),runtime_gid=os.getegid(),credentials={k:'private-file'for k in ['jwt_secret_file','encryption_secret_file','admin_password_file']},
        network_id='not used for empty document plan',namespace='host-test',workspace=target.parent/'doc-target',
        gateway_database=root/'gateway/contextforge.sqlite',host_preparation=prepared)


def test_empty_layouts_still_reject_unowned_native_documents(source,tmp_path):
    root,target,_,_=prepare(source,tmp_path)
    with pytest.raises(ValueError,match='do not cover all retained documents'):builder(root,target,target)
    with pytest.raises((ValueError,OSError)):builder(root,target,tmp_path/'missing')


def test_empty_native_layouts_only_after_real_host_coverage(source,tmp_path):
    root,inputs,config,_=source
    for name in list(inputs['file_roles']):
        if name.startswith('documents/project/'):
            (root/name).unlink();del inputs['file_roles'][name]
    write_installation_package(root,**inputs)
    target=tmp_path/'host-only'
    host.prepare_host_extension(root,target,configuration=config,verify_guard=lambda:None)
    assert builder(root,target,target)=={}


def test_operator_cannot_drop_a_host_capture_or_claim_regular_document_namespace(source,tmp_path):
    root,inputs,config,_=source;write_installation_package(root,**inputs)
    altered=deepcopy(config);altered['retained_paths'].pop()
    with pytest.raises(ValueError,match='complete retained'):
        host.prepare_host_extension(root,tmp_path/'dropped',configuration=altered,verify_guard=lambda:None)
    altered=deepcopy(config);altered['retained_paths'].append('documents/project/report.md')
    with pytest.raises(ValueError,match='outside the retained'):
        host.prepare_host_extension(root,tmp_path/'claimed',configuration=altered,verify_guard=lambda:None)


@pytest.fixture
def sdk_source(source):
    root, inputs, config, shared = source
    config['dependency_bundles'][0]['name'] = 'bridge'
    reference = 'documents/host-executor/venv/distribution-metadata'
    site = 'lib/python3.12/site-packages/'
    distribution = 'example-1.0.dist-info'
    additional = {site+'additional.py': b'raise RuntimeError("inactive only")\n',
                  site+'original.pth': b'import never_activate_this_original_hook\n',
                  site+distribution+'/METADATA': b'Name: example\nVersion: 1.0\n',
                  site+distribution+'/WHEEL': b'Wheel-Version: 1.0\n'}
    rows = []
    for path, raw in {**shared, **additional}.items():
        relative = path[len(site):] if path.startswith(site) else '../../../'+path
        rows.append([relative, 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('='), str(len(raw))])
    rows.append([distribution+'/RECORD', '', ''])
    stream=io.StringIO(newline='');csv.writer(stream).writerows(rows)
    additional[site+distribution+'/RECORD'] = stream.getvalue().encode()
    # Include a duplicate shared file: deterministic order must really copy the
    # independently retained bridge bytes rather than depend on the old source.
    additional[site+'example/__init__.py'] = shared[site+'example/__init__.py']
    archive_path='documents/host-executor/dependencies/supplement.tar.gz'
    inventory_path='documents/host-executor/dependencies/supplement.manifest.json'
    raw=bundle(additional)
    inventory={'contract':'EvaExistingDependencyByteCapture/v1',
        'archive':{'packagePath':archive_path,'sha256':digest(raw),'bytes':len(raw)},
        'fileCount':len(additional),'sourcePayloadBytes':sum(map(len,additional.values())),
        'files':{name:{'sha256':digest(value),'bytes':len(value)}for name,value in additional.items()}}
    values={archive_path:raw, inventory_path:json.dumps(inventory).encode()}
    for leaf in ['METADATA','WHEEL','RECORD']:
        values[reference+'/'+distribution+'/'+leaf]=additional[site+distribution+'/'+leaf]
    for path,value in values.items():
        item=root/path;item.parent.mkdir(parents=True,exist_ok=True,mode=0o700);item.write_bytes(value)
        inputs['file_roles'][path]='document_artifact' if path==archive_path else 'document_marker'
    config['retained_paths']+=sorted(values)
    config['dependency_bundles'].append({'name':'supplement','archive':archive_path,'inventory':inventory_path})
    config['sdk_assembly']={'name':'sdk','reference_directory':reference,'bundle_names':['bridge','supplement']}
    return source


def test_original_sdk_physically_assembled_from_shared_and_supplement(sdk_source,tmp_path):
    root,target,result,_=prepare(sdk_source,tmp_path)
    receipt=json.loads((target/'host-preparation.json').read_bytes())
    assembly=receipt['sdkAssembly']
    assert len(assembly)==7
    shared='assembled/sdk/lib/python3.12/site-packages/example/__init__.py'
    assert assembly[shared]['source']=='expanded/bridge/lib/python3.12/site-packages/example/__init__.py'
    sources={item['source'].split('/')[1]for item in assembly.values()}
    assert sources=={'bridge','supplement'}
    for name,item in assembly.items():
        path=target/name;source_path=target/item['source']
        assert path.read_bytes()==source_path.read_bytes()
        assert path.stat().st_ino!=source_path.stat().st_ino and path.stat().st_nlink==1
        assert path.stat().st_mode&0o777==0o600
    assert 'sdk_launchers_and_pth_not_activated' in receipt['activationBlockers']
    assert host.read_host_preparation(root,target)==frozenset(sdk_source[2]['retained_paths'])
    # Rebinding provenance to another equal payload is not an accepted receipt.
    receipt['sdkAssembly'][shared]['source']='expanded/supplement/lib/python3.12/site-packages/example/__init__.py'
    marker=target/'host-preparation.json';marker.chmod(0o600);marker.write_bytes(host._encoded(receipt));marker.chmod(0o400)
    with pytest.raises(ValueError,match='source provenance'):host.read_host_preparation(root,target)


@pytest.mark.parametrize('change',['missing','unhashed','escape'])
def test_sdk_record_cannot_be_satisfied_by_old_source_or_unverified_payload(sdk_source,tmp_path,change):
    root,inputs,config,_=sdk_source
    record=root/'documents/host-executor/venv/distribution-metadata/example-1.0.dist-info/RECORD'
    rows=list(csv.reader(io.StringIO(record.read_text())))
    if change=='missing':rows[0][0]='not-in-captured-bundles.py'
    elif change=='unhashed':rows[0][1]=''
    else:rows[0][0]='../../../../escape'
    stream=io.StringIO(newline='');csv.writer(stream).writerows(rows);record.write_text(stream.getvalue())
    write_installation_package(root,**inputs)
    with pytest.raises(ValueError):
        host.prepare_host_extension(root,tmp_path/'rejected',configuration=config,verify_guard=lambda:None)
    assert not (tmp_path/'rejected').exists() and not list(tmp_path.glob('.host-inactive-*'))
