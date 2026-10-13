"""Room-bound HA owner transport with leased media and owned cleanup."""
from __future__ import annotations
import asyncio
import base64
import json
from dataclasses import dataclass, field
import hashlib
import re
import secrets
import time
from urllib.parse import urlsplit
import aiohttp
from aiohttp import web


def token_scope(token):
    """Comparison filter only. LiveKit still verifies every JWT signature.

    A refreshed token may change its validity times, but cannot use this lease
    for a different issuer, participant, room, permission or metadata scope.
    """
    try:
        if not isinstance(token,str) or len(token)>16384 or len(token.split("."))!=3: return None
        header,payload,_=token.split(".")
        def decode(part):
            return json.loads(base64.urlsafe_b64decode(part+"="*(-len(part)%4)))
        if decode(header).get("alg")!="HS256": return None
        claims=decode(payload)
        if not all(claims.get(key) for key in ("iss","sub")) or not claims.get("video",{}).get("room"): return None
        for key in ("iat","nbf","exp"): claims.pop(key,None)
        claims.setdefault("kind","standard")
        claims.setdefault("identity",claims["sub"])
        return json.dumps(claims,sort_keys=True,separators=(",",":"))
    except (ValueError,TypeError,AttributeError): return None


@dataclass
class SignalLease:
    user_id: str
    token_digest: str
    expires: float
    room: str
    owner_token: str = field(repr=False)
    token_scope: str | None = field(default=None, repr=False)
    revoked: bool = False
    cleanup: asyncio.Task | None = field(default=None, repr=False)


