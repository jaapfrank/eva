# P1.1 native SDK/OAuth bridge review — 1 October 2026

Independent read-only review of `p1_agentzero_bridge_probe.py`, `p1_agentzero_litellm_capabilities.json`, sanitized product evidence, the participant bridge record and the new admission checkpoint. Retained reports inspected: v2 completed cases, v3 partial boundary and v5 final report. Relevant pinned native route and stream-close functions were checked. No authentication, inference, tests or replay were performed. Credential files, tokens and login challenges were not inspected.

## Retained result

The native source remains pinned to `e3051fb584b1a36be2b0a0c90606f1c2c2d356ec`; all three native module hashes in the v5 report match the reviewed files. Native model construction, actual asynchronous SDK serialization, registered OAuth routes and owned-auth forwarding are exercised. The upstream HTTP response and usage are synthetic. The WSGI transport runs in process and does not establish deployed proxy behavior or real network-disconnect handling.

The v2 Astra/medium nonstream result is retained. Its companion contact case did not prove native SSE: v3 shows an absent SDK wire `stream` flag, native JSON content and already completed synthetic usage. Those observations are kept as earlier evidence rather than described as a successful partial-stream stop.

The small capability declaration adds native-streaming metadata through LiteLLM's registration interface. It does not change model identity, supply fabricated prices or prove live provider support. The v5 report records the lookup changing from false to configured true and an actual SDK `stream:true` request with native `text/event-stream` output. The contact fixture completes and its native upstream closes before explicit fixture cleanup.

The v5 partial case cancels and settles the model task while the native upstream remains open and its ledger attempt remains pending. Before fixture cleanup, the active count is one and total usage is unknown. Explicit cleanup closes the response and changes the count to zero with `native_upstream_closed_usage_unknown`; total usage stays null. This is a discovered boundary limitation. Fixture cleanup is not evidence of automatic SDK, deployed-provider or cloud cancellation. Ledger usage is observed at the mocked upstream seam and does not establish complete real SDK/provider accounting.

## Source reporting finding resolved

**P2 — cancelled partial outcome labelled explicitly.** The executed probe could choose `observed_route_completed` when a partial sample's upstream had closed before result capture, despite the model task being cancelled and usage remaining unknown. The reviewed correction gives every partial outcome an explicit cancelled label, distinguishing observed upstream closure from unverified closure. The completed label is reserved for non-partial cases. The retained v5 negative result is unchanged; no native replay followed this reporting correction.

## Publication disposition

**GO for publishing the corrected probe, bounded negative checkpoint and sanitized observations.** The reporting finding is closed and no further blocker remains in this bounded review. The evidence, participant record and checkpoint keep native admission and product completion false, separate cleanup from the native result, and preserve preparation failures and synthetic consumption. They do not support full helper/retry/sync/tool coverage, real provider usage, cloud cancellation, participant parity or P1.1 completion.

The evidence separates the executed v5 probe hash from the current candidate hash and explicitly records the reporting-only correction. Both the current candidate hash and unchanged retained v5 report hash were checked.

Reviewed current source hashes:

- Executed v5 probe: `8ab1ce02bec96f2897e8c2f923914f1fa35dec9892d73943a96dc3899a6145ef`.
- Current corrected probe: `6b8a5018df9de5c537a4495f5d1174bd7e2129f1f671c6147c892b175a12b86a`.
- Capability declaration: `6f98bd8afc21330f3e8c30297bf252555b52af42abc8d9c040c64362ce8602c3`.
- Retained v5 report: `9a65d5ac4bca83729b42cd4f94278d1bfcd50ead2b791f0ecb2d0706315b7a64`.
