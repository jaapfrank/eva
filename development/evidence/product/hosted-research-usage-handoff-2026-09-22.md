# Count completed hosted web-search calls from provider evidence

Separate candidate after prompt-fix commit
`b08c07cc72c2cbaab9d71cb0e192cefc2cd18d4b`; its implementation hunk does not depend
on that prompt change. Only the model adapter, offline tests and this evidence
are changed. The research runner and tool interface remain owned by the research
agent. No model requests, settings changes or deployment were performed.

## Defect and correction

The existing `_request_using` inferred `tool_calls` from whether the parser's
second return element was a ConversationActionCall. The research parser returns
`(text, cited_sources, consulted_sources)`, so research usage always reported zero.
This follows directly from source types; it does not prove that a prior real
request actually used web search.

The existing SSE usage capture now optionally collects unique completed
`web_search_call` item IDs for the research parser. It accepts completed item-done
records, explicit web-search-completed events and completed response output.
Search and open-page actions are counted alike. Repeated done records, lifecycle
events and terminal output echoes share one ID set and count once. An in-progress
or failed item, ordinary answer text, source URLs and citation annotations do not
establish a completed call. A completed search without an item ID fails closed.

Research usage uses the size of this set only after a valid terminal response and
validated token usage. Error, failed, incomplete and cancelled research streams
cannot return a successful usage record. The normal function-call counting path
is unchanged. Parser return shape, artifacts, credentials, budgets and offered
tools are unchanged.

## Verification and boundaries

191 tests passed in a disposable network-disabled container, including 20 new
cases for search/open-page completion through each evidence form, deduplication,
no-search URL text, incomplete/failed items, error and incomplete streams, missing
terminal usage, missing identity, and unchanged ordinary function-call counts.
Receipt: `hosted-research-usage-tests-2026-09-22.xml`.

The known pre-existing catalog hash assertion was excluded from this selection;
its separate failing receipt is retained with the preceding prompt handoff. No
catalog definitions or assertions were changed. This is not a claim that the
whole model suite is green.

The research agent's earlier single provider request has no retained raw SSE.
Its recorded 9967 ms, 8895 input / 135 output tokens, one HTTP request and ambiguous
zero tool count remain historical evidence. This fix cannot reconstruct its
actual number of completed web calls. Root may independently review and authorize
a new integrated probe under a separate budget; none is implied by these scripts
or tests.

Root separately owns adding `start_background_research` to prompt capability
guidance and contact/work routing during research integration. That name is
deliberately not mixed into this usage-count correction.
