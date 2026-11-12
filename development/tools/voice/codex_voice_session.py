"""Experimental transport probe for Codex 0.162.1; NOT a production default.

The installed schema marks token-injection auth internal-only. The caller supplies
EVA's existing access-lease loader and owns microphone, playback and authority.
No credentials or upstream error text are logged or persisted by this adapter.
"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile

class VoiceTransportError(RuntimeError):
    pass

class CodexVoiceSession:
    def __init__(self, executable, credentials, *, timeout=20):
        self.executable, self.credentials, self.timeout = executable, credentials, timeout
        self.events = asyncio.Queue(maxsize=512)
        self.pending = {}
        self.sequence = 0
        self.process = self.reader = self.home = None
        self.thread = None
        self.closing = False

    async def __aenter__(self):
        self.home = tempfile.TemporaryDirectory(prefix="eva-voice-session-")
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.executable, "app-server", "--listen", "stdio://",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "CODEX_HOME": self.home.name},
                cwd=self.home.name, limit=1048576)
            self.reader = asyncio.create_task(self.read())
            await self.request("initialize", {
                "clientInfo": {"name": "eva_voice_probe", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True}})
            await self.write({"method": "initialized"})
            token, account = self.credentials()
            await self.request("account/login/start", {
                "type": "chatgptAuthTokens", "accessToken": token, "chatgptAccountId": account})
            result = await self.request("thread/start", {
                "ephemeral": True, "cwd": self.home.name, "model": "gpt-6.1-sol",
                "approvalPolicy": "never", "sandbox": "read-only", "environments": [],
                "dynamicTools": [], "baseInstructions":
                "Isolated synthetic voice transport test. Do not use tools or perform work."})
            self.thread = result["thread"]["id"]
            return self
        except BaseException:
            await self.close()
            raise

    async def write(self, message):
        if self.process is None or self.process.returncode is not None:
            raise VoiceTransportError("Voice process unavailable")
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise VoiceTransportError("Voice process disconnected") from None

    async def request(self, method, params):
        self.sequence += 1
        ident = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.write({"id": ident, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError:
            raise VoiceTransportError("Voice request timed out") from None
        finally:
            self.pending.pop(ident, None)
            if not future.done():
                future.cancel()

    async def read(self):
        try:
            while raw := await self.process.stdout.readline():
                message = json.loads(raw)
                if "method" in message:
                    if "id" in message:
                        # Never silently answer tool or authentication-refresh requests.
                        await self.write({"id": message["id"], "error": {
                            "code": -32601, "message": "Unsupported by voice transport probe"}})
                    else:
                        self.events.put_nowait(message)
                elif (future := self.pending.get(message.get("id"))) is not None and not future.done():
                    if "error" in message:
                        future.set_exception(VoiceTransportError("Voice request rejected"))
                    else:
                        future.set_result(message.get("result", {}))
        except (ValueError, asyncio.QueueFull, OSError):
            pass
        finally:
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(VoiceTransportError("Voice event stream closed"))
            if not self.closing and self.process.returncode is None:
                self.process.terminate()

    async def start(self, offer_sdp, *, prompt):
        """Wait for real startup events; an RPC ACK alone is not success."""
        if not offer_sdp.startswith("v=0") or len(offer_sdp) > 131072:
            raise ValueError("Invalid voice SDP offer")
        await self.request("thread/realtime/start", {
            "threadId": self.thread, "outputModality": "audio", "version": "v3",
            "transport": {"type": "webrtc", "sdp": offer_sdp},
            "clientManagedHandoffs": True, "includeStartupContext": False, "prompt": prompt})
        try:
            async with asyncio.timeout(self.timeout):
                accepted, answer = False, None
                while not accepted or answer is None:
                    event = await self.events.get()
                    params = event.get("params", {})
                    if params.get("threadId") != self.thread:
                        continue
                    if event["method"] in {"thread/realtime/error", "thread/realtime/closed"}:
                        raise VoiceTransportError("Voice session failed after startup acknowledgment")
                    if event["method"] == "thread/realtime/started":
                        accepted = True
                    if event["method"] == "thread/realtime/sdp":
                        answer = params["sdp"]
                return answer
        except TimeoutError:
            raise VoiceTransportError("Voice session did not become ready") from None

    async def say(self, text):
        if not text or len(text) > 2000:
            raise ValueError("Synthetic speech must be short")
        await self.request("thread/realtime/appendSpeech", {"threadId": self.thread, "text": text})

    async def close(self):
        if self.closing:
            return
        self.closing = True
        try:
            if self.thread and self.process and self.process.returncode is None:
                try:
                    await asyncio.wait_for(self.request("thread/realtime/stop", {"threadId": self.thread}), 3)
                except (VoiceTransportError, TimeoutError):
                    pass
        finally:
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            if self.home:
                self.home.cleanup()

    async def __aexit__(self, *_):
        closing = asyncio.create_task(self.close())
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await closing
            raise
