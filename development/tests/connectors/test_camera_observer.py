from datetime import UTC, datetime, timedelta
import threading
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.camera_observer import CameraObservationSource, discover_cameras
from eva.adapters.connectors.home_assistant_camera import CameraSnapshot
from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.application.sensory import SensoryEvent
from eva.contracts.camera_observation import normalize_camera_observation


def inventory(prefix='unrelated', device='arbitrary-device'):
    rows = [{'entity_id': 'camera.'+prefix+'_big', 'device_id': device, 'platform': 'different_vendor'},
            {'entity_id': 'camera.'+prefix+'_small', 'device_id': device, 'platform': 'different_vendor'},
            {'entity_id': 'binary_sensor.'+prefix, 'device_id': device, 'translation_key': 'person_detected'},
            {'entity_id': 'camera.occupant_map', 'device_id': 'robot'},
            {'entity_id': 'binary_sensor.elsewhere', 'device_id': 'other', 'translation_key': 'person_detected'}]
    states = [{'entity_id': row['entity_id'], 'state': 'off', 'attributes': {
        'width': 1920 if row['entity_id'].endswith('big') else 640, 'height': 480}} for row in rows]
    return rows, states


def event():
    return SensoryEvent('home_assistant', 'home-assistant:synthetic-only', 'state_changed',
        datetime.now(UTC).replace(microsecond=0), {'entity_id': 'binary_sensor.unrelated', 'old_state': 'off', 'new_state': 'on'})


def source(vision=None, faces=None):
    registry = discover_cameras(*inventory())
    snaps = []
    def snapshot(entity):
        snaps.append(entity)
        return CameraSnapshot(entity, 'image/jpeg', b'\xff\xd8\xffsynthetic', datetime.now(UTC))
    wrapper = CameraObservationSource(None, discovery=SimpleNamespace(discover=lambda: registry),
        snapshots=SimpleNamespace(snapshot=snapshot), vision=vision, faces=faces)
    wrapper._refresh_inventory()
    return wrapper, snaps


def binding():
    return HouseholdInitiativeBinding('tenant', 'install', 'house', 'household-device:'+'a'*32,
        'assist_satellite.fixture', 'a'*32, 'household-consent:fixture', 'b'*64)


def test_registry_join_survives_different_names_and_ignores_map_and_unrelated_sensor():
    first = discover_cameras(*inventory())
    renamed = discover_cameras(*inventory('renamed', 'other-device'))
    assert first.discovered_camera_count == 3 and first.unsupported_camera_count == 1
    assert len(first.devices) == len(renamed.devices) == 1
    assert first.devices[0].camera_entity_id == 'camera.unrelated_small'
    assert renamed.devices[0].camera_entity_id == 'camera.renamed_small'
    assert renamed.devices[0].trigger_entity_ids == ('binary_sensor.renamed',)
    assert first.revision != renamed.revision


def test_disabled_removed_and_unavailable_cameras_are_not_selected():
    rows, states = inventory()
    rows[0]['disabled_by'] = 'user'
    states[1]['state'] = 'unavailable'
    assert discover_cameras(rows, states).devices == ()
    assert discover_cameras(rows[:2], states).devices == ()


def test_model_output_and_known_candidate_keep_image_origin_and_shared_scope():
    def recognize(snapshot):
        return {'contract': 'EvaLocalFaceObservation/v1', 'image_sha256': snapshot.sha256,
            'analyzed_at': datetime.now(UTC).isoformat(), 'backend': 'arcface', 'backend_revision': 'fixture',
            'profile_revision': 'c'*64, 'evidence_kind': 'face_similarity_candidate_not_authentication',
            'faces': [{'region': [0, 0, 50, 50], 'status': 'known_candidate', 'person_label': 'Synthetic enrolled person',
                       'similarity_ppm': 600000, 'margin_ppm': 200000}]}
    wrapper, snaps = source(faces=SimpleNamespace(analyze=recognize))
    original = event()
    observed = wrapper.enrich(original, wrapper._inventory.devices[0], stop=threading.Event())
    assert observed.source_event_id == original.source_event_id and original.payload.get('camera_observation') is None
    assert len(snaps) == 1
    evidence = observed.payload['camera_observation']
    assert evidence['status'] == 'observed' and evidence['captured_at'] is None
    assert evidence['identity_is_not_authentication'] is True
    assert evidence['integration'] == 'different_vendor'
    bound = binding().observation_event(observed)
    assert bound.payload['household_binding'] == binding().to_payload()
    assert bound.payload['observation']['payload']['camera_observation'] == evidence
    assert binding().matches_event(bound)
    assert 'data' not in evidence and 'embedding' not in str(evidence)


