"""Owner presentation preserves streaming negotiation without accepting upstream auth."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from fastapi import FastAPI
from starlette.requests import Request
from eva.adapters.api.installer_runtime_proxy import register_installer_runtime_proxy


def test_proxy_keeps_ndjson_accept_and_uses_only_its_bound_owner_credential():
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.headers.get("Accept"), self.headers.get("Authorization"), self.path))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("X-Eva-Input-Receipt", "Message received")
            self.send_header("Content-Security-Policy", "default-src 'self'")
            self.send_header("Set-Cookie", "upstream-session=not-for-the-browser")
            self.end_headers()
            self.wfile.write(b'{"type":"answer","text":"real upstream response"}\n')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = FastAPI()
    checks = []
    register_installer_runtime_proxy(app, access=lambda: (f"http://127.0.0.1:{server.server_port}", "bound-token"),
        require_read=lambda request: checks.append("read"), require_write=lambda request: checks.append("write"))
    endpoint = next(route.endpoint for route in app.routes if route.path == "/eva/{path:path}")
    async def run():
        async def receive():
            return {"type": "http.request", "body": b'{}', "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/eva/internal/v1/conversation",
            "raw_path": b"/eva/internal/v1/conversation", "query_string": b"",
            "scheme": "http", "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1234),
            "headers": [(b"accept", b"application/x-ndjson"), (b"authorization", b"Bearer browser-token")]}, receive)
        response = await endpoint(request, "internal/v1/conversation")
        return b"".join([part async for part in response.body_iterator]), response.headers
    try:
        body, headers = asyncio.run(run())
        assert b"real upstream response" in body
        assert headers["X-Eva-Input-Receipt"] == "Message received"
        assert headers["Content-Security-Policy"] == "default-src 'self'"
        assert "Set-Cookie" not in headers
        assert observed == [("application/x-ndjson", "Bearer bound-token", "/internal/v1/conversation")]
        assert checks == ["write"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
