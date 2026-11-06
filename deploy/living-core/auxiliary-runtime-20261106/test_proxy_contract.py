import importlib.util,unittest,socket,threading,tempfile,json,http.client,contextlib,io
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler
from socketserver import UnixStreamServer
s=importlib.util.spec_from_file_location('actual_proxy','/actual-proxy.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Contract(unittest.TestCase):
 def test_gateway_unique_private_exact(self):
  def records(addresses):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,0))for a in addresses]
  for addresses,peer,expected in [(['172.30.0.4'],'172.30.0.4',True),(['172.30.0.4'],'172.30.0.5',False),(['172.30.0.4','172.30.0.5'],'172.30.0.4',False),(['8.8.8.8'],'8.8.8.8',False),(['127.0.0.1'],'127.0.0.1',False),(['169.254.1.1'],'169.254.1.1',False),([],'172.30.0.4',False)]:self.assertEqual(m.gateway_peer_allowed(peer,lambda *a:records(addresses)),expected)
 def test_gateway_dns_failure(self):
  def fail(*a):raise OSError('synthetic')
  self.assertFalse(m.gateway_peer_allowed('172.30.0.4',fail))
 def test_text_normalization_and_structured_preservation(self):
  base={'jsonrpc':'2.0','id':1,'result':{'content':[{'type':'text','text':'one'},{'type':'text','text':'two'}]}};out=json.loads(m.normalize_tool_output(json.dumps(base)));self.assertEqual(out['result']['structuredContent'],{'text':'one\n\ntwo'});base['result']['structuredContent']={'exact':True};self.assertEqual(json.loads(m.normalize_tool_output(json.dumps(base)))['result']['structuredContent'],{'exact':True})
 def test_rejection_logs_reason_only(self):
  value={'result':{'isError':True,'content':[{'type':'text','text':'fresh chrome_snapshot required SECRET-PAGE-TEXT'}]}};capture=io.StringIO()
  with contextlib.redirect_stdout(capture):out=json.loads(m.normalize_tool_output(json.dumps(value)))
  self.assertEqual(out,value);self.assertNotIn('SECRET-PAGE-TEXT',capture.getvalue());self.assertEqual(json.loads(capture.getvalue())['reason'],'stale_observation')
 def _watch(self,disconnected):
  client,caller=socket.socketpair();upstream,server=socket.socketpair();done=threading.Event()
  try:
   if disconnected:caller.close()
   t=threading.Thread(target=m.watch_forwarding,args=(client,upstream,'peer',done),kwargs={'authorize':lambda p:disconnected,'interval':0.001});t.start();server.settimeout(2);self.assertEqual(server.recv(1),b'');done.set();t.join(2);self.assertFalse(t.is_alive())
  finally:
   done.set();client.close();caller.close();upstream.close();server.close()
 def test_owner_revocation_closes_only_current_upstream(self):self._watch(False)
 def test_client_disconnect_closes_upstream(self):self._watch(True)
 def _http(self,method,path,body=None,allowed=True,response=None):
  seen=[];response=response or {'result':{'content':[{'type':'text','text':'synthetic-result'}]}}
  class U(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_POST(self):
    data=self.rfile.read(int(self.headers.get('Content-Length','0')));seen.append((self.path,self.headers.get('Authorization'),json.loads(data)));raw=response if isinstance(response,bytes)else json.dumps(response).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
    try:self.wfile.write(raw)
    except BrokenPipeError:pass
  with tempfile.TemporaryDirectory()as d:
   address=str(Path(d)/'mcp.sock');unix=UnixStreamServer(address,U);t=threading.Thread(target=unix.serve_forever);t.start()
   def connect(connection):connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.settimeout(3);connection.sock.connect(address)
   m.TOKEN='synthetic-token'
   with patch.object(m.UnixConnection,'connect',connect),patch.object(m,'gateway_peer_allowed',lambda peer:allowed):
    server=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);thread=threading.Thread(target=server.serve_forever);thread.start();client=http.client.HTTPConnection(*server.server_address,timeout=5)
    try:client.request(method,path,body);r=client.getresponse();status,raw=r.status,r.read()
    finally:client.close();server.shutdown();thread.join();server.server_close()
   unix.shutdown();t.join();unix.server_close()
  return status,raw,seen
 def test_actual_http_unix_handoff(self):
  body=json.dumps({'jsonrpc':'2.0','id':7,'method':'tools/call','params':{'name':'chrome_snapshot'}});status,raw,seen=self._http('POST','/mcp',body);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['result']['structuredContent'],{'text':'synthetic-result'});self.assertEqual(seen,[('/mcp','Bearer synthetic-token',json.loads(body))])
 def test_unauthorized_and_wrong_path_never_forward(self):
  for path,allowed,status in [('/mcp',False,403),('/other',True,404)]:s,_,seen=self._http('POST',path,'{}',allowed);self.assertEqual(s,status);self.assertEqual(seen,[])
 def test_request_and_response_budget(self):
  status,_,seen=self._http('POST','/mcp','x'*65537);self.assertEqual(status,413);self.assertEqual(seen,[]);status,_,seen=self._http('POST','/mcp','{}',response=b'x'*2000001);self.assertEqual(status,503);self.assertEqual(len(seen),1)
 def test_health_requires_actual_snapshot_tool(self):
  status,raw,seen=self._http('GET','/healthz',response={'result':{'tools':[{'name':'chrome_snapshot'}]}});self.assertEqual(status,200);self.assertEqual(json.loads(raw),{'status':'ok'});self.assertEqual(seen[0][2]['method'],'tools/list');status,_,_=self._http('GET','/healthz',response={'result':{'tools':[]}});self.assertEqual(status,503)
if __name__=='__main__':unittest.main()
