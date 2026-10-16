from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_whatsapp import install_dashboard_whatsapp, whatsapp_turn
from eva.application.voice import SpeechAudioChunk

TOKEN = 'w' * 48
PHONE = '31612345678'


class Control:
    def __init__(self):
        self.value = dict(contract='EvaWhatsApp/v1', enabled=True, connection='connected',
            contact_phone=PHONE, qr_data_url=None, audio_enabled=True, last_error=None)
    def call(self, operation='status', payload=None):
        if operation == 'disable': self.value.update(enabled=False, connection='disabled')
        if operation == 'configure': self.value.update(payload)
        return self.value | {'secret': 'not-forwarded'}
    def require_contact(self, phone):
        if self.value['contact_phone'] != phone or not self.value['enabled']:
            raise ValueError('not current')
        return self.value


class Runtime:
    _runtime_tenant_id = 'tenant-test'
    _runtime_installation_id = 'installation-test'
    _dashboard_owner_principal_id = UUID('00000000-0000-4000-8000-000000000001')
    _dashboard_principal_binding_id = UUID('00000000-0000-4000-8000-000000000002')
    def __init__(self):
        self.turns = []; self.after = []
        self._delegated_work_owner_service = SimpleNamespace(validate_authority=lambda turn: None)
    def admit_turn_audience(self, turn): self.turns.append(turn)
    def respond(self, turn): return SimpleNamespace(text='Hoi!')
    def after_response(self, turn, reply): self.after.append((turn, reply))
    def private_work_return(self, turn, operation, **kwargs):
        self.return_turn = turn
        return {'status': 'idle', 'result': None}


class Media:
    def __init__(self): self.speech_input = self; self.speech_output = self; self.audio = None
    def issue_session(self): return object()
    def check(self, session): pass
    async def transcribe(self, chunks):
        self.audio = b''.join([chunk async for chunk in chunks]); return 'Hallo Eva'
    async def synthesize(self, text):
        yield SpeechAudioChunk(b'OggSfixture', 'audio/ogg')


@pytest.fixture
def setup():
    runtime, control, media = Runtime(), Control(), Media()
    app = FastAPI()
    install_dashboard_whatsapp(app, access=DashboardAccess(TOKEN), runtime=runtime, media=media, control=control)
    return TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}), runtime, control, media


def message(**changes):
    return dict(contact_phone=PHONE, message_id='ABCD', text='Hallo',
        occurred_at=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')) | changes


def test_real_routes_use_existing_owner_runtime_and_exact_origin(setup):
    client, runtime, _, _ = setup
    assert client.post('/internal/v1/whatsapp/respond', json=message()).json() == {
        'text': 'Hoi!', 'conversation_id': 'whatsapp:' + PHONE, 'message_id': 'whatsapp:' + PHONE + ':ABCD'}
    turn = runtime.turns[0]
    assert turn.principal_id == str(runtime._dashboard_owner_principal_id)
    assert turn.audience.kind == 'private_person' and runtime.after[0][0] == turn
    assert client.post('/internal/v1/whatsapp/return/poll', json={'contact_phone': PHONE}).json()['status'] == 'idle'
    assert runtime.return_turn.conversation_id == turn.conversation_id


@pytest.mark.parametrize('changes', [{'contact_phone': '31600000000'}, {'message_id': '../escape'}, {'principal_id': 'other'}, {'text': ''}, {'occurred_at': '2020-01-01T00:00:00Z'}])
def test_wrong_contact_identity_and_unbound_messages_rejected(setup, changes):
    client, runtime, _, _ = setup
    assert client.post('/internal/v1/whatsapp/respond', json=message(**changes)).status_code == 422
    assert runtime.turns == []


def test_disable_blocks_ingress_return_and_delivery(setup):
    client, runtime, _, _ = setup
    assert client.post('/internal/v1/dashboard/whatsapp/disable', json={}).status_code == 200
    assert client.post('/internal/v1/whatsapp/respond', json=message()).status_code == 422
    assert client.post('/internal/v1/whatsapp/authorize', json={'contact_phone': PHONE, 'message_id': 'ABCD'}).status_code == 403
    assert client.post('/internal/v1/whatsapp/return/poll', json={'contact_phone': PHONE}).status_code == 403
    assert runtime.turns == []


def test_existing_media_ports_receive_pcm_and_return_encoded_audio(setup):
    client, runtime, _, media = setup
    assert client.post('/internal/v1/whatsapp/transcribe', content=b'\x00\x00' * 200,
        headers={'content-type': 'audio/pcm', 'x-eva-contact': PHONE}).json() == {'text': 'Hallo Eva'}
    assert media.audio == b'\x00\x00' * 200 and runtime.turns == []
    response = client.post('/internal/v1/whatsapp/synthesize', json={'contact_phone': PHONE, 'text': 'Hoi'})
    assert response.content == b'OggSfixture' and response.headers['content-type'] == 'audio/ogg'


def test_media_is_optional_and_audio_is_bounded(setup):
    client, _, control, _ = setup
    control.value['audio_enabled'] = False
    assert client.post('/internal/v1/whatsapp/transcribe', content=b'12',
        headers={'content-type': 'audio/pcm', 'x-eva-contact': PHONE}).status_code == 503
    control.value['audio_enabled'] = True
    assert client.post('/internal/v1/whatsapp/transcribe', content=b'123',
        headers={'content-type': 'audio/pcm', 'x-eva-contact': PHONE}).status_code == 422


def test_owner_gate_status_and_closed_projection(setup):
    client, _, _, _ = setup
    response = client.get('/internal/v1/dashboard/whatsapp')
    assert response.status_code == 200 and 'secret' not in response.json()
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/internal/v1/dashboard/whatsapp', headers={'Authorization': ''}).status_code == 401
    assert client.get('/internal/v1/dashboard/whatsapp?owner=other').status_code == 403


def test_revocation_after_stt_discards_transcript(setup):
    client, _, control, media = setup
    original = media.transcribe
    async def revoked(chunks):
        result = await original(chunks); control.value['enabled'] = False; return result
    media.transcribe = revoked
    response = client.post('/internal/v1/whatsapp/transcribe', content=b'12',
        headers={'content-type': 'audio/pcm', 'x-eva-contact': PHONE})
    assert response.status_code == 503 and 'Hallo Eva' not in response.text


def test_owner_revoked_during_settings_body_never_dispatches(setup, monkeypatch):
    from eva.adapters.api import dashboard_whatsapp as module
    client, runtime, control, _ = setup
    original = module.read_small_json_request
    async def revoked(*args):
        value = await original(*args)
        def deny(turn): raise ValueError('revoked')
        runtime._delegated_work_owner_service.validate_authority = deny
        return value
    monkeypatch.setattr(module, 'read_small_json_request', revoked)
    assert client.post('/internal/v1/dashboard/whatsapp/disable', json={}).status_code == 403
    assert control.value['enabled'] is True


def test_audio_disabled_during_upload_never_starts_stt(setup):
    client, _, control, media = setup
    def body():
        control.value['audio_enabled'] = False
        yield b'12'
    response = client.post('/internal/v1/whatsapp/transcribe', content=body(),
        headers={'content-type': 'audio/pcm', 'x-eva-contact': PHONE})
    assert response.status_code == 503 and media.audio is None
