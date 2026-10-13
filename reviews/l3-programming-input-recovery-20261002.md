# Independent review: programming input recovery

Date: 2026-10-02  
Decision: GO for this bounded correction.

The reviewed change converts malformed nested JSON and non-object
`arguments_json` into the existing `ConversationActionInputRejected` path.
Rejection occurs before dispatch, a host request, or a durable receipt. A
corrected call can therefore use the same step safely; the existing successful
receipt still prevents duplicate execution. A previously started programming
session remains marked for final status checking and cleanup.

The conversation exception gains an optional keyword-only prompt while retaining
its existing default. The shared loop offers its existing single correction
within the current model/turn allowance. Its transient invalid-input feedback
has `action_admitted: false` and is excluded from successful action receipts.
Outer schema validation, source authorization, cancellation, transport errors,
and uncertain-effect handling are unchanged.

Reviewed files and SHA-256:

- `src/eva/adapters/postgres/programming_actions.py`:
  `e629cfe71019c1c6037d75839d5fad465bec7558294298908e5bc1cd02a7323a`
- `src/eva/application/conversation.py`:
  `7e64d5f146c8d6a0bdac4a6d5564d2f92954b6764fdb8af6ec54bf3ef9502a62`
- `tests/operations/test_programming_service.py`:
  `f9af2aac6a5ec11f2b4c03f78f7f24a43fe47f452435428f77bc24531d887eb9`

Validation: the coordinator reports 136 focused checks passing, including the
existing conversation and delegated-runner checks. This independent review
inspected the patch and tests without rerunning them. The new parameterized test
covers malformed JSON, an array, and null: no dispatch, no pending receipt, a
corrected same-step call, and one execution on replay. Existing
`tests/application/test_background_research_contact.py` covers the shared loop's
single correction, repeated-invalid exit, no false admission, and preservation
of earlier real receipts. No blocking gap was found.

Controlled tests do not establish live provider recovery. That resumed provider
run, installed-version verification, candidate quality, independent evaluation,
and application of any improvement remain separate evidence owned by the
coordinator; none is claimed by this review.
