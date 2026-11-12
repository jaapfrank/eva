import asyncio
import unittest
from codex_voice_session import CodexVoiceSession, VoiceTransportError

class SessionTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session = CodexVoiceSession('unused', lambda: ('secret', 'account'), timeout=.03)
        session.thread = 'our-thread'
        async def acknowledged(*_): return {}
        session.request = acknowledged
        return session

    async def test_ack_followed_by_error_is_not_success_and_does_not_leak(self):
        s = self.session()
        s.events.put_nowait({'method':'thread/realtime/error', 'params':{
            'threadId':'our-thread', 'message':'secret token reflected by server'}})
        with self.assertRaises(VoiceTransportError) as caught:
            await s.start('v=0\r\n', prompt='test')
        self.assertNotIn('secret', str(caught.exception))

    async def test_waits_for_both_events_and_ignores_other_threads(self):
        s = self.session()
        for method, params in [
            ('thread/realtime/error', {'threadId':'other-thread'}),
            ('thread/realtime/sdp', {'threadId':'our-thread', 'sdp':'answer'}),
            ('thread/realtime/started', {'threadId':'our-thread'})]:
            s.events.put_nowait({'method':method, 'params':params})
        self.assertEqual(await s.start('v=0\r\n', prompt='test'), 'answer')

    async def test_ack_without_readiness_times_out(self):
        with self.assertRaisesRegex(VoiceTransportError, 'did not become ready'):
            await self.session().start('v=0\r\n', prompt='test')

    async def test_cancelled_exit_waits_for_cleanup(self):
        s = self.session()
        entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def close():
            entered.set()
            await release.wait()
            closed.set()
        s.close = close
        task = asyncio.create_task(s.__aexit__())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(closed.is_set())

if __name__ == '__main__': unittest.main()
