# Contact catalog cost comparison

Bounded C1 diagnostic on installed source e975681f, 28 September 2026.
The scripts use the installed provider bundle and contact model settings.
wire.py uses synthetic credentials and MockTransport; it deliberately terminates at a captured HTTP 400.
pair.py makes exactly two real provider requests with a fixed synthetic greeting.
ExternalToolset returns any tool request without executing it. No conversations or memories are written.
Do not rerun without a changed hypothesis. Run inside the existing core environment only after reviewing criteria.

This is a configured superset, not the exact authorized catalog of an owner turn.
No-catalog is a diagnostic control, not a proposed production configuration.
One sequential pair is not a general latency benchmark; credits remain unknown.
