"""Canonical host-recovery routing: real PG/data/host copies, instrumented test guard.
The production Docker guard/lineage is retained unchanged; this fixture does not
claim a fresh production guard or shared installation proof.
"""
import sys
from pathlib import Path
# Same source-binding assertion as the private suite, now rooted in this checkout.
CANDIDATE=Path(__file__).resolve().parents[3]/'src/tools'
import json,os,hashlib
from copy import deepcopy
import pytest
import sqlalchemy as sa
from tools import installer_restore_wizard as wizard_module
from tools.installer_portable_restore import InstallerPortableRestore
from tools import installation_host_extension_restore as host
from tools.installation_recovery_package import write_installation_package,read_installation_package
from tools.installation_recovery_materializer import COMPONENTS
from tools.portable_transport import seal_portable_archive
from eva.application.installer_restore import InactiveRecoveryMaterialRetained
from eva.application.portable_archive import canonical_json_bytes
from tests.portable.restore_authority_fixture import restore_authority_for_archive
from tests.portable.test_installation_host_extension_restore import source,sdk_source
from tests.portable.test_installation_recovery_package import staged,current_archive,PASSPHRASE
from tests.portable.test_portable_v2_current_work import RUNTIME


def packaged(value,tmp_path,*,host_only=True):
    root,inputs,host_config,_=value
    if host_only:
        for name in list(inputs['file_roles']):
            if name.startswith('documents/project/'):
                (root/name).unlink();del inputs['file_roles'][name]
    package=write_installation_package(root,**inputs)
    sealed=tmp_path/'transport.eva'
    seal_portable_archive(root,sealed,passphrase=PASSPHRASE,validator=read_installation_package)
    authority=json.loads(restore_authority_for_archive(package['core_archive']['manifest_raw']))
    authority['authorized_archives']+=json.loads(restore_authority_for_archive(package['manifest_raw']))['authorized_archives']
    authority['authorized_archives'].sort(key=lambda item:item['archive_id'])
    private=tmp_path/'authority.json';private.write_bytes(canonical_json_bytes(authority));private.chmod(0o600)
    work=tmp_path/'wizard';work.mkdir(mode=0o700)
    config={'dsn':None,'authority':str(private),'workspace':str(work),'objects':str(tmp_path/'objects'),
        'keyring':None,'traffic_closed':True,'runtime_guard':{'fixture':'instrumented_not_docker'},'runtime_identity':RUNTIME,
        'recovery_materialization':{'destination':str(tmp_path/'inactive-package'),
            'owners':{name:{'uid':os.geteuid(),'gid':os.getegid()}for name in COMPONENTS}},
        'host_preparation':{'destination':str(tmp_path/'inactive-host'),'configuration':host_config}}
    return config,package,sealed


def selected(value,current_archive,tmp_path,monkeypatch,*,host_only=True):
    config,package,sealed=packaged(value,tmp_path,host_only=host_only)
    engine=current_archive['database']();config['dsn']=engine.url.render_as_string(hide_password=False)
    events=[]
    def guard(connection,*,expected):
        assert expected==config['runtime_guard'] and connection.in_transaction()
        held=connection.exec_driver_sql('SELECT held, archive_sha256 FROM eva_core.restore_activation').all()
        assert not held or held==[(True,package['archive_sha256'])]
        events.append('guard-held'if held else'guard-empty')
    monkeypatch.setattr(wizard_module,'verify_restore_runtime',guard)
    return config,package,sealed,engine,events


def empty(engine):
    with engine.connect()as connection:
        assert connection.exec_driver_sql('SELECT count(*) FROM eva_core.installations').scalar_one()==0
        assert connection.exec_driver_sql('SELECT count(*) FROM eva_core.restore_activation').scalar_one()==0