def test_failure_stale_trigger_and_stop_do_not_fake_a_seen_person():
    def fail(snapshot):
        raise RuntimeError('private backend information must not be copied')
    wrapper, snaps = source(faces=SimpleNamespace(analyze=fail))
    observed = wrapper.enrich(event(), wrapper._inventory.devices[0], stop=threading.Event())
    assert observed.payload['camera_observation']['recognition_error'] == 'RuntimeError'
    assert 'private backend' not in str(observed.payload)
    assert observed.payload['camera_observation']['status'] == 'analysis_unavailable'
    stale = event()
    stale = SensoryEvent(stale.source, stale.source_event_id, stale.event_type, stale.occurred_at-timedelta(minutes=2), stale.payload)
    assert wrapper.enrich(stale, wrapper._inventory.devices[0], stop=threading.Event()).payload['camera_observation']['status'] == 'stale_trigger'
    stop = threading.Event(); stop.set()
    wrapper.enrich(event(), wrapper._inventory.devices[0], stop=stop)
    assert len(snaps) == 1


def test_forbidden_raw_data_and_unknown_identity_label_cannot_cross_shared_seam():
    wrapper, _ = source()
    evidence = wrapper.enrich(event(), wrapper._inventory.devices[0], stop=threading.Event()).payload['camera_observation']
    with pytest.raises(ValueError):
        normalize_camera_observation({**evidence, 'raw_image': 'never'})
    with pytest.raises(ValueError):
        normalize_camera_observation({**evidence, 'identity_is_not_authentication': False})


def test_slow_analysis_never_blocks_unrelated_event_or_repeats_same_burst():
    release = threading.Event(); entered = threading.Event(); stop = threading.Event(); retained = []
    def recognize(snapshot):
        entered.set(); release.wait(2)
        raise RuntimeError('synthetic unavailable')
    wrapper, snaps = source(faces=SimpleNamespace(analyze=recognize))
    unrelated = SensoryEvent('home_assistant', 'home-assistant:other', 'state_changed', event().occurred_at,
        {'entity_id': 'binary_sensor.some_door', 'old_state': 'off', 'new_state': 'on'})
    def listen(stop, handler):
        handler(event()); assert entered.wait(1)
        handler(unrelated); handler(event())
        assert unrelated in retained and len(snaps) == 1
        release.set()
        stop.wait(0.3)
    wrapper._source = SimpleNamespace(listen=listen)
    wrapper.listen(stop, retained.append)
    assert len(snaps) == 1
    assert any('camera_observation' in e.payload for e in retained)


def test_image_evidence_survives_newer_binary_reset_in_household_selection():
    from eva.adapters.postgres.sensory_events import _household_observation_selection_key
    wrapper, _ = source(vision=SimpleNamespace(analyze=lambda _: SimpleNamespace(payload={
        'description': 'Synthetic scene', 'model': 'fixture', 'model_revision': 'test',
        'analyzed_at': datetime.now(UTC).isoformat()})))
    image = wrapper.enrich(event(), wrapper._inventory.devices[0], stop=threading.Event())
    reset = SensoryEvent('home_assistant', 'home-assistant:reset', 'state_changed',
        image.occurred_at+timedelta(seconds=1), {**event().payload, 'old_state': 'on', 'new_state': 'off'})
    keys = set(); selected = []
    for candidate in (reset, image):
        key = _household_observation_selection_key(candidate)
        if key not in keys:
            keys.add(key); selected.append(candidate)
    assert selected == [reset, image]
    shared = binding().observation_event(image)
    assert shared.payload['observation']['occurred_at'] == image.occurred_at.isoformat()


def test_duplicate_source_during_analysis_is_retained_only_once():
    stop = threading.Event(); retained = []
    wrapper, snaps = source()
    original = event()
    def listen(stop, handler):
        handler(original); handler(original)
        stop.wait(0.3)
    wrapper._source = SimpleNamespace(listen=listen)
    wrapper.listen(stop, retained.append)
    assert len(retained) == len(snaps) == 1
