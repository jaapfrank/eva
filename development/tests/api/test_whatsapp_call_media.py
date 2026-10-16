"""Exercise the existing voice-lease protocol without a model or real caller."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from eva.whatsapp_service.server import CallSettings, VoiceLease, create_app


class LeaseProtocol(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'owner'
        self.path.write_text('w' * 48); self.path.chmod(0o600)
        self.settings = CallSettings(str(self.path), str(self.path), 'http://127.0.0.1:44710',
            'http://127.0.0.1:44716', 'http://localhost:44716', 'ws://127.0.0.1:7880')

    def tearDown(self):
        self.directory.cleanup()

    async def test_existing_leased_voice_open_renew_and_specific_close(self):
        requests = []
        room = 'eva-voice-' + 'a' * 32
        def handle(request):
            requests.append(request)
            if request.url.path.endswith('/authorize'):
                self.assertEqual(json.loads(request.content)['contact_phone'], '31612345678')
                return httpx.Response(200, json={'authorized': True})
            self.assertEqual(request.headers['origin'], 'http://localhost:44716')
            return httpx.Response(200, json={'room': room, 'token': 'room-only-test-token', 'renewed': True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            lease = VoiceLease(self.settings, '31612345678', 'CALL123', client=client)
            await lease.open(); await lease.renew(); await lease.close()
        self.assertEqual([r.url.path for r in requests], ['/internal/v1/whatsapp/authorize',
            '/session/leased', '/internal/v1/whatsapp/authorize', '/internal/v1/whatsapp/authorize',
            '/session/renew/' + room, '/session/end/' + room])

    async def test_current_authority_or_busy_voice_never_gets_a_second_session(self):
        requests = []
        def handle(request):
            requests.append(request.url.path)
            return httpx.Response(403 if request.url.path.endswith('/authorize') else 409, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            lease = VoiceLease(self.settings, '31612345678', 'CALL123', client=client)
            with self.assertRaises(httpx.HTTPStatusError): await lease.open()
            await lease.close()
        self.assertEqual(requests, ['/internal/v1/whatsapp/authorize'])

    async def test_token_rotation_is_checked_without_contacting_another_route(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail('request after rotation'))) as client:
            lease = VoiceLease(self.settings, '31612345678', 'CALL123', client=client)
            self.path.write_text('z' * 48)
            with self.assertRaises(ValueError): await lease.authorize()

    def test_external_or_credential_bearing_urls_are_not_validated_as_existing_voice(self):
        for url in ['http://example.com', 'http://user:secret@127.0.0.1:44716', 'http://127.0.0.1:44716/path']:
            with self.assertRaises(ValueError):
                CallSettings(str(self.path), str(self.path), url, self.settings.voice_url,
                    self.settings.voice_origin, self.settings.livekit_url)

    def test_websocket_owner_gate_and_closed_protocol(self):
        settings = self.settings
        class Audio:
            def __init__(self, socket, lease): self.socket = socket
            async def run(self):
                await self.socket.send_json({'type': 'ready'})
        app = create_app(settings, lease_factory=lambda *args: None, audio_factory=Audio)
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/call-audio', headers={'x-eva-contact': '31612345678', 'x-eva-call-id': 'CALL123'}): pass
            with client.websocket_connect('/call-audio', headers={'authorization': 'Bearer ' + 'w' * 48,
                'x-eva-contact': '31612345678', 'x-eva-call-id': 'CALL123'}) as socket:
                self.assertEqual(socket.receive_json(), {'type': 'ready'})
            self.assertEqual(client.get('/healthz').json()['active_calls'], 0)


if __name__ == '__main__':
    unittest.main()