def test_trusted_composition_real_sdk_copies_follow_hold_and_authorization(sdk_source,current_archive,tmp_path,monkeypatch):
    config,package,sealed,engine,events=selected(sdk_source,current_archive,tmp_path,monkeypatch)
    original=host.prepare_host_extension
    def observe(package_root,target,**kwargs):
        assert events[-1]=='guard-held'
        assert read_installation_package(package_root)['archive_sha256']==package['archive_sha256']
        events.append('host-enter')
        result=original(package_root,target,**kwargs)
        events.append('host-return')
        return result
    monkeypatch.setattr(host,'prepare_host_extension',observe)
    with wizard_module.configured_restore_workflow(config)as workflow:
        assert Path(sys.modules[InstallerPortableRestore.__module__].__file__).is_relative_to(CANDIDATE)
        preview=workflow.prepare(sealed.read_bytes(),PASSPHRASE)
        assert preview['summary']['host_components']=='preparation_planned_inactive'
        result=workflow.execute(preview['confirmation'])
        assert result['host_components']=='prepared_inactive_not_verified'
        assert result['target_activation']=='closed' and result['host_preparation']['runtimeActivated']is False
        assert events.index('host-enter')>events.index('guard-held')
        assert events[events.index('host-return')+1]=='guard-held'
        receipt=json.loads((Path(config['host_preparation']['destination'])/'host-preparation.json').read_bytes())
        assert len(receipt['sdkAssembly'])==7
        assert receipt['importsOrInferencePerformed']is False and receipt['credentialsRebound']is False
        with engine.connect()as conn:
            assert conn.exec_driver_sql('SELECT held,archive_sha256 FROM eva_core.restore_activation').all()==[(True,package['archive_sha256'])]
        with pytest.raises(ValueError,match='confirmation is invalid'):workflow.execute(preview['confirmation'])


def test_fresh_authority_revoked_after_actual_host_copy_preserves_both_inactive_targets(source,current_archive,tmp_path,monkeypatch):
    config,package,sealed,engine,events=selected(source,current_archive,tmp_path,monkeypatch)
    original=host.prepare_host_extension
    def revoke(*args,**kwargs):
        result=original(*args,**kwargs)
        p=Path(config['authority']);authority=json.loads(p.read_bytes());authority.update(erasure_generation=2,authorized_archives=[])
        p.write_bytes(canonical_json_bytes(authority));return result
    monkeypatch.setattr(host,'prepare_host_extension',revoke)
    with wizard_module.configured_restore_workflow(config)as workflow:
        preview=workflow.prepare(sealed.read_bytes(),PASSPHRASE)
        with pytest.raises(InactiveRecoveryMaterialRetained)as error:workflow.execute(preview['confirmation'])
        assert error.value.destination==config['recovery_materialization']['destination']
        assert not error.value.activation_attempted and not error.value.runtime_start_attempted
        empty(engine)
        assert read_installation_package(Path(error.value.destination))['archive_sha256']==package['archive_sha256']
        assert (Path(config['host_preparation']['destination'])/'host-preparation.json').is_file()
        assert not list(Path(config['workspace']).iterdir())
        with pytest.raises(ValueError,match='confirmation is invalid'):workflow.execute(preview['confirmation'])


def test_missing_native_coverage_fails_preview_before_writes(source,current_archive,tmp_path,monkeypatch):
    config,_,sealed,engine,_=selected(source,current_archive,tmp_path,monkeypatch,host_only=False)
    with wizard_module.configured_restore_workflow(config)as workflow:
        with pytest.raises(ValueError,match='do not cover all retained'):workflow.prepare(sealed.read_bytes(),PASSPHRASE)
        empty(engine)
        assert not Path(config['recovery_materialization']['destination']).exists()
        assert not Path(config['host_preparation']['destination']).exists()


