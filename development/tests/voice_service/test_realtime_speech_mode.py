import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eva.voice_service.codex_media import (
    CodexMediaSession,
    REALTIME_SPEECH_PROMPT,
    VOICE_PROMPT,
)
from eva.voice_service.codex_session import (
    CodexVoiceSession,
    SUPPORTED_REALTIME_V3_VOICES,
    normalize_realtime_v3_voice,
)


class RealtimeSpeechModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_core_mode_enforced_even_when_legacy_realtime_speech_configured(self):
        legacy = CodexMediaSession({}, SimpleNamespace(session_id="00000000-0000-0000-0000-000000000001"), lambda: None)
        self.assertFalse(legacy.native_realtime)

        # Even if a config carries legacy "voice_mode": "realtime_speech", production
        # CodexMediaSession must never allow raw provider weights ("Ik ben ChatGPT")
        # to bypass Eva's authoritative Core + literal speech renderer.
        coerced = CodexMediaSession(
            {"voice_mode": "realtime_speech"},
            SimpleNamespace(session_id="00000000-0000-0000-0000-000000000002"),
            lambda: None,
        )
        self.assertFalse(coerced.native_realtime)

        isolated_proto = CodexMediaSession(
            {"_unsafe_test_realtime_speech": True},
            SimpleNamespace(session_id="00000000-0000-0000-0000-000000000003"),
            lambda: None,
        )
        self.assertTrue(isolated_proto.native_realtime)
        self.assertIsNone(isolated_proto.renderer)

    async def test_codex_voice_session_forwards_supported_v3_voice_and_normalizes_unsupported_voice(self):
        self.assertIn("juniper", SUPPORTED_REALTIME_V3_VOICES)
        self.assertIn("maple", SUPPORTED_REALTIME_V3_VOICES)
        self.assertEqual(normalize_realtime_v3_voice("marin"), "juniper")
        self.assertEqual(normalize_realtime_v3_voice("maple"), "maple")
        self.assertEqual(normalize_realtime_v3_voice(None), "juniper")

        captured = {}
        session = CodexVoiceSession(
            "unused", lambda: ("tok", "acc"), None, timeout=0.05, media_only=False, voice="maple"
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
        self.assertEqual(captured["params"]["voice"], "maple")
        self.assertFalse(captured["params"]["clientManagedHandoffs"])
        self.assertEqual(captured["params"]["prompt"], REALTIME_SPEECH_PROMPT)
        self.assertNotEqual(REALTIME_SPEECH_PROMPT, VOICE_PROMPT)

        fallback_session = CodexVoiceSession(
            "unused", lambda: ("tok", "acc"), None, timeout=0.05, media_only=True, voice="marin"
        )
        self.assertEqual(fallback_session.voice_name, "juniper")


if __name__ == "__main__":
    unittest.main()
