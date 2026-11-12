"""Bounded, content-free observation of native research SSE arrival times."""
import json
import logging
from time import monotonic

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
LOG.propagate = False
if not LOG.handlers:
    LOG.addHandler(logging.StreamHandler())


class ResearchTiming:
    EVENTS = frozenset({"response.created", "response.web_search_call.in_progress",
        "response.web_search_call.searching", "response.web_search_call.completed",
        "response.output_text.delta", "response.completed", "response.failed", "error"})

    def __init__(self):
        self.started = monotonic()
        self.buffer = b""
        self.discard_line = False
        self.events = {}
        self.headers_ms = None

    def elapsed(self):
        return round((monotonic() - self.started) * 1000)

    def feed(self, chunk):
        # Never log payloads. Skip oversized lines without retaining their bytes.
        for part in chunk.splitlines(keepends=True):
            ended = part.endswith(b"\n")
            if not self.discard_line:
                if len(self.buffer) + len(part) > 65536:
                    self.buffer = b""
                    self.discard_line = True
                else:
                    self.buffer += part
            if not ended:
                continue
            line, self.buffer = self.buffer, b""
            discard, self.discard_line = self.discard_line, False
            if discard or not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:])
            except (ValueError, UnicodeError):
                continue
            kind = event.get("type") if isinstance(event, dict) else None
            if isinstance(kind, str) and kind in self.EVENTS:
                stats = self.events.setdefault(kind, {"first_ms": self.elapsed(), "count": 0})
                stats["count"] += 1
                stats["last_ms"] = self.elapsed()

    def finish(self, outcome):
        LOG.info("research_transport_timing %s", json.dumps({
            "outcome": outcome, "elapsed_ms": self.elapsed(),
            "headers_ms": self.headers_ms, "events": self.events,
        }, separators=(",", ":")))
