# F1.2 current-candidate preparation review - 5 October 2026

**Decision: preparation GO only. F1.2 remains open.** The refreshed preparation
binds the current candidate and the original synthetic records. It does not
prove a Windows restart, post-restart recall/result access or a new completed
product function.

Reviewer: `/root/t3_installed_review`. Base:
`62955fecab735364e32c2c9467d16cc5e9c36448`.
Scope: the refreshed public evidence, existing F1 evidence/review, collector
source and saved before-restart metadata. No runtime tests, model calls, service
queries, authentication, reboot or protected P2 files were used in this review.

## Source and preserved data

The collector restricts event selection in SQL to four literal thread IDs,
within the existing timeline scopes and original start boundary. Those four
distinct IDs match exactly the original saved synthetic event threads.
The work query retains the three original message IDs; the snapshot binds the
same two work records. This is a narrower query, not a fresh scenario or replay.

The snapshot retains separate before/after output names and refuses to overwrite
an existing snapshot. It reads the known runtime inventory, original memory
document identity and two original document paths. It calls only the existing
boot-identity reader; it does not invoke or arm the historical model canary.

Independent local comparisons returned true for exact equality of original
work, delivery events, memory binding and document bindings. Host-unit and
host-client bindings also match. The original snapshot hash remains
`82bdde24a8cc908eab30f87b269c07d2022b8a0ff5a44131a0277d87f4e16b2a`.
All six hashes referenced by the new public receipt match their saved files.
The new snapshot hash is
`ed0e41ac1c76c542c0fd3c68d6a1e90f226234343d3ee972f11f99e1696d6033`.

Counts match the receipt: 20 containers, 11 host services, three host-client
files, two work records and two documents. The saved capture reports all
containers running and all host services active. These are capture-time
metadata, not a new end-to-end behavior check.

## Current candidate and boot classification

Only core/worker image and configuration bindings differ in the compared
runtime fields. Container names, mount bindings and restart policies are
preserved. The current images match the reviewed T3 installed evidence:

- Core: `sha256:b42a5b8a74eaf7307339b8513fbb1e3343d925039a4e0e7866ddfc4defc05dca`.
- Worker: `sha256:42e43365526c6e0f6d9ecb5e0ccd2f5c0829fc7c2c44ab72a5b92ff5a1733468`.

The supplemental receipt binds both the browser-firewall helper and its checksum
manifest. Its helper hash matches the reviewed T3 source:
`84b932f979f6bbe969674a1dec851e359bdcf6f03f043f77ebef9e619a3bbb91`.

The saved Windows boot and kernel boot identities are unchanged; the WSL init
start value changed. The receipt correctly declines to count that difference as
the required Windows restart. Speaker metadata remains limited to device
connection/identity/firmware/mute information; its receipt does not claim native
audio transport health, audible speech or microphone interruption.

## Review boundary and remaining work

An initial proposed comparison was rejected before execution because it would
print complete private metadata. The subsequently authorized narrower comparison
returned only named booleans and counts. No memory/result payload was printed;
no unresolved approval blocker remains for this preparation review.

After an owner-initiated real Windows restart, compare the same bindings and
synthetic records, including the supplemental helper hashes, without replaying
the original tasks. Ordinary memory recall and result access after restart and
an independent final review still remain. The earlier F1 functional evidence is
retained; this preparation neither repeats it nor replaces those remaining
conditions. Human acceptance and the separate final comparison remain open.
