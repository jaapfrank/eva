"""Exercise the actual nginx ingress in an isolated Linux network namespace.

Usage: sudo unshare --net python3 tools/verify_household_voice_ingress.py
       --nginx /absolute/path/to/nginx
No production ports, credentials, model calls or household audio are used.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

from render_household_voice_ingress import render


def verify(binary: Path) -> dict:
    # Refuse to bind the retained-slot port in the host's network namespace.
    if Path('/proc/self/ns/net').readlink() == Path('/proc/1/ns/net').readlink():
        raise RuntimeError('run this verifier through unshare --net')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    observations = []
    disconnected = threading.Event()
    pending = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            observations.append((self.path, dict(self.headers), body))
            if self.path.endswith('/interrupt'):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            elif body == b'pending':
                pending.set()
                self.connection.settimeout(4)
                if self.connection.recv(1) == b'':
                    disconnected.set()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"reply":"probe"}')

        def log_message(self, *_):
            pass

    results = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        results.append(name)

    server = ThreadingHTTPServer(('127.0.0.1', 44710), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    child = None
    try:
        with tempfile.TemporaryDirectory(prefix='eva2-voice-ingress-') as work:
            directory = Path(work)
            for name in ('body', 'proxy'):
                (directory / name).mkdir()
            configuration = render(environment='EVA2_CANARY_URL=http://127.0.0.1:44710',
                                   listen='127.0.0.1', home_assistant='127.0.0.1',
                                   runtime=work)
            config = directory / 'nginx.conf'
            config.write_text(configuration)
            syntax = subprocess.run([str(binary), '-t', '-c', str(config), '-e', 'stderr'],
                                    capture_output=True, text=True)
            if syntax.returncode:
                raise RuntimeError(syntax.stderr)
            check('nginx_configuration_valid', True)
            child = subprocess.Popen([str(binary), '-c', str(config), '-e', 'stderr'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1', 44714), timeout=.1):
                        break
                except OSError:
                    if child.poll() is not None:
                        raise RuntimeError('nginx failed to start')
                    time.sleep(.02)
            else:
                raise RuntimeError('nginx did not listen')

            def request(path, *, method='POST', body=b'{}', source='127.0.0.1'):
                connection = http.client.HTTPConnection('127.0.0.1', 44714, timeout=4,
                                                       source_address=(source, 0))
                try:
                    connection.request(method, path, body=body, headers={
                        'Authorization': 'Bearer verifier-only',
                        'Cookie': 'private=must-not-forward',
                        'X-Eva-Principal': 'must-not-forward',
                    })
                    response = connection.getresponse()
                    response.read()
                    return response.status
                finally:
                    connection.close()

            route = '/internal/v1/ha-voice/turns'
            check('allowed_turn_reaches_upstream', request(route) == 200)
            headers = {key.lower(): value for key, value in observations[-1][1].items()}
            check('only_voice_credential_forwarded', headers.get('authorization') ==
                  'Bearer verifier-only' and 'cookie' not in headers and
                  'x-eva-principal' not in headers)
            check('exact_interrupt_reaches_upstream',
                  request(route + '/' + 'a' * 32 + '/interrupt') == 200)
            before = len(observations)
            check('other_network_peer_denied', request(route, source='127.0.0.2') == 403)
            check('dashboard_not_exposed', request('/internal/v1/dashboard') == 404)
            check('arbitrary_core_route_not_exposed', request('/health/ready') == 404)
            check('invalid_interrupt_not_exposed', request(route + '/wrong/interrupt') == 404)
            check('query_not_forwarded', request(route + '?secret=no') == 400)
            check('method_not_forwarded', request(route, method='GET') == 405)
            check('oversized_body_rejected', request(route, body=b'x' * 131073) == 413)
            check('denied_requests_never_reached_core', len(observations) == before)
            connection = socket.create_connection(('127.0.0.1', 44714))
            connection.sendall(('POST ' + route + ' HTTP/1.1\r\nHost: localhost\r\n'
                                'Content-Length: 7\r\n\r\npending').encode())
            check('pending_request_reached_core', pending.wait(3))
            connection.close()
            check('disconnect_closes_core_request', disconnected.wait(3))
            check('next_question_still_works', request(route) == 200)
            check('no_transcript_spilled_to_disk', not list((directory / 'body').iterdir())
                  and not list((directory / 'proxy').iterdir()))
            child.terminate()
            child.wait(timeout=5)
            child = None
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
        server.shutdown()
        server.server_close()
    for environment in ('', 'EVA2_CANARY_URL=http://example.com',
                        'EVA2_CANARY_URL=http://127.0.0.1:44710 EVA2_CANARY_URL=http://127.0.0.1:44711'):
        try:
            render(environment=environment, listen='127.0.0.1', home_assistant='127.0.0.1')
        except ValueError:
            continue
        raise AssertionError('ambiguous or untrusted route admitted')
    check('ambiguous_and_nonlocal_routes_rejected', True)
    alternate = render(environment='EVA2_CANARY_URL=http://127.0.0.1:44711',
                       listen='127.0.0.1', home_assistant='127.0.0.1')
    check('other_retained_slot_rendered', 'proxy_pass http://127.0.0.1:44711;' in alternate)
    return {'status': 'passed', 'checks': results, 'passed': len(results),
            'nginxSha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'boundary': 'Actual nginx and HTTP in isolated network; no HA audio or model.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx', required=True, type=Path)
    print(json.dumps(verify(parser.parse_args().nginx), indent=2))
