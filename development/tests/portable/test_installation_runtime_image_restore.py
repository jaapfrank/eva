import hashlib,io,json,tarfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools.installation_runtime_image_restore import validate_runtime_image_archive,load_held_runtime_images

def make_archive(path,corrupt=False,layers=1):
    layer=b'layer';lh=hashlib.sha256(layer).hexdigest()
    config=json.dumps(dict(os='linux',architecture='amd64',rootfs=dict(type='layers',diff_ids=['sha256:'+lh]*layers))).encode();ch=hashlib.sha256(config).hexdigest()
    manifest=json.dumps(dict(schemaVersion=2,config=dict(mediaType='application/vnd.oci.image.config.v1+json',digest='sha256:'+ch,size=len(config)),layers=[dict(mediaType='application/vnd.oci.image.layer.v1.tar',digest='sha256:'+lh,size=len(layer))]*layers)).encode();mh=hashlib.sha256(manifest).hexdigest()
    values={'oci-layout':b'{"imageLayoutVersion":"1.0.0"}','index.json':json.dumps(dict(schemaVersion=2,manifests=[dict(mediaType='application/vnd.oci.image.manifest.v1+json',digest='sha256:'+mh,size=len(manifest))])).encode(),'blobs/sha256/'+ch:config,'blobs/sha256/'+mh:manifest,'blobs/sha256/'+lh:b'bad!!' if corrupt else layer}
    with tarfile.open(path,'w') as tar:
        for name,raw in values.items():
            info=tarfile.TarInfo(name);info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
    path.chmod(0o600);return 'sha256:'+ch

def test_exact_local_runtime_layout_and_held_load(tmp_path):
    path=tmp_path/'runtime.tar';image=make_archive(path);proof=validate_runtime_image_archive(path,image)
    artifact=dict(proof,path=str(path),package_path='documents/runtime-images/runtime.tar',role='document_artifact')
    pair=dict(core=image,**{'delegated-work-worker':image})
    receipt=dict(contract='EvaRetainedRuntimeImages/v1',before=pair,after=pair,artifacts={image:artifact})
    commands=[];guards=[]
    def run(args,**kw):
        commands.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps([dict(Id=image)]))
    result=load_held_runtime_images(receipt,resolve=lambda _:path,guard=lambda:guards.append(True),run=run)
    assert result['activated'] is False and result['services_started'] is False
    assert len(guards)==2 and not any('start' in args for args in commands)

def test_reject_corrupt_blob(tmp_path):
    path=tmp_path/'bad.tar';image=make_archive(path,True)
    with pytest.raises(RuntimeError,match='digest'):validate_runtime_image_archive(path,image)

def test_no_load_if_wrong_package_digest(tmp_path):
    path=tmp_path/'runtime.tar';image=make_archive(path)
    with pytest.raises(ValueError,match='hash changed'):validate_runtime_image_archive(path,image,expected_sha256='0'*64)

def test_reject_wrong_image_identity(tmp_path):
    path=tmp_path/'runtime.tar';make_archive(path)
    with pytest.raises(ValueError,match='image ID'):validate_runtime_image_archive(path,'sha256:'+'f'*64)


def test_actual_core_layer_count_supported_with_finite_bound(tmp_path):
    path=tmp_path/'core271.tar';image=make_archive(path,layers=271)
    assert validate_runtime_image_archive(path,image)['image']==image
    too_many=tmp_path/'too-many.tar';image=make_archive(too_many,layers=513)
    with pytest.raises(ValueError,match='root filesystem'):validate_runtime_image_archive(too_many,image)
