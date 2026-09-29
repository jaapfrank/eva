"""Owned async HTTP I/O behind the existing synchronous research interface."""
from __future__ import annotations

import asyncio
from io import BytesIO
from time import monotonic
import urllib.error
import urllib.request

import httpx2

from eva.adapters.model.contracts import ConversationModelError, ConversationModelDeadlineExceeded
from eva.application.cancellation import run_cancellable


def open_research_response(
    request: urllib.request.Request, *, deadline: float, maximum_bytes: int,
) -> BytesIO:
    """Close owned network resources before returning bytes or raising.

    No synchronous provider thread is spawned here. Cancellation of the owning
    request interrupts headers or body reads through the existing shared signal.
    Local closure does not attest that a remote provider has stopped computing.
    """
    async def read() -> bytes:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            async with httpx2.AsyncClient(
                transport=httpx2.AsyncHTTPTransport(retries=0),
                timeout=httpx2.Timeout(remaining), trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    request.get_method(), request.full_url,
                    headers=dict(request.header_items()), content=request.data,
                ) as response:
                    failed = not 200 <= response.status_code < 300
                    limit = 1024 if failed else maximum_bytes
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if failed:
                            chunks.append(chunk[:max(0, limit - (total - len(chunk)))])
                            if total >= limit:
                                break
                        elif total > limit:
                            raise ConversationModelError("research response is too large")
                        else:
                            chunks.append(chunk)
                    body = b"".join(chunks)
                    if failed:
                        raise urllib.error.HTTPError(request.full_url, response.status_code,
                            "research provider rejected request", dict(response.headers), BytesIO(body))
                    return body

    try:
        return BytesIO(asyncio.run(run_cancellable(read)))
    except (TimeoutError, httpx2.TimeoutException) as error:
        raise ConversationModelDeadlineExceeded("research provider deadline expired") from error
    except httpx2.TransportError as error:
        raise urllib.error.URLError("research provider transport unavailable") from error
