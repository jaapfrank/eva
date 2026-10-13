"""Exercise real multipart upload, original reply binding and file readback."""
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from threading import Thread

import pytest

from eva.adapters.connectors.telegram import TelegramProactiveDelivery


@pytest.mark.parametrize('damage', [None, 'content', 'chat', 'reply', 'path'])
def test_saved_document_preserves_utf8_bytes_and_never_retries(damage):
    content = ('# Volledig voorstel\n\nBron https://example.test 😀\r\n' * 180 + '\n').encode()
    sends = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            if self.path.endswith('/sendDocument'):
                parsed = BytesParser(policy=default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                parts = {p.get_param('name', header='Content-Disposition'): p for p in parsed.iter_parts()}
                uploaded = parts['document'].get_payload(decode=True)
                sends.append(uploaded)
                assert uploaded == content
                assert json.loads(parts['reply_parameters'].get_payload(decode=True)) == {
                    'message_id': 123, 'allow_sending_without_reply': False}
                result = {'message_id': 456, 'chat': {'id': 43 if damage == 'chat' else 42, 'type': 'private'},
                    'caption': parts['caption'].get_payload(decode=True).decode(),
                    'reply_to_message': {'message_id': 124 if damage == 'reply' else 123},
                    'document': {'file_name': 'eva-volledig-resultaat.md', 'file_size': len(content), 'file_id': 'fixture'}}
            else:
                assert self.path.endswith('/getFile') and json.loads(body) == {'file_id': 'fixture'}
                result = {'file_id': 'fixture', 'file_size': len(content),
                    'file_path': '../secret.md' if damage == 'path' else 'documents/file_1.md'}
            self.respond(json.dumps({'ok': True, 'result': result}).encode())

        def do_GET(self):
            assert self.path == '/file/bot123456:fixture/documents/file_1.md'
            self.respond((b'X' + content[1:]) if damage == 'content' else content)

        def respond(self, raw):
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        delivery = TelegramProactiveDelivery(bot_token='123456:fixture', chat_id='42',
            api_base_url=f'http://127.0.0.1:{server.server_port}')
        if damage:
            with pytest.raises(RuntimeError):
                delivery.deliver_document(content.decode(), expected_chat_id='42', reply_to_message_id=123)
        else:
            assert delivery.deliver_document(content.decode(), expected_chat_id='42', reply_to_message_id=123) == (
                'telegram:42:456:sha256:' + hashlib.sha256(content).hexdigest())
        assert sends == [content]
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
