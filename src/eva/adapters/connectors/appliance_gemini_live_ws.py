"""Outbound TLS WebSocket connector helper for Google Gemini Live (`BidiGenerateContent`)."""

from __future__ import annotations

from typing import Any
import websockets


async def connect_gemini_live_websocket(uri: str, *, open_timeout: float = 10.0) -> Any:
    """Open an outbound TLS WebSocket session to Google's Gemini Live BidiGenerateContent endpoint."""
    return await websockets.connect(uri, max_size=4 * 1024 * 1024, open_timeout=open_timeout)


__all__ = ["connect_gemini_live_websocket"]
