"""Comparison-only observation of a sample-owned native Codex HTTP client.

Reuse Eva's raw SSE attestor and the existing sample ledger. This transport does
not authorize a run or prove all clients in an installation are bound. A caller
must dedicate the instance to one sample and bind every provider client.
"""

import asyncio
from dataclasses import asdict
import json
import logging

try:
    import httpx
except ImportError:
    # The installed core uses httpx2; Hindsight uses the original httpx package.
    import httpx2 as httpx

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
        self.invalid = False
        self.settled = False
        self.failure_observation = None

    def _record_failure(self, error):
        # The observer sees the decoded event before the native provider does.
        # Retain only provider-sanitized fields and fixed counters, never bytes,
        # exception text, upstream labels, credentials, or tool arguments.
        if self.failure_observation is not None:
            return
        failure = getattr(self.attestor, "failure", None)
        if failure is None:
            failure = getattr(error, "diagnostic", None)
        counts = getattr(self.attestor, "event_counts", {})
        observation = {
            "attempt_id": self.attempt if type(self.attempt) is int else None,
            "provider_failure": failure,
            "event_counts": {
                key: counts.get(key) if type(counts.get(key)) is int
                and counts[key] >= 0 else None
                for key in ("sse_events", "text_deltas", "tool_argument_deltas",
                            "reasoning_deltas", "completed_events")
            },
        }
        self.failure_observation = observation
        try:
            logging.getLogger(__name__).warning(
                "native_provider_failure %s",
                json.dumps(observation, separators=(",", ":")),
            )
        except Exception:
            # Diagnostic logging must never replace the original stop/close.
            pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await anext(self.parts)
        except StopAsyncIteration:
            try:
                self._observe_decoded(self.decoder.flush())
                self.attestor.finish()
            except Exception as error:
                self.invalid = True
                self._record_failure(error)
                await self.aclose()
                raise SampleStopped("native HTTP response has no complete raw usage") from error
            await self.aclose()
            raise
        except BaseException:
            self.invalid = True
            raise
        try:
            self._observe_decoded(self.decoder.decode(chunk))
        except Exception as error:
            self.invalid = True
            self._record_failure(error)
            raise SampleStopped("native HTTP response usage is invalid") from error
        return chunk

    def _observe_decoded(self, decoded):
        # HTTPX returns bytes; HTTPX2 streams decoder output as byte chunks.
        if isinstance(decoded, (bytes, bytearray)):
            self.attestor.feed(decoded)
        else:
            for chunk in decoded:
                self.attestor.feed(chunk)

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
        # Native SDKs may stop reading at response.completed, before HTTP EOF.
        # Settle only already-observed terminal bytes after successful close;
        # never drain unread bytes or upgrade cancellation/invalid input.
        task = asyncio.current_task()
        if (not self.invalid and not self.ledger.stop_requested
            and not (task is not None and task.cancelling())
            and self.attestor.terminal is not None and not self.settled):
            try:
                usage = self.attestor.finish()
            except Exception as error:
                self.invalid = True
                self._record_failure(error)
                raise SampleStopped("native HTTP terminal usage is invalid") from error
            self.ledger.finish(self.attempt, asdict(usage))
            self.settled = True


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
