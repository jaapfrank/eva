# L3 context improvement — independent installed review, 3 October 2026

**Installed GO for this bounded reviewed improvement.** Eva submitted the accepted application through her existing delegated task/tool route; the host installed the operator-prepared images after that task completed; a separate later Eva task read back the completed installation without applying it again. Independent live readback confirmed the exact candidate in both healthy services. This establishes the concrete reviewed application outcome, not unrestricted autonomous release engineering or recursive AIDE² improvement.

The frozen candidate remains `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`, from baseline `99954a459bb14c3411613d165d981887746890749df1e51c2dd50f0a21acf34e`. The canonical package/review ID is `503e263aa1c01ca0bc5aa443990c98a61c94fa29ae4c9f6d637135ccf3b9d5ab`. Its unchanged independent quality assessment is [l3-context-independent-20261003.json](../evidence/product/l3-context-independent-20261003.json): fixed promotion gate passed, with no new holdout or answer-model evaluation in this installed review.

## Application and independent readback

The ordinary application task `work:eva-chat:8a68dfd2-e2d6-5fe6-9408-bb056791627e` called `apply_reviewed_component_improvement` and received `queued/installed:false`, then read `waiting/installed:false`. It finished `succeeded` at 11:38:51 UTC, without a stop request, and correctly reported only that the application request was accepted. The durable queue contains exactly one application request, `6ea597a8d7e47362fd270ba0694e1513b109e03561fe6e7e639f62343edbabd5`, now `completed`. Its accepted-review hash `519c2654ee098aed56dfb9e865628bad5d0e48c7543479b9154c8fa954927a2a` matches the source-import record.

The existing updater recorded `running_with_retained_mounts`. Independently inspected the recorded current container IDs, checked their image IDs and health, and compared every actual mount with the updater receipt. Both services were running and healthy; both mount lists matched. Read the actual files in each container under `/opt/eva/src/eva`: the context source equals the frozen candidate above and the grant correction equals `cf7b13dd84f87751a01ca8b69beb05c11ddedaa5cfa399125cca217dc99c8698`.

| Service | Installed image |
| --- | --- |
| Core | `sha256:730524883c9c47c193b33e9a875ad4c4e3f3b722d315c50387fd10c8fdd59661` |
| Delegated worker | `sha256:0b1375541e87bf8145ef896248836b51d78d14e47b6c32dd8df951efbf4205b1` |

The later task `work:eva-chat:878208c1-51a8-5a38-b29a-3c5f9bc3c2d1` made one status-tool call and succeeded at 11:49:22 UTC. Its original broad collector omitted the component event because the event returned the earlier application task ID. Independently reconstructed the exact component thread from this later task's execution/source/step binding and exported its existing event using read-only database transactions. At 11:49:15 UTC, `get_reviewed_component_application` returned `status:verified`, `state:completed` and `installed:true` for the same single request and original application task. No new application request was created.

## Assistance, failure and measurement limits

Operators prepared and accepted the package, source import, host service, configuration and images, including the two-name grant correction after an actual failed startup. The prior source review had missed that grant integration gap. The existing task/model route then triggered the reviewed application and performed the later status read. These are distinct contributions: Eva authored the evaluated context candidate and requested its accepted application; this run does not prove she independently built or approved the deployment envelope.

An earlier status question said to start nothing new and stayed in the foreground route, where the status tool was unavailable. It returned an honest limitation after 38.232 seconds, without a new task or application. A subsequent explicit, short, read-only work request reached the available status tool. This assisted routing limitation remains visible; the run does not demonstrate reliable status access from every phrasing.

Readiness uses a database snapshot plus the existing graceful updater shutdown. It is not an atomic admission barrier against all new work. Healthy services, exact source/mount readback and one later status receipt establish this installation; they do not establish long-term reliability, family acceptance, universal self-installation, latency improvement or broader model superiority. The earlier bounded answer assessment and its sampling/latency-variance limits remain unchanged.

| Recorded model phase | Responses | Input tokens | Output tokens | Requested tool calls |
| --- | ---: | ---: | ---: | ---: |
| Application foreground | 1 | 6,913 | 204 | 1 |
| Application worker | 3 | 16,694 | 192 | 2 |
| Earlier unavailable foreground status | 3 | 23,729 | 307 | 2 |
| Later status foreground | 1 | 7,623 | 205 | 1 |
| Later status worker | 2 | 12,323 | 205 | 1 |
| Total for these application/status interactions | 10 | 67,282 | 1,113 | 7 |

These counts exclude candidate generation, the earlier independent answer evaluation and operator development work. Recorded request-response times were 16.034 seconds for application acceptance, 38.232 seconds for the unsuccessful foreground status question and 10.832 seconds for later status-task acceptance; task timestamps place worker completion approximately 30 and 23 seconds after their respective starts. Acceptance latency is not installation latency. Credits and monetary cost remain unknown.

## Evidence binding

Private evidence remains under `/home/administrator/project/.eva2-artifacts/l3-context-typed-20261003/application`; no protected evaluation cases or credentials are included here.

- `reviewed-host/update.json`: SHA256 `dacaf8835969d375954fc67fbf1a595385f5a163310e57f6245970a74d52fe03`.
- `reviewed-host/build.json`: SHA256 `491ee4f9bfa36ac8f371fd63ffc567ae88777436e9e1910f4764de5cabc6fc15`. This is the actual bound corrected build; the older top-level build receipt is retained history.
- `contract-completion/actual-image-profile-preflight.json`: SHA256 `3a0b11f71035f3813e59054baf99b0d1a0055e741103c84b55214e55c5298b9b`.
- Independently exported `ordinary-status-task/status-receipt.private.json`: SHA256 `cb785fef8655ae6a72f8b9e6ee9be982eaa7e1d9d72c6377478d3ba43904f6ed`.
- Existing signed source review `l3-context-application-20261003.md` remains SHA256 `2e18519f3fdbf23fa9665749b0e2bacdd86bf9feab8ea9b311dbb6eab80acab6`; `l3-reviewed-application-source-20261003.md` remains SHA256 `067def4d971a9ad9a3096b7acecf35e3a0bf5a8d84a5d1804baaf0fca11a7f40`.

No tests, model calls, holdout comparisons, deployment changes or product-source edits were made by this final review. Only the scoped private status export and this new review were written.