def test_native_overlap_refused_using_existing_validated_configuration(source,current_archive,tmp_path,monkeypatch):
    config,_,sealed,engine,_=selected(source,current_archive,tmp_path,monkeypatch)
    provider={'original_binding':'config/host-executor/service.json','catalog_path':'config/catalog.json',
        'document_directory':'documents/host-executor','journal_directory':'documents/test-journal',
        'logical_catalog_sha256':'a'*64,'project_keys':[],'runtime_uid':os.geteuid(),'runtime_gid':os.getegid(),
        'image':'sha256:'+'a'*64,'output_directory':str(tmp_path/'not-used-native')}
    workflow=InstallerPortableRestore(engine=engine,authority_reader=lambda:Path(config['authority']).read_bytes(),
            workspace=Path(config['workspace']),object_store_root=Path(config['objects']),runtime_identity=RUNTIME,
            runtime_guard=lambda connection:None,recovery_materialization=config['recovery_materialization'],
            host_preparation=config['host_preparation'],native_document_configuration={'providers':[provider]},
            external_restore=lambda *a,**kw:None,document_prepare=lambda *a,**kw:None)
    try:
        with pytest.raises(ValueError,match='authority ownership overlaps'):workflow.prepare(sealed.read_bytes(),PASSPHRASE)
        empty(engine)
    finally:workflow.close()


def test_default_without_host_selection_is_previous_inactive_route(source,current_archive,tmp_path,monkeypatch):
    config,_,sealed,engine,events=selected(source,current_archive,tmp_path,monkeypatch)
    target=config.pop('host_preparation')['destination']
    def never(*args,**kwargs):raise AssertionError('default must not prepare or activate host')
    monkeypatch.setattr(host,'prepare_host_extension',never)
    with wizard_module.configured_restore_workflow(config)as workflow:
        preview=workflow.prepare(sealed.read_bytes(),PASSPHRASE)
        assert 'host_components'not in preview['summary']
        result=workflow.execute(preview['confirmation'])
        assert 'host_preparation'not in result and len(events)==3
        assert not Path(target).exists()


@pytest.mark.parametrize('change',['none','authority-fields','activation','nested-target'])
def test_opt_in_cannot_choose_owners_start_or_overlap_package(source,current_archive,tmp_path,monkeypatch,change):
    config,_,_,engine,_=selected(source,current_archive,tmp_path,monkeypatch)
    if change=='none':config['host_preparation']=None
    elif change=='authority-fields':config['host_preparation']['uid']=0
    elif change=='activation':config['activation']={}
    else:config['host_preparation']['destination']=config['recovery_materialization']['destination']+'/host'
    with pytest.raises(ValueError):
        with wizard_module.configured_restore_workflow(config):raise AssertionError('invalid opt in admitted')
    empty(engine)


@pytest.mark.parametrize('alias',['objects-dotdot','host-dotdot','objects-parent-symlink','host-parent-symlink'])
def test_host_opt_in_requires_canonical_physical_roots_before_database_access(tmp_path,alias):
    work=tmp_path/'wizard';work.mkdir(mode=0o700)
    objects=tmp_path/'objects';host_target=objects/'host'
    if alias=='objects-dotdot':objects=tmp_path/'placeholder/../objects'
    elif alias=='host-dotdot':host_target=tmp_path/'placeholder/../objects/host'
    else:
        real=tmp_path/'real';real.mkdir(mode=0o700)
        link=tmp_path/'alias';link.symlink_to(real,target_is_directory=True)
        objects=real/'objects';host_target=objects/'host'
        if alias=='objects-parent-symlink':objects=link/'objects'
        else:host_target=link/'objects/host'
    class NoDatabase:
        def __getattr__(self,name):raise AssertionError('path admission must precede every database access')
    with pytest.raises(ValueError,match='canonical|disjoint'):
        InstallerPortableRestore(engine=NoDatabase(),authority_reader=lambda:b'',workspace=work,
            object_store_root=objects,runtime_guard=lambda c:None,
            recovery_materialization={'destination':str(tmp_path/'inactive'),
                'owners':{name:{'uid':os.geteuid(),'gid':os.getegid()}for name in COMPONENTS}},
            host_preparation={'destination':str(host_target),'configuration':{}})
