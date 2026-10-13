# Bounded native gateway shutdown evidence

The existing graceful shutdown completed one pending native document action exactly once. This supports further review of a narrow maintenance procedure; it is not runtime GO or an all-client admission proof.

Evidence: `/home/administrator/project/.eva2-artifacts/gateway-shutdown-proof-20260913/proof-summary.json`, SHA256 `54c1a5edb21597f372ce1bec0609668dbd1a9dc4595eb9210626964798aa5cba`. Scripts and case logs remain in that private directory. Sanitized summary and configuration comparison accompany this review in `eva2/evidence/gateway-shutdown-native-20260913`.

## Results

| Fresh case | Observed result | Maintenance classification |
|---|---|---|
| Native Node paused, one gateway RPC pending, gateway SIGTERM, Node continued | One `slow.txt`, valid journal checksums and exactly P,L,C; created receipt; gateway exit0. Existing keepalive and new-connection probes after signal failed. | Bounded native completion passed. |
| Pending native call, gateway SIGKILL | Gateway exit137, disconnected client, no terminal journal record at both readbacks. | Failed drain; block. |
| Pending native call, client timeout/disconnect, gateway SIGTERM | Client TimeoutError; gateway exit0, but no terminal journal record at both readbacks. | Unresolved effect; block despite clean exit. |

Each case uses new private root/journal/database, exact current gateway image, entrypoint, command, init wrapper and six copied source mounts. Gateway environment differs only in three private secret-file locations and exact fixture endpoint pinning. The fixture runs UID0 while live runs UID10001 and uses its own network, mounts and quotas; no DAC or production-owner-access proof is claimed. The native provider binary/image is unchanged; only its disposable Node process is temporarily stopped to hold a call pending. No models, live resources or consumed prior fixtures were used. Fixture providers were stopped after readback; evidence and containers retained.

Two initial metadata-only setup attempts were retained: one lacked the live init wrapper and was replaced before effects; the next failed to mount a script into a readonly configuration directory. Neither is counted as test evidence or retried after an effect. New unique fixtures fixed preparation.

## Narrow maintenance procedure proposed for review

1. Bind the actual gateway image, full compose sources, current patches and registry/assignment snapshot. Enumerate every gateway client and every outstanding effect without age-based omission. Unknown activity remains blocking.
2. After independent authorization of a bounded gateway-only maintenance window, use the existing graceful stop, with a reviewed timeout long enough for the actual downstream bounds. Do not use Docker's default forced-kill timeout as evidence of completion. Preserve the exact stop outcome and do not start replacement when forced termination occurred.
3. Treat successful gateway exit as listener closure only. Reconcile every previously admitted or shutdown-racing action against its own authoritative terminal receipt/journal. A missing record, disconnected client, cancelled handler or clean exit does not prove absence of an effect. Do not repeat uncertain actions.
4. Only after all effects are accounted for may the exact two-endpoint configuration be recreated. Verify original resources/assignments and old PJ01 files/journal/status/result before admitting new work. Rollback targets only gateway configuration and exact newly registered resources; never delete or cascade existing state.

Remaining gaps: probes were sent 700ms after SIGTERM, so this evidence does not establish an atomic no-new-admission boundary at the instant of the signal. The current old gateway cannot retrospectively inventory every racing call from a newly installed counter. Browser operations, other providers, streaming and detached downstream tasks require their own accounting or a reviewed proof that they are absent. If that accounting cannot be obtained, this procedure stays blocked; these tests do not justify a broad stop or a new drain platform.