class PhoneTransport:
    def __init__(self, client, gateway_url, user_allowed):
        parsed = urlsplit(gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid configured Eva gateway")
        self.client = client
        self.base = gateway_url.rstrip("/") + "/owner-voice"
        self.user_allowed = user_allowed
        self.leases = {}
        self.connections = set()
        self.timers = set()
        self.creates = set()
        self.create_lock = asyncio.Lock()
        self.closed = False

    async def _control(self, path, token, *, create=False):
        method = "GET" if path == "session/ready" else "POST"
        async with self.client.request(method, self.base + "/" + path,
                headers={"Authorization": "Bearer " + token}, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30 if create else 5)) as response:
            if response.status != 200:
                return {"status": response.status, "body": {"detail": "Eva's stemverbinding is niet beschikbaar of de eigenaarssleutel is ongeldig."}}
            return {"status": 200, "body": await response.json()}

    async def request(self, user_id, path, owner_token, capability=None):
        if self.closed: raise web.HTTPServiceUnavailable()
        if not await self.user_allowed(user_id): raise web.HTTPForbidden()
        if not isinstance(owner_token, str) or not 16 <= len(owner_token) <= 256 or any(c.isspace() for c in owner_token):
            raise web.HTTPUnauthorized()
        if path == "session/ready": return await self._control(path, owner_token)
        if path == "session/end":
            lease = self.leases.get(capability)
            if lease is None:
                return {"status": 200, "body": {"closed": True}}
            if lease.user_id != user_id or not secrets.compare_digest(owner_token, lease.owner_token):
                raise web.HTTPForbidden()
            try:
                await asyncio.wait_for(asyncio.shield(self._revoke(capability)), timeout=6)
            except asyncio.TimeoutError:
                return {"status": 503, "body": {"detail": "Afsluiten is nog niet bevestigd; Eva's tijdelijke audiotoegang wordt niet verlengd."}}
            return {"status": 200, "body": {"closed": True, "ongoing_core_work_cancelled": False}}
        if path != "session": raise web.HTTPNotFound()
        async with self.create_lock:
            if self.closed or len(self.leases) >= 8: raise web.HTTPServiceUnavailable()
            task = asyncio.create_task(self._create(user_id, owner_token))
            self.creates.add(task)
            task.add_done_callback(self.creates.discard)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    result = await asyncio.shield(task)
                    if result["status"] == 200:
                        await asyncio.wait([self._revoke(result["body"]["capability"])], timeout=6)
                finally:
                    raise

    async def _create(self, user_id, owner_token):
        result = await self._control("session/leased", owner_token, create=True)
        if result["status"] != 200: return result
        data = result["body"]
        token, room = data.get("token"), data.get("room")
        if not isinstance(token, str) or not token or not isinstance(room, str) or not re.fullmatch(r"eva-voice-[0-9a-f]{32}", room):
            raise web.HTTPBadGateway()  # Unrenewed media expires independently in 10s.
        capability = secrets.token_urlsafe(32)
        self.leases[capability] = SignalLease(user_id, hashlib.sha256(token.encode()).hexdigest(), time.monotonic()+1800, room, owner_token, token_scope(token))
        if self.closed or not await self.user_allowed(user_id):
            await asyncio.wait([self._revoke(capability)], timeout=6)
            raise web.HTTPForbidden()
        timer = asyncio.create_task(self._watch(capability))
        self.timers.add(timer)
        timer.add_done_callback(self.timers.discard)
        return {"status": 200, "body": {"token": token, "capability": capability, "url": "/api/eva2_voice/rtc/"+capability}}

    def _revoke(self, capability):
        lease = self.leases.get(capability)
        if lease is None:
            async def done(): pass
            return asyncio.create_task(done())
        lease.revoked = True
        if lease.cleanup is None:
            lease.cleanup = asyncio.create_task(self._cleanup(capability, lease))
        return lease.cleanup

    async def _cleanup(self, capability, lease):
        for key, socket in list(self.connections):
            if key == capability: await socket.close()
        # Keep failed cleanup owned and retain the room id for retries. Renewal
        # stops immediately; the media server additionally expires audio in 10s.
        while True:
            try:
                result = await self._control("session/end/"+lease.room, lease.owner_token)
                if result["status"] == 200: break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            if time.monotonic() >= lease.expires: break
            await asyncio.sleep(2)
        if self.leases.get(capability) is lease: self.leases.pop(capability, None)

    async def _watch(self, capability):
        while True:
            await asyncio.sleep(2)
            lease = self.leases.get(capability)
            if lease is None or lease.revoked: return
            if self.closed or lease.expires <= time.monotonic() or not await self.user_allowed(lease.user_id):
                await asyncio.shield(self._revoke(capability)); return
            try:
                renewed = await self._control("session/renew/"+lease.room, lease.owner_token)
                if renewed["status"] != 200:
                    await asyncio.shield(self._revoke(capability)); return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.shield(self._revoke(capability)); return

    async def signal(self, request, capability, tail):
        lease = self.leases.get(capability)
        token = request.query.get("access_token", "")
        scope = token_scope(token)
        matching = lease is not None and (secrets.compare_digest(lease.token_digest,hashlib.sha256(token.encode()).hexdigest())
            or (scope is not None and lease.token_scope is not None and secrets.compare_digest(scope,lease.token_scope)))
        if (self.closed or lease is None or lease.revoked or lease.expires <= time.monotonic()
                or not await self.user_allowed(lease.user_id)
                or len(request.query.getall("access_token",[]))!=1 or not matching):
            if lease is not None and (lease.expires <= time.monotonic() or not await self.user_allowed(lease.user_id)):
                self._revoke(capability)
            raise web.HTTPForbidden()
        if tail not in {"rtc", "rtc/validate"} or request.method != "GET": raise web.HTTPNotFound()
        url = self.base + "/livekit/" + tail
        if request.headers.get("Upgrade", "").lower() != "websocket":
            async with self.client.get(url, params=request.query, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15)) as response:
                body = await response.content.read(65537)
                if len(body)>65536: raise web.HTTPBadGateway()
                return web.Response(status=response.status,body=body,content_type=response.content_type,headers={"Cache-Control":"no-store"})
        async with self.client.ws_connect(url,params=request.query,heartbeat=20,max_msg_size=1048576) as upstream:
            if lease.revoked or self.closed: raise web.HTTPForbidden()
            downstream=web.WebSocketResponse(heartbeat=20,max_msg_size=1048576)
            await downstream.prepare(request)
            self.connections.add((capability,downstream))
            async def copy(source,target):
                async for message in source:
                    if message.type==aiohttp.WSMsgType.TEXT: await target.send_str(message.data)
                    elif message.type==aiohttp.WSMsgType.BINARY: await target.send_bytes(message.data)
                    else: break
            tasks=[asyncio.create_task(copy(upstream,downstream)),asyncio.create_task(copy(downstream,upstream))]
            try:
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                await downstream.close()
                self.connections.discard((capability,downstream))
            return downstream

    async def close(self):
        self.closed=True
        if self.creates:
            await asyncio.gather(*(asyncio.shield(task) for task in list(self.creates)),return_exceptions=True)
        for timer in list(self.timers): timer.cancel()
        await asyncio.gather(*self.timers,return_exceptions=True)
        cleanups=[self._revoke(capability) for capability in list(self.leases)]
        if cleanups:
            await asyncio.wait(cleanups,timeout=6)
