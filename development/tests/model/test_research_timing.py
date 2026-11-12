"""Telemetry retains event types and numbers, never provider content."""
import json
from eva.adapters.model.research_timing import ResearchTiming


def test_split_frames_and_oversized_payloads_do_not_leak(monkeypatch):
    records = []
    monkeypatch.setattr('eva.adapters.model.research_timing.LOG.info', lambda _, value: records.append(value))
    timing = ResearchTiming()
    frame = b'data: {"type":"response.output_text.delta","delta":"PRIVATE"}\n\n'
    for byte in frame:
        timing.feed(bytes([byte]))
    timing.feed(b'data: {"type":"PRIVATE","secret":"PRIVATE"}\n')
    timing.feed(b'data: ' + b'x' * 70000)
    timing.feed(b'PRIVATE\n' + frame)
    timing.finish('succeeded')
    result = json.loads(records[0])
    assert 'PRIVATE' not in records[0]
    assert result['events']['response.output_text.delta']['count'] == 2
    assert timing.buffer == b''
