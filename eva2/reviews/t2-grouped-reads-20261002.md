# Independent review: grouped HA state reads, 2026-10-02

Final GO to retain the installed three-file bundle; source approval and terminal evidence are distinguished below. Reviewed against base `7eab21f277b595c13291c0f5459f3a6dd0754ef3`. The source approval alone did not establish an efficiency result.

The final diff changes only three instruction texts: `HomeAssistantApiTool.conversation_tools` describes bounded reads through the existing POST /api/template route, and `_TaskModel.generate` advises grouping independent scoped reads within an observation phase. The third change narrows PostgresDelegatedWorkActions.active_context guidance: questions about an earlier result use its stored work ID; a new explicit action request requires current execution even when its wording matches a completed request. An old result proves only that earlier execution. Same-turn duplicate admission and unauthorized restart of uncertain, stopped or failed work remain disallowed. The temporary global change in pydantic_conversation.py was removed; there is no diff in that file. The bundle introduces no tool, entity selection rule, API permission, transport behavior, model change or task-budget change. Existing discovery and independent post-action readback instructions remain. The new wording explicitly preserves restoration-relevant attributes, every requested entity, unknown/unavailable states, completeness checks and ordered dependent actions.

The existing adapter parses valid template JSON before projection and retains nested attributes and numeric values. It still limits root pages to 50 items and marks oversized responses as truncated. The supplied real-adapter check returned all three requested records with attributes: HTTP 200, total 3, next_offset null, truncated false, 82 ms. The check used selected identifiers only in its private transport fixture; they are absent from the source instructions and planned user request. This confirms the existing grouped-read mechanism, not autonomous source selection or complete restoration behavior. The recorded 16 existing API checks passed; the reviewer did not rerun tests or issue model/HA requests.

The frozen criteria preserve the same ordinary request, provider/model roles, authority, contextual selection, actual on/readback/off/readback and original-state restoration. Efficiency remains a hypothesis until the installed trial supplies model responses, input tokens and latency alongside equivalent functional evidence. A service acknowledgement, old snapshot, omitted entity or incomplete page must not count as fresh verification. Instruction compliance is model behavior, not a new mechanical completeness guard.

The preceding successful run remains the baseline and is not invalidated. One matched trial can demonstrate a measured improvement for this case; it cannot establish general reliability, speed superiority or repair of the historical T2.1 timeout. Credits and unmeasured background/development costs remain unknown. No new proof of initially-on color restoration or physical audio acceptance is implied.

Evidence anchors:

- Criteria: `7153b7cfa5589e978d85c408d52ca2611f22babbf3fa8a3521f1b973513d459e`.
- Existing adapter check: `7c67ed837e62b7a1ff7277319ddfc529bc39bac06c2d4f49f8ce7ac89b86fbbb`.
- Adapter result: `ef11089f29d0aa513828e177079844a6f42fd287b80737ac8478bb84ce67bed2`.
- Reviewed HA source: `8b9677fbb91aec6ab2d795c3073f4d4c72fd93a0b7e581f73f58474a9e623bcd`.
- Reviewed task source: `b60a23c9b1d9805b3341686f159e4a41a33973cc7481b939e6a02e46a866808a`.
- Reviewed task-context source: `99954a459bb14c3411613d165d981887746890749df1e51c2dd50f0a21acf34e`.

The first post-guidance intake retrieved the previous completed work and restated its result. Its new ingress produced get_delegated_work for the old work ID, with no new work admission or HA execution. Preserve this failure and its cost separately; it is not a faster equivalent outcome. The active-context correction addresses that observed interpretation error at its source, without adding a second routing rule elsewhere. At source-review time its effectiveness remained unproven; the terminal evidence below now includes fresh admission and execution.

The initial source approval required fresh admission and the original selection/effect/readback/restoration criteria before comparing efficiency. The terminal assessment follows.


## Installed outcome and final verdict

