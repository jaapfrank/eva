# Independent review: ordinary room request

Date: 2026-10-02  
Reviewer: `/root/l3_application_seam`  
Decision: GO for the original functional T2.2 criterion. English rendering of the reviewer's recorded findings; no change to the decision or evidence scope.

The ordinary website question supplied no technical entity identifiers. On integration baseline `d2f1154d0e552b2d6a3f37a1700b25ab9c5fb609`, work `work:eva-chat:8f646008-a77b-5f37-8c3a-c6bf5e2e1f94` discovered the area and its light inventory. Group membership confirmed the three selected physical lights. The speaker LED ring was excluded. No runtime change or additional live trial was performed by the reviewer.

All 15 tool admissions belong to the same source and execution namespace. Exactly two mutate HA: turn on and turn off, each targeting only the same three lights. The other calls read inventory, service information or state. Each admission has an executed and closed receipt; no repeated actuation or failed attempt was found.

Three individual GETs confirmed on, then three confirmed off after restoration. Independent HA websocket events show off → on → off for each target. Independent final GETs match the original states and all published attributes, including the unchanged speaker LED ring. Aggregate groups followed member state changes. An unrelated light briefly became unavailable with null user context; no Eva request targeted it.

The conclusion depends on these readbacks and observations, not merely HTTP200, STATE_VERIFIED event names or succeeded status. Raw work output retains verified=false. The final answer accurately describes the actions and explicitly limits verification to HA observations. The result is available through the original website retrieval route; actual browser display, Telegram, push or audio delivery was not established.

Reviewed: frozen criteria, 131 operational events, independent light events, before/after snapshots, area membership, work result and measurements in the existing private artifact directory. Verified artifact anchors:

- `events.private.json`: `f276a2f1e55467d2ac8f052e2f939f832caff936b8aade963f8218280cba7839`
- `ha-light-events.private.json`: `8b9b6ec6451df5a53a73d7b0d9a8fb7bbfc68738cd4f93facf6c77b1fe4cbe97`
- `lights-after.private.json`: `14c3f3d72df8ffc2341d07241abf0a61eb289a5719e534b16ad5cb818f831766`

The initially empty receipt export used the authority scope; records were retrieved using the existing `external_timeline_scope(source.tenant_id, source.installation_id)`. No task replay was needed. A separate observer-only template error did not affect task execution.

Limits: all selected lights were initially off, so restoration of initially-on color settings is unproven. No general reliability, physical/audio acceptance or T2.1 historical timeout fix is claimed. Seventeen measured model responses and sixteen tools including intake consumed 216145 input and1514 output tokens; monetary and other background/development costs remain unknown. Recorded start12:58:50.963 UTC to completion13:01:44 UTC is about173 seconds, including the19.54-second intake. The reviewer identified the erroneous193-second sum; the coordinator corrected the public evidence. No speed or efficiency advantage is claimed.
