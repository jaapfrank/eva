"""Fault evidence contains only Eva code metadata, never private exception data."""
from pathlib import Path
import pytest
from eva.application.self_repair import RepairObservation, repair_task


def test_observation_is_version_bound_and_does_not_copy_exception_text_or_locals(tmp_path):
    module = tmp_path / 'example.py'
    module.write_text("def fail():\n    private = 'SECRET_PRIVATE_TEXT'\n    raise RuntimeError(private)\n")
    namespace = {}
    exec(compile(module.read_text(), str(module), 'exec'), namespace)
    try:
        namespace['fail']()
    except RuntimeError as error:
        observation = RepairObservation.from_error(error, source_root=tmp_path)
    payload = observation.to_payload()
    assert len(payload['frames']) == 1
    assert payload['frames'][0]['path'] == 'example.py'
    assert payload['frames'][0]['function'] == 'fail:3'
    assert 'SECRET_PRIVATE_TEXT' not in str(payload)
    assert str(tmp_path) not in str(payload)
    assert 'SECRET_PRIVATE_TEXT' not in repair_task(observation, incident_id='synthetic-repair')
    old = observation.fingerprint
    module.write_text(module.read_text()+'\n# Changed implementation\n')
    try:
        namespace['fail']()
    except RuntimeError as error:
        changed = RepairObservation.from_error(error, source_root=tmp_path)
    assert changed.fingerprint != old


def test_external_traceback_is_not_promoted_to_own_repair_source(tmp_path):
    try:
        raise RuntimeError('Private provider body')
    except RuntimeError as error:
        observation = RepairObservation.from_error(error, source_root=tmp_path)
    assert observation.frames == ()


@pytest.mark.parametrize('frame',[
    ('../private.py','f:1','b'*64),('/private.py','f:1','b'*64),
    ('app.py','PRIVATE ERROR TEXT','b'*64),('app.py','f:1','not-a-hash'),
    ('app.py','f:0','b'*64),('a//b.py','f:1','b'*64),
])
def test_materialized_observation_cannot_smuggle_paths_or_private_text(frame):
    with pytest.raises(ValueError):RepairObservation('a'*64,(frame,))
