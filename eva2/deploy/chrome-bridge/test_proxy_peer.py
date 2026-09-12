import importlib.util,pathlib,socket,unittest
spec=importlib.util.spec_from_file_location('chrome_proxy',pathlib.Path(__file__).with_name('proxy.py'))
proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)

def resolver(*addresses):
    return lambda *args:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,0)) for address in addresses]

class GatewayPeerTests(unittest.TestCase):
    def test_reboot_address_change_does_not_keep_old_peer_authorized(self):
        self.assertTrue(proxy.gateway_peer_allowed('172.30.0.2',resolver('172.30.0.2')))
        self.assertTrue(proxy.gateway_peer_allowed('172.30.0.4',resolver('172.30.0.4')))
        self.assertFalse(proxy.gateway_peer_allowed('172.30.0.2',resolver('172.30.0.4')))
        self.assertFalse(proxy.gateway_peer_allowed('172.30.0.10',resolver('172.30.0.4')))

    def test_missing_ambiguous_or_inappropriate_resolution_fails_closed(self):
        for addresses in [(),('172.30.0.2','172.30.0.4'),('127.0.0.1',),('8.8.8.8',),('0.0.0.0',),('169.254.1.2',)]:
            with self.subTest(addresses=addresses):
                self.assertFalse(proxy.gateway_peer_allowed(addresses[0] if addresses else '172.30.0.4',resolver(*addresses)))
        def unavailable(*args):raise socket.gaierror('unavailable')
        self.assertFalse(proxy.gateway_peer_allowed('172.30.0.4',unavailable))

if __name__=='__main__':unittest.main()
