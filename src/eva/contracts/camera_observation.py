"""Shared camera evidence is bounded interpretation, never identity authority."""
from copy import deepcopy
from datetime import datetime
import math
import re

from eva.contracts.canonical_json import canonical_json_bytes


def normalize_camera_observation(value: dict) -> dict:
    allowed = {'contract', 'status', 'camera_entity_id', 'device_id', 'discovery_revision',
               'alternate_camera_count', 'captured_at', 'retrieved_at', 'image_sha256',
               'interpretation_not_verified_fact', 'identity_is_not_authentication',
               'scene', 'recognition', 'snapshot_error', 'vision_error', 'recognition_error', 'integration', 'area_id'}
    if (not isinstance(value, dict) or set(value)-allowed or value.get('contract') != 'EvaCameraObservation/v1'
            or value.get('status') not in {'unavailable', 'stale_trigger', 'observed', 'analysis_unavailable'}
            or value.get('interpretation_not_verified_fact') is not True
            or value.get('identity_is_not_authentication') is not True or value.get('captured_at') is not None
            or not re.fullmatch(r'camera\.[a-z0-9_]{1,200}', str(value.get('camera_entity_id')))
            or not isinstance(value.get('device_id'), str) or not 1 <= len(value['device_id']) <= 200
            or type(value.get('alternate_camera_count')) is not int or not 0 <= value['alternate_camera_count'] <= 1000):
        raise ValueError('camera observation envelope is invalid')
    for key in ('discovery_revision', 'image_sha256'):
        if (key == 'discovery_revision' or key in value) and not re.fullmatch(r'[0-9a-f]{64}', str(value.get(key))):
            raise ValueError('camera observation provenance is invalid')
    def timestamp(text):
        if not isinstance(text, str) or len(text) > 60 or datetime.fromisoformat(text).tzinfo is None:
            raise ValueError('camera observation timestamp is invalid')
    if 'retrieved_at' in value:
        timestamp(value['retrieved_at'])
    for key in ('integration', 'area_id'):
        if key in value and (not isinstance(value[key], str) or not 1 <= len(value[key]) <= 200
                             or any(ord(c) < 32 for c in value[key])):
            raise ValueError('camera source registry metadata is invalid')
    for key in ('snapshot_error', 'vision_error', 'recognition_error'):
        if key in value and (not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_]{1,80}', value[key])):
            raise ValueError('camera observation failure metadata is invalid')
    if 'scene' in value:
        scene = value['scene']
        if not isinstance(scene, dict) or set(scene) != {'description', 'model', 'model_revision', 'analyzed_at'}:
            raise ValueError('camera scene is invalid')
        for key, bound in (('description', 1500), ('model', 200), ('model_revision', 200)):
            if not isinstance(scene[key], str) or not scene[key].strip() or len(scene[key]) > bound:
                raise ValueError('camera scene text is invalid')
        timestamp(scene['analyzed_at'])
    if 'recognition' in value:
        result = value['recognition']
        if (not isinstance(result, dict) or set(result) != {'contract', 'image_sha256', 'analyzed_at', 'backend',
                'backend_revision', 'profile_revision', 'evidence_kind', 'faces'}
                or result['contract'] != 'EvaLocalFaceObservation/v1' or result['image_sha256'] != value.get('image_sha256')
                or result['backend'] != 'arcface' or result['evidence_kind'] != 'face_similarity_candidate_not_authentication'
                or not isinstance(result['backend_revision'], str) or not 1 <= len(result['backend_revision']) <= 200
                or not re.fullmatch('[0-9a-f]{64}', str(result['profile_revision']))
                or not isinstance(result['faces'], list) or len(result['faces']) > 8):
            raise ValueError('camera recognition is invalid')
        timestamp(result['analyzed_at'])
        for face in result['faces']:
            if (not isinstance(face, dict) or set(face)-{'region', 'status', 'person_label', 'similarity_ppm', 'margin_ppm'}
                    or face.get('status') not in {'known_candidate', 'unknown'}
                    or not isinstance(face.get('region'), list) or len(face['region']) != 4
                    or any(type(v) is not int or not 0 <= v <= 100000 for v in face['region'])):
                raise ValueError('camera face evidence is invalid')
            if face['status'] == 'known_candidate':
                label = face.get('person_label')
                if not isinstance(label, str) or not label.strip() or len(label) > 120:
                    raise ValueError('camera identity label is invalid')
            elif 'person_label' in face:
                raise ValueError('unrecognized face cannot carry a candidate label')
            for key in ('similarity_ppm', 'margin_ppm'):
                if key in face and (type(face[key]) is not int or not -2000000 <= face[key] <= 2000000):
                    raise ValueError('camera identity score is invalid')
    if len(canonical_json_bytes(value)) > 4500:
        raise ValueError('camera observation exceeds shared evidence bound')
    return deepcopy(value)
