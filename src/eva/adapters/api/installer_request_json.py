"""Bounded JSON intake shared by loopback installer routes."""

import json

from fastapi import HTTPException, Request


async def read_small_json_request(
    request: Request, maximum_bytes: int, request_label: str
) -> object:
    too_large = f"{request_label} request is too large"
    invalid = f"{request_label} request is invalid"
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > maximum_bytes:
                raise HTTPException(status_code=413, detail=too_large)
        except ValueError:
            raise HTTPException(status_code=422, detail=invalid)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > maximum_bytes:
            raise HTTPException(status_code=413, detail=too_large)
        raw.extend(chunk)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail=invalid)
