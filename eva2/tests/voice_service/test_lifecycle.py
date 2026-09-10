import asyncio
import pytest
from eva.voice_service.lifecycle import SessionLease

@pytest.mark.asyncio
async def test_expiry_can_close_itself_and_cleanup_is_idempotent():
    calls = []
    class Session:
        async def aclose(self):
            await asyncio.sleep(0)
            calls.append("session")
    class Room:
        async def disconnect(self):
            await asyncio.sleep(0)
            calls.append("room")
    lease = SessionLease(Session(), Room(), lambda: calls.append("removed"))
    async def expire(): await lease.close()
    timer = asyncio.create_task(expire())
    lease.timers.add(timer)
    await timer
    await lease.close()
    assert calls == ["removed", "session", "room"]

@pytest.mark.asyncio
async def test_room_disconnects_even_if_session_cleanup_fails():
    calls = []
    class Session:
        async def aclose(self):
            await asyncio.sleep(0)
            raise RuntimeError("failed")
    class Room:
        async def disconnect(self): calls.append("room")
    lease = SessionLease(Session(), Room(), lambda: None)
    with pytest.raises(RuntimeError): await lease.close()
    assert calls == ["room"]
