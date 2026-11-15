"""Idempotent resource cleanup for the local media session."""
import asyncio

class SessionLease:
    def __init__(self, session, room, remove, delete_room=None):
        self.session, self.room, self.remove = session, room, remove
        self.delete_room = delete_room
        self.timers = set()
        self._closing = None

    async def close(self):
        if self._closing is None:
            current = asyncio.current_task()
            for timer in self.timers:
                if timer is not current:
                    timer.cancel()
            self.remove()
            self._closing = asyncio.create_task(self._cleanup())
        if self._closing is not asyncio.current_task():
            await asyncio.shield(self._closing)

    async def _cleanup(self):
        try:
            await self.session.aclose()
        finally:
            try:
                was_connected = bool(getattr(self.room, "isconnected", lambda: True)())
                await self.room.disconnect()
                if not was_connected and getattr(self.room, "_ffi_queue", None) is not None:
                    try:
                        from livekit.rtc._ffi_client import FfiClient

                        FfiClient.instance.queue.unsubscribe(self.room._ffi_queue)
                    except Exception:
                        pass
            finally:
                if self.delete_room is not None:
                    await self.delete_room()