Final GO to retain the installed three-file bundle. The terminal work is `work:eva-chat:fbe167b2-b38a-5ea9-8069-65e83ea3bbe3`, bound to a new ingress. Core and worker are healthy with identical current profile `cada222186f97ac991194652ab938f0bca3d5c608acbefe739e91ee2444572c5` and all three reviewed source hashes. The installed build record names core image `sha256:813f62fabd7787bf07527a0880860416cc93aded6ec30ed67457ac22078ebfda` and worker image `sha256:3de9d93e67ecca111fc782d111c1c300efa5726725c656538955298cd099b9c9`.

The foreground discovered areas and admitted genuinely new work. The worker discovered the room entities, excluded the speaker LED ring and selected the three physical lights plus their aggregate. The aggregate's recorded membership is precisely those same three lights: redundant addressing, without an additional physical target. Ten prepared/executed HA operations belong to this ingress: one foreground inventory and nine worker operations. There are exactly two device-mutating service calls, turn_on and turn_off. Template POSTs are reads. The separate delegation accounts for the eleventh total tool call.

Two template reads returned HTTP 400 before any light action: both report `Type is not JSON serializable: set`. The first attempted complete attribute dictionaries; converting supported_color_modes alone in the second did not remove every non-JSON value. Eva then requested explicit restoration fields and obtained four complete records: entity identity, state, brightness, color mode/temperature, color coordinates and effect. All selected lights were initially off with null light-setting values. This supplies the required restoration evidence for this case; it is not proof that arbitrary attribute structures or initially-on configurations are handled correctly.

One fresh grouped read after turn_on confirmed every selected record on. A further grouped read after turn_off confirmed every selected record off and matched the recorded restoration fields. Successful grouped responses have total 4, next_offset null, truncated false and no missing target. The partial service-schema page is discovery data, not state-verification evidence. Independent websocket events show all three physical lights off to on to off. Independent final snapshots match the original state and every published attribute for all 108 recorded lights. The final answer accurately describes this execution and makes no physical-observation claim.

The measured model-record totals independently reproduce the reported 114162 input tokens, 1825 output tokens, 12 responses and 11 tool calls, including the two corrected template failures. The baseline was 216145 input, 1514 output, 17 responses and 16 tools. Recorded start-to-completion decreased from 173.037 to 147.981296 seconds. These observations support fewer rounds and input tokens in this case. Output tokens increased; intake increased from 19.54 to 28.676 seconds, and the first observed light-on was later, 103.554 versus 93.938 seconds. This is not a claim of uniformly faster interaction.

The failed earlier intake remains separately recorded at 12538 input and 184 output tokens; combined current-trial usage is 126700 input and 2009 output. The successful comparison does not erase that failure or its cost. Recent history and the observed foreground model mix differ under the unchanged routing configuration, so this pair does not isolate the causal benefit of the guidance or establish a general benchmark. Credits, other background usage and development costs remain unknown.

The public evidence `eva2/evidence/product/t2-grouped-reads-installed-20261002.json` accurately retains these principal limits, unchanged task acceptances, no new task count and the open historical T2.1 timeout. The coordinator records 26 existing focused API/active-context checks; the reviewer did not rerun tests or issue live requests. No further run or source change is necessary for this bounded decision.

Final evidence anchors in the existing private `new-action` directory:

- Work record: `29a528e0059780c8f706fb40c55f64c849134c764317e84f99819d8d4021ae37`.
- Tool/model events: `ce767e99b05782708eaf12b1bfdaa049eb1b52896dcb84a4be01c61c84b6601b`.
- Independent light events: `6c369ef50eef29ea734fa72b975419c43eaba66522470e0703e66aa33f281893`.
- Final light snapshot: `07244c24b90f7322d4b6fe1a41467324acf7d80ea28e4fe59174889724c7a220`.
- Measurement: `289b948e00e41b35e09353943b33b742545caaa2a5cba44d353cd32db8f7c748`.
- Installed profile: `572fb4589c783f92afb4dbfe8d7ac8427f3c1a7051793ddeec90e87c84c7bfe9`.

No owner acceptance, physical audio/Telegram delivery, general reliability or autonomous self-improvement is inferred.
