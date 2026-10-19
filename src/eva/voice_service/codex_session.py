"""Codex-managed WebRTC voice with all backend reasoning routed to EVA.

Pinned to the experimentally verified Codex 0.162.1 protocol. The existing lease
loader supplies account access; no login UI or persistent credentials are added.
"""
from __future__ import annotations
import asyncio
import json
import hashlib
import secrets
import os
import tempfile
from pathlib import Path

class VoiceTransportError(RuntimeError):
    pass

class CodexVoiceSession:
    def __init__(self, executable, credentials, backend, *, timeout=25, media_only=False,
                 context_sender=None, on_input_turn=None):
        self.executable, self.credentials, self.timeout = executable, credentials, timeout
        self.backend = backend
        self.media_only = media_only
        self.context_sender = context_sender
        self.on_input_turn = on_input_turn
        self._error_salt = secrets.token_bytes(32)
        self.account_id = None
        self.events = asyncio.Queue(maxsize=512)
        self.pending = {}
        self.sequence = 0
        self.process = self.reader = self.home = None
        self.thread = None
        self.closing = False

    async def __aenter__(self):
        self.home = tempfile.TemporaryDirectory(prefix="eva-voice-session-")
        try:
            # Apply the same local provider before app-server initialization.
            # Delaying it until thread/start can initialize the default remote
            # provider first. This temporary file contains only session config;
            # account credentials still come from the rotating lease below.
            config={**self.backend.provider_config(),
                    "model_provider":"eva_voice", "model":"gpt-6.1-sol"}
            lines=[]
            def emit(path,value):
                if isinstance(value,dict):
                    for key,item in value.items():emit([*path,key],item)
                else:
                    if not isinstance(value,(str,bool,int,float)):
                        raise ValueError("Unsupported voice provider configuration")
                    lines.append(".".join(json.dumps(key) for key in path)
                                 + " = " + json.dumps(value,ensure_ascii=False))
            for key,value in config.items():emit(key.split("."),value)
            config_path=Path(self.home.name)/"config.toml"
            config_path.write_text("\n".join(lines)+"\n",encoding="utf-8")
            config_path.chmod(0o600)
            self.process = await asyncio.create_subprocess_exec(
                self.executable, "app-server", "--listen", "stdio://",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**{k:v for k,v in os.environ.items() if k not in {"OPENAI_API_KEY", "CODEX_API_KEY"}},
                     "CODEX_HOME": self.home.name},
                cwd=self.home.name, limit=1048576)
            self.reader = asyncio.create_task(self.read())
            await self.request("initialize", {
                "clientInfo": {"name": "eva_voice", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True}})
            await self.write({"method": "initialized"})
            token, account = self.credentials()
            self.account_id = account
            await self.request("account/login/start", {
                "type": "chatgptAuthTokens", "accessToken": token, "chatgptAccountId": account})
            result = await self.request("thread/start", {
                "ephemeral": True, "cwd": self.home.name, "model": "gpt-6.1-sol", "modelProvider": "eva_voice",
                "config": self.backend.provider_config(),
                "approvalPolicy": "never", "sandbox": "read-only", "environments": [],
                "dynamicTools": [], "baseInstructions":
                "EVA owns all reasoning and actions through the configured local provider. Do not use tools."})
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

    def input_turn_notification(self, message):
        """Observe only bound app-server turns; never publish provider text."""
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread:
            return
        method = message.get("method")
        turn = params.get("turn")
        identity = params.get("turnId") if method == "error" else (
            turn.get("id") if isinstance(turn, dict) else None)
        if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
            return
        phase = None
        if method == "turn/started":
            phase = "started"
        elif method == "error" or method == "turn/completed":
            failure = params.get("error") if method == "error" else (
                turn.get("error") if isinstance(turn, dict) else None)
            if method == "error" or (isinstance(turn, dict) and turn.get("status") == "failed"):
                encoded = json.dumps(failure, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                info = failure.get("codexErrorInfo") if isinstance(failure, dict) else None
                categories = {"contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
                    "rateLimitExceeded", "serverOverloaded", "internalServerError", "unauthorized",
                    "badRequest", "httpConnectionFailed", "responseStreamConnectionFailed",
                    "responseStreamDisconnected", "responseTooManyFailedAttempts"}
                category = info if isinstance(info, str) and info in categories else "other"
                status = None
                if isinstance(info, dict) and len(info) == 1:
                    key, value = next(iter(info.items()))
                    if key in categories and isinstance(value, dict):
                        category = key
                        code = value.get("httpStatusCode")
                        if type(code) is int and 100 <= code <= 599: status = code
                self.backend.diagnostic("input_transport_error", category=category, http_status=status,
                    error_sha256=hashlib.sha256(self._error_salt + encoded[:65536]).hexdigest(),
                    error_truncated=len(encoded)>65536, retrying=params.get("willRetry") is True)
                if method == "turn/completed" or params.get("willRetry") is False:
                    phase = "failed"
            elif method == "turn/completed":
                phase = "completed"
        if phase is not None and self.on_input_turn is not None:
            self.on_input_turn(phase, identity)

    async def read(self):
        try:
            while raw := await self.process.stdout.readline():
                message = json.loads(raw)
                if "method" in message:
                    if "id" in message:
                        if message['method'] == 'account/chatgptAuthTokens/refresh':
                            self.backend.current()
                            token, account = self.credentials()
                            if account != self.account_id:
                                raise ValueError('Voice account changed')
                            await self.write({'id':message['id'], 'result':{
                                'accessToken':token, 'chatgptAccountId':account}})
                        else:
                            await self.write({"id": message["id"], "error": {
                                "code": -32601, "message": "EVA does not permit Codex tool execution"}})
                    else:
                        self.input_turn_notification(message)
                        if message['method'] in {'turn/started','turn/completed','error','thread/realtime/delegation/started','thread/realtime/delegation/completed'}:
                            self.backend.diagnostic('codex_event', method=message['method'])
                        if not self.media_only and message['method'] == 'thread/realtime/transcript/done':
                            params = message.get('params', {})
                            if params.get('threadId') == self.thread:
                                self.backend.transcript(params.get('role'), params.get('text'),
                                    interrupted=None if params.get('role') == 'assistant' else False)
                        if message['method'] in {'thread/realtime/started', 'thread/realtime/sdp',
                                                 'thread/realtime/error', 'thread/realtime/closed'}:
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
            if not self.closing:
                try:
                    self.events.put_nowait({'method':'thread/realtime/error',
                        'params':{'threadId':self.thread,'message':'Voice event stream closed'}})
                except asyncio.QueueFull:
                    pass
                if self.process.returncode is None:self.process.terminate()

    async def start(self, offer_sdp, *, prompt, output_modality="audio"):
        """Wait for real startup events; an RPC ACK alone is not success."""
        if not offer_sdp.startswith("v=0") or len(offer_sdp) > 131072:
            raise ValueError("Invalid voice SDP offer")
        await self.request("thread/realtime/start", {
            "threadId": self.thread, "outputModality": output_modality, "version": "v3",
            "transport": {"type": "webrtc", "sdp": offer_sdp},
            "clientManagedHandoffs": self.media_only, "delegationAckFiller": False, "codexResponseHandoffMode": "commentary",
            "model": "gpt-live-1-codex", "voice": "juniper",
            "includeStartupContext": False, "prompt": prompt})
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
            raise ValueError("Speech update must be short")
        if self.closing or self.context_sender is None:
            raise VoiceTransportError("Speech context transport unavailable")
        # Codex 0.162.1's V3 wire drops appendText's role; its omitted channel
        # defaults to speakable. A developer-role prep is therefore not quiet.
        # Use the verified FramelessBidi channels on one ordered data channel,
        # with no await between binding the script and offering it for speech.
        self.context_sender({"type": "session.context.append", "channel": "commentary",
            "content": [{"type": "input_text", "text":
                "Read only this Dutch script exactly once, then remain silent. "
                "Do not add words. Script: " + json.dumps(text, ensure_ascii=False)}]})
        self.context_sender({"type": "session.context.append", "channel": "speakable",
            "content": [{"type": "input_text", "text": text}]})

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
