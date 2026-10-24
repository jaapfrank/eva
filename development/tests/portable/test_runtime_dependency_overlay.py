import base64,csv,hashlib,io,json,zipfile
from pathlib import Path
import pytest
from tools.runtime_dependency_overlay import installed_pair,apply_owned_pair,validate_wheel_delta,verify_effective_inventory

def package(root,name,version):
    prefix=name.replace('-','_');dist=prefix+'-'+version+'.dist-info'
    files={prefix+'/__init__.py':('version='+repr(version)+'\n').encode(),dist+'/METADATA':('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\n').encode(),dist+'/WHEEL':b'Wheel-Version: 1.0\nTag: py3-none-any\n'}
    out=io.StringIO();writer=csv.writer(out,lineterminator='\n')
    for key,raw in files.items():writer.writerow([key,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('='),str(len(raw))])
    writer.writerow([dist+'/RECORD','','']);files[dist+'/RECORD']=out.getvalue().encode()
    return files

def fixture(tmp_path):
    site=tmp_path/'site';site.mkdir();wheels=[]
    for name in ('psycopg','psycopg-binary'):
        for key,raw in package(site,name,'3.3.4').items():
            p=site/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        wheel=tmp_path/(name.replace('-','_')+'-3.3.6-py3-none-any.whl')
        with zipfile.ZipFile(wheel,'w') as z:
            for key,raw in package(site,name,'3.3.6').items():z.writestr(key,raw)
        raw=wheel.read_bytes();wheels.append(dict(name=name,version='3.3.6',path=str(wheel),filename=wheel.name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
    return site,wheels

def test_pair_overlay_preserves_unrelated_and_rejects_second_apply(tmp_path):
    site,wheels=fixture(tmp_path);(site/'unrelated.py').write_bytes(b'important')
    before=installed_pair(site);receipt=apply_owned_pair(site,wheels,expected_before=before)
    assert (site/'unrelated.py').read_bytes()==b'important'
    assert set(d['version'] for d in installed_pair(site).values())=={'3.3.6'}
    assert verify_effective_inventory(site,receipt)==receipt['delta_sha256']
    assert not list(site.glob('*3.3.4.dist-info'))
    with pytest.raises(ValueError):apply_owned_pair(site,wheels,expected_before=before)

def test_bad_wheel_hash_touches_nothing(tmp_path):
    site,wheels=fixture(tmp_path);before=installed_pair(site);wheels[1]['sha256']='0'*64
    with pytest.raises(ValueError,match='official pin'):apply_owned_pair(site,wheels,expected_before=before)
    assert installed_pair(site)==before

def test_record_tampering_rejected_even_with_updated_outer_hash(tmp_path):
    _,wheels=fixture(tmp_path);path=Path(wheels[0]['path'])
    with zipfile.ZipFile(path,'a') as z:z.writestr('psycopg/injected.py',b'bad')
    raw=path.read_bytes();wheels[0].update(sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
    with pytest.raises(ValueError,match='unrecorded'):validate_wheel_delta(wheels)

def test_current_source_change_prevents_overlay(tmp_path):
    site,wheels=fixture(tmp_path);before=installed_pair(site);(site/'psycopg/__init__.py').write_text('changed')
    with pytest.raises(ValueError,match='ownership changed'):apply_owned_pair(site,wheels,expected_before=before)

def test_distribution_symlink_rejected(tmp_path):
    site,wheels=fixture(tmp_path);target=site/'psycopg/__init__.py';target.unlink();target.symlink_to(site/'unrelated.py');(site/'unrelated.py').write_text('outside')
    with pytest.raises(ValueError,match='symlink'):installed_pair(site)
