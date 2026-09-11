"""Internal Docker HTTP endpoint forwards only MCP to the host's Unix socket."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from pathlib import Path
TOKEN = Path('/run/secrets/chrome-token').read_text().strip()

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
