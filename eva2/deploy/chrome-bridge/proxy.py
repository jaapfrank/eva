"""Internal Docker HTTP endpoint forwards only MCP to the host's Unix socket."""
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from pathlib import Path
TOKEN = Path('/run/secrets/chrome-token').read_text().strip()

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
        self.sock.settimeout(130)
        self.sock.connect('/bridge/mcp.sock')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.client_address[0] != '172.30.0.2':
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
        try:
            connection.request('POST', '/mcp', self.rfile.read(size), {
                'Authorization': 'Bearer ' + TOKEN,
                'Content-Type': 'application/json',
            })
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
            connection.close()

ThreadingHTTPServer(('0.0.0.0', 45164), Handler).serve_forever()
