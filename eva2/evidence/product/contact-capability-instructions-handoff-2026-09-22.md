# Authorized future-work instructions

Base: `63fc248a`, on new branch
`codex/contact-capability-instructions-20260922`. The earlier latency and Luna
none candidates remain on their separate branch and are not ancestors of this
change. No model requests, runtime settings, deployment or board edits.

## Confirmed defect and bounded fix

`generate` constructed the offered deferred tool catalog from the authorized
`available_actions`, but passed an empty tuple to `_instructions` whenever tools
were offered. The effective model instructions consequently stated that the
route could not schedule future work even when the authorized catalog contained
`create_bounded_durable_goal`, `start_delegated_work` or `resume_delegated_work`.

Providerless Pydantic fixtures reproduced that precise sentence in all six
positive cases before the change: each of those tools on the first phase and
after a personal-memory result. Six unavailable/read-only/stop-only cases passed.

The adapter now supplies the actual authorized action names to its existing
instruction builder. That builder uses only the three relevant work-start or
resume capabilities to decide whether the negative sentence is accurate; it
does not add descriptions or schemas to the prompt. The evidence requirement
for promising a time or later response remains in both cases. Merely registering
a tool descriptor does not grant the capability, and get/stop tools cannot
remove the negative guidance. Other receipt-specific instructions are unchanged.

The offered tool definitions, authority checks, action execution and model
selection are unchanged. This corrects an instruction contradiction; there is
no latency or model-quality claim and no automatic permission grant.

## Verification

192 tests passed in 13.52 seconds in a disposable network-disabled container:
the 12 new effective-instruction cases, existing Pydantic model tests,
conversation-model tests and contact/work-model selection tests. The known
catalog digest assertion was separated and run explicitly: it still fails with
expected `e96adcb879caaf9a7ef864369c15d30db1b106603e8c37056b1e55c8511b6ca0`
versus actual `4b2ed4d6d8b444a917aee8c34ed92c13b08f72f94effed8f3ce1423ebdd9d905`.
These are the exact baseline values previously reproduced against unchanged
installed modules. This candidate edits neither the catalog nor its assertion.
The complete selection is therefore not described as all green.

Receipts: `contact-capability-instructions-tests-2026-09-22.xml` and
`contact-capability-catalog-baseline-2026-09-22.xml`. Root owns independent review,
integration and any eventual installation. No physical or provider-backed trial
was performed or requested for this fix.
