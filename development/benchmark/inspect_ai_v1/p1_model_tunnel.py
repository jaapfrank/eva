"""Model-only CONNECT relay for an internal comparison network.

Native TLS/authentication stays end to end. This is sample isolation assistance,
not an Eva feature or a replacement model provider.
"""
import ipaddress
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ModelTunnel:
    def __init__(self, address, client_network):
        self.network = ipaddress.ip_network(client_network)
        self.connections = set()
        self.events = []
        self.lock = threading.Lock()
        self.stopped = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                if (ipaddress.ip_address(self.client_address[0]) not in owner.network
                        or self.path != "chatgpt.com:443"):
                    self.send_error(403)
                    return
                upstream = None
                with owner.lock:
                    if owner.stopped:
                        self.send_error(503)
                        return
                    owner.connections.add(self.connection)
                try:
                    upstream = socket.create_connection(("chatgpt.com", 443), timeout=10)
                    with owner.lock:
                        if owner.stopped:
                            return
                        owner.connections.add(upstream)
                        owner.events.append({"endpoint": "chatgpt.com:443", "connected": True})
                    self.send_response(200, "Connection Established")
                    self.end_headers()
                    self.wfile.flush()
                    peers = [self.connection, upstream]
                    while not owner.stopped:
                        readable, _, _ = select.select(peers, [], [], 0.2)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = upstream if source is self.connection else self.connection
                            target.sendall(data)
                except (OSError, ValueError) as error:
                    with owner.lock:
                        owner.events.append({"error": type(error).__name__})
                finally:
                    if upstream is not None:
                        upstream.close()
                    with owner.lock:
                        owner.connections.discard(self.connection)
                        owner.connections.discard(upstream)
                    self.close_connection = True

        class Server(ThreadingHTTPServer):
            daemon_threads = False
            block_on_close = True

            def get_request(self):
                connection, address = super().get_request()
                with owner.lock:
                    owner.connections.add(connection)
                return connection, address

            def close_request(self, connection):
                super().close_request(connection)
                with owner.lock:
                    owner.connections.discard(connection)

        self.server = Server(address, Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self):
        with self.lock:
            self.stopped = True
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        return {"closed": all(c.fileno() == -1 for c in connections),
                "events": list(self.events)}
