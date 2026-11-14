import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eva.voice_service.codex_media import (
    CodexMediaSession,
    REALTIME_SPEECH_PROMPT,
    VOICE_PROMPT,
)
from eva.voice_service.codex_session import CodexVoiceSession


class RealtimeSpeechModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_mode_remains_legacy_while_realtime_speech_enables_native_audio_and_handoffs(self):
        legacy = CodexMediaSession({}, SimpleNamespace(session_id="00000000-0000-0000-0000-000000000001"), lambda: None)
        self.assertFalse(legacy.native_realtime)

        realtime = CodexMediaSession(
            {"voice_mode": "realtime_speech"},
            SimpleNamespace(session_id="00000000-0000-0000-0000-000000000002"),
            lambda: None,
        )
        self.assertTrue(realtime.native_realtime)
        self.assertIsNone(realtime.renderer)

    async def test_codex_voice_session_forwards_selected_voice_to_realtime_start(self):
        captured = {}
        session = CodexVoiceSession(
            "unused", lambda: ("tok", "acc"), None, timeout=0.05, media_only=False, voice="marin"
        )
        session.thread = "t-1"

        async def fake_request(method, params):
            captured["method"] = method
            captured["params"] = params
            return {}

        session.request = fake_request
        session.events.put_nowait({"method": "thread/realtime/started", "params": {"threadId": "t-1"}})
        session.events.put_nowait({"method": "thread/realtime/sdp", "params": {"threadId": "t-1", "sdp": "v=0-ans"}})
        ans = await session.start("v=0\r\n", prompt=REALTIME_SPEECH_PROMPT)
        self.assertEqual(ans, "v=0-ans")
        self.assertEqual(captured["method"], "thread/realtime/start")
        self.assertEqual(captured["params"]["voice"], "marin")
        self.assertFalse(captured["params"]["clientManagedHandoffs"])
        self.assertEqual(captured["params"]["prompt"], REALTIME_SPEECH_PROMPT)
        self.assertNotEqual(REALTIME_SPEECH_PROMPT, VOICE_PROMPT)


if __name__ == "__main__":
    unittest.main()
