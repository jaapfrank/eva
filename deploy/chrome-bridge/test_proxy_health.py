import importlib.util,pathlib,unittest,io,json
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('chrome_proxy_health',pathlib.Path(__file__).with_name('proxy.py'))
proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)

class HealthTests(unittest.TestCase):
    def handler(self,peer='127.0.0.1'):
        handler=object.__new__(proxy.Handler)
        handler.path='/healthz';handler.client_address=(peer,1);handler.wfile=io.BytesIO()
        handler.send_response=lambda status:setattr(handler,'status',status)
        handler.send_error=lambda status:setattr(handler,'status',status)
        handler.send_header=lambda *args:None;handler.end_headers=lambda:None
        return handler

    def test_health_reads_catalog_without_any_browser_action(self):
        calls=[]
        class Connection:
            def __init__(self,*args,**kwargs):self.status=200
            def request(self,method,path,body,headers):calls.append(json.loads(body))
            def getresponse(self):return self
            def read(self,size):return b'{"result":{"tools":[{"name":"chrome_snapshot"}]}}'
            def close(self):pass
        with patch.object(proxy,'UnixConnection',Connection),patch.object(proxy,'TOKEN','test-only'):
            handler=self.handler();handler.do_GET()
        self.assertEqual(handler.status,200);self.assertEqual(calls[0]['method'],'src/tools/list')

    def test_missing_bridge_is_unhealthy_and_nonlocal_health_is_denied(self):
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,*args):raise ConnectionRefusedError()
            def close(self):pass
        with patch.object(proxy,'UnixConnection',Connection),patch.object(proxy,'TOKEN','test-only'):
            handler=self.handler();handler.do_GET();self.assertEqual(handler.status,503)
            handler=self.handler('172.30.0.4');handler.do_GET();self.assertEqual(handler.status,403)

if __name__=='__main__':unittest.main()
