"""Comparison-only observation of a sample-owned native Codex HTTP client.

Reuse Eva's raw SSE attestor and the existing sample ledger. This transport does
not authorize a run or prove all clients in an installation are bound. A caller
must dedicate the instance to one sample and bind every provider client.
"""

from dataclasses import asdict
import json

import httpx

from cp001_accounting import SampleAsyncStream, SampleStopped


class _ObservedBytes:
    def __init__(self, response, ledger, attempt, attestor_factory):
        self.response, self.ledger, self.attempt = response, ledger, attempt
        self.parts = response.stream.__aiter__()
        # Decode only the observed copy. Forward the original wire bytes/headers;
        # HTTPX's native client owns its own content decoder on the new response.
        self.decoder = response._get_content_decoder()
        self.attestor = attestor_factory()
        self.closed = False
        self.close_error = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await anext(self.parts)
        except StopAsyncIteration:
            # Close before declaring a settled attempt. A failed close must not
            # be hidden by HTTPX subsequently treating the response as closed.
            await self.aclose()
            try:
                self.attestor.feed(self.decoder.flush())
                usage = self.attestor.finish()
            except Exception as error:
                raise SampleStopped("native HTTP response has no complete raw usage") from error
            self.ledger.finish(self.attempt, asdict(usage))
            raise
        try:
            self.attestor.feed(self.decoder.decode(chunk))
        except Exception as error:
            raise SampleStopped("native HTTP response usage is invalid") from error
        return chunk

    async def aclose(self):
        if self.closed:
            return
        if self.close_error is not None:
            raise self.close_error
        try:
            await self.response.aclose()
        except BaseException as error:
            self.close_error = error
            # Leave failed ownership unsettled, but reject any native retry.
            with self.ledger.lock:
                self.ledger.stopped = self.ledger.stopped or "native response close failed"
            raise
        self.closed = True


class _BoundBody(httpx.AsyncByteStream):
    def __init__(self, response, ledger, attempt, attestor_factory):
        self.guard = SampleAsyncStream(ledger, attempt, _ObservedBytes(response, ledger, attempt, attestor_factory))

    async def __aiter__(self):
        async for chunk in self.guard:
            yield chunk

    async def aclose(self):
        await self.guard.aclose()


class ResponsesSampleTransport(httpx.AsyncBaseTransport):
    """Bind an explicit native role's actual HTTP attempts to one existing ledger.

    Request bytes, native retries, response bytes and headers remain unchanged.
    Only complete raw terminal usage settles a row; no estimate or zero fallback.
    Closing/cancelling is comparison assistance, not native cancellation evidence.
    """

    def __init__(self, inner, ledger, *, role, endpoint, attestor_factory):
        self.inner, self.ledger, self.role = inner, ledger, role
        self.endpoint = httpx.URL(endpoint)
        self.attestor_factory = attestor_factory
        self.bodies = []

    @property
    def active_responses(self):
        return sum(not body.guard.closed for body in self.bodies)

    async def handle_async_request(self, request):
        if request.method != "POST" or request.url != self.endpoint:
            self.ledger.stop_async_attempts("unbound native provider endpoint")
            raise SampleStopped("unbound native provider endpoint")
        try:
            payload = json.loads(await request.aread())
            model = payload["model"]
            effort = payload["reasoning"]["effort"]
        except (ValueError, KeyError, TypeError) as error:
            self.ledger.stop_async_attempts("unbound native request model/effort")
            raise SampleStopped("unbound native request model/effort") from error
        attempt = self.ledger.begin(self.role, model, effort)
        self.ledger.track_async_attempt(attempt)
        response = None
        try:
            response = await self.inner.handle_async_request(request)
            body = _BoundBody(response, self.ledger, attempt, self.attestor_factory)
        except BaseException:
            with self.ledger.lock:
                self.ledger.stopped = self.ledger.stopped or "native HTTP setup failed"
            if response is not None:
                await response.aclose()
            self.ledger.incomplete(attempt, "native_http_request_usage_unknown")
            raise
        self.bodies.append(body)
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=body, extensions=response.extensions)

    async def aclose(self):
        first_error = None
        for resource in [*self.bodies, self.inner]:
            try:
                await resource.aclose()
            except BaseException as error:
                first_error = first_error or error
                with self.ledger.lock:
                    self.ledger.stopped = self.ledger.stopped or "native transport close failed"
        if first_error is not None:
            raise first_error
