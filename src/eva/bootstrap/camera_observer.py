"""Compose an opt-in camera adapter around the installed sensory source."""
import os

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.connectors.camera_observer import CameraObservationSource, HomeAssistantCameraDiscovery
from eva.adapters.connectors.home_assistant_camera import HomeAssistantCameraSource
from eva.adapters.connectors.local_vision import LocalVisionHttpAnalyzer


def camera_observer_source(source):
    enabled = os.environ.get('EVA_CAMERA_OBSERVER_ENABLED', 'false').strip().lower()
    if enabled not in {'true', 'false'}:
        raise ValueError('EVA_CAMERA_OBSERVER_ENABLED must be true or false')
    if enabled == 'false' or source is None:
        return source
    base_url = os.environ['EVA_HOME_ASSISTANT_URL']
    token = read_runtime_secret_file(os.environ['EVA_HOME_ASSISTANT_TOKEN_FILE'], setting_name='camera Home Assistant token').strip()
    vision = None
    scenes_enabled = os.environ.get('EVA_CAMERA_OBSERVER_SCENE_ENABLED', 'true').strip().lower()
    if scenes_enabled not in {'true', 'false'}:
        raise ValueError('EVA_CAMERA_OBSERVER_SCENE_ENABLED must be true or false')
    if scenes_enabled == 'true' and os.environ.get('EVA_LOCAL_VISION_URL'):
        vision = LocalVisionHttpAnalyzer(endpoint=os.environ['EVA_LOCAL_VISION_URL'],
            model=os.environ['EVA_LOCAL_VISION_MODEL'], revision=os.environ['EVA_LOCAL_VISION_REVISION'])
    faces = None
    if os.environ.get('EVA_LOCAL_FACE_URL'):
        from eva.adapters.connectors.local_face_identity import LocalFaceIdentityHttpAnalyzer
        credential = read_runtime_secret_file(os.environ['EVA_LOCAL_FACE_TOKEN_FILE'], setting_name='local face bridge token').strip()
        faces = LocalFaceIdentityHttpAnalyzer(endpoint=os.environ['EVA_LOCAL_FACE_URL'], token=credential,
                                              revision=os.environ['EVA_LOCAL_FACE_REVISION'])
    if vision is None and faces is None:
        raise ValueError('camera observer requires a configured local image analyzer')
    return CameraObservationSource(source, discovery=HomeAssistantCameraDiscovery(base_url=base_url, token=token),
        snapshots=HomeAssistantCameraSource(base_url=base_url, token=token), vision=vision, faces=faces)
