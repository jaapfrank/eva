"""Loopback-only proxy fixture; no Docker, production socket or credentials."""
import importlib.util,json,pathlib,socket,sys
spec=importlib.util.spec_from_file_location('chrome_proxy',pathlib.Path(__file__).with_name('proxy.py'))
proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)
config=json.loads(pathlib.Path(sys.argv[1]).read_text())
proxy.TOKEN=pathlib.Path(config['tokenFile']).read_text().strip()
class FixtureUnix(proxy.UnixConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
  self.sock.settimeout(10)
  self.sock.connect(config['mcpSocket'])
proxy.UnixConnection=FixtureUnix
original=proxy.gateway_peer_allowed
def authorized(peer):
 address=pathlib.Path(config['addressFile']).read_text().strip()
 caller=pathlib.Path(config['callerAddressFile']).read_text().strip() if config.get('callerAddressFile') else '172.30.0.2'
 return peer=='127.0.0.1' and original(caller,lambda *args:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,0))])
proxy.gateway_peer_allowed=authorized
server=proxy.ThreadingHTTPServer(('127.0.0.1',0),proxy.Handler)
server.handle_error=lambda *args:None
print(json.dumps({'port':server.server_address[1]}),flush=True)
server.serve_forever()
