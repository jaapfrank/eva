"""Internal Docker HTTP endpoint forwards only MCP to the host's Unix socket."""
import http.client
import json
import ipaddress
import select
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from pathlib import Path
TOKEN = None

def rejection_reason(result):
    """Keep useful diagnostics without logging page text or relay credentials."""
    text = '\n'.join(item.get('text', '') for item in result.get('content', [])
                     if isinstance(item, dict) and isinstance(item.get('text', ''), str)).lower()
    for pattern, reason in [
        ('no browser control', 'owner_control_or_disconnected'),
        ('another browser action', 'concurrent_action_blocked'),
        ('fresh chrome_snapshot required', 'stale_observation'),
        ('worker stopped', 'worker_interrupted'),
        ('action interrupted', 'owner_interrupted'),
        ('not found in the current page snapshot', 'stale_element_reference'),
        ('intercepts pointer events', 'element_obstructed'),
        ('timeout', 'action_timeout'),
        ('timed out', 'action_timeout'),
        ('chrome action failed', 'worker_action_failed'),
    ]:
        if pattern in text:
            return reason
    return 'upstream_rejected'

def normalize_tool_output(body):
    """Preserve MCP blocks and expose plain text to Eva's object-result adapter."""
    envelope = json.loads(body)
    result = envelope.get('result')
    if isinstance(result, dict) and result.get('isError'):
        print(json.dumps({'event': 'chrome_tool_rejected', 'reason': rejection_reason(result)}), flush=True)
    if isinstance(result, dict) and not result.get('isError') and 'structuredContent' not in result:
        content = result.get('content')
        if isinstance(content, list) and content and all(
            isinstance(item, dict) and item.get('type') == 'text'
            and isinstance(item.get('text'), str) for item in content
        ):
            result['structuredContent'] = {'text': '\n\n'.join(item['text'] for item in content)}
    return json.dumps(envelope, ensure_ascii=False).encode('utf-8')

class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout if isinstance(self.timeout, (int, float)) else 130)
        self.sock.connect('/bridge/mcp.sock')

def gateway_peer_allowed(peer, resolver=None):
    """Resolve only the named gateway on the existing Docker network; fail closed."""
    try:
        records = (resolver or socket.getaddrinfo)(
            'eva2-mcp-gateway', None, socket.AF_INET, socket.SOCK_STREAM)
        addresses = {record[4][0] for record in records}
        if len(addresses) != 1:
            return False
        address = ipaddress.ip_address(next(iter(addresses)))
        return (address.is_private and not address.is_loopback
                and not address.is_link_local and not address.is_multicast
                and not address.is_unspecified and str(address) == peer)
    except (OSError, ValueError, IndexError, TypeError):
        return False

def watch_forwarding(client, upstream, peer, done, authorize=None, interval=0.25):
    """Revoke an in-flight host request when its authorized caller disappears."""
    authorize = authorize or gateway_peer_allowed
    while not done.wait(interval):
        try:
            readable, _, _ = select.select([client], [], [], 0)
            lost = bool(readable) and client.recv(1, socket.MSG_PEEK) == b''
            if not lost and authorize(peer):
                continue
        except OSError:
            pass
        if done.is_set():
            return
        # Shutdown wakes the handler's blocking read and the host's close listener.
        # No cancellation is sent to a later request or a newly granted epoch.
        try:
            upstream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != '/healthz' or self.client_address[0] != '127.0.0.1':
            self.send_error(403)
            return
        connection = UnixConnection('localhost', timeout=2)
        healthy = False
        try:
            request = {'jsonrpc': '2.0', 'id': 'health', 'method': 'tools/list', 'params': {}}
            connection.request('POST', '/mcp', json.dumps(request), {
                'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            value = json.loads(raw) if len(raw) <= 65536 else {}
            tools = value.get('result', {}).get('tools', [])
            healthy = response.status == 200 and any(
                isinstance(tool, dict) and tool.get('name') == 'chrome_snapshot' for tool in tools)
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        body = json.dumps({'status': 'ok' if healthy else 'unavailable'}).encode()
        self.send_response(200 if healthy else 503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not gateway_peer_allowed(self.client_address[0]):
            self.send_error(403)
            return
        if self.path != '/mcp':
            self.send_error(404)
            return
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 65536:
            self.send_error(413)
            return
        connection = UnixConnection('localhost')
        done = threading.Event()
        watcher = None
        try:
            connection.request('POST', '/mcp', self.rfile.read(size), {
                'Authorization': 'Bearer ' + TOKEN,
                'Content-Type': 'application/json',
            })
            watcher = threading.Thread(target=watch_forwarding, args=(
                self.connection, connection.sock, self.client_address[0], done), daemon=True)
            watcher.start()
            response = connection.getresponse()
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('Response too large')
            if response.status == 200:
                body = normalize_tool_output(body)
            self.send_response(response.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ValueError, http.client.HTTPException):
            self.send_error(503)
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=1)
            connection.close()

if __name__ == '__main__':
    TOKEN = Path('/run/secrets/chrome-token').read_text().strip()
    ThreadingHTTPServer(('0.0.0.0', 45164), Handler).serve_forever()
