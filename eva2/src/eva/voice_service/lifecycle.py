"""Idempotent resource cleanup for the local media session."""
import asyncio

class SessionLease:
    def __init__(self, session, room, remove):
        self.session, self.room, self.remove = session, room, remove
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
            await self.room.disconnect()
