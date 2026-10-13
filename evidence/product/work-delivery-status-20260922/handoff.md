# Private task delivery status in the existing owner view

Base `2fa216fc`, branch `codex/work-delivery-status`. The existing owner-service
work view now adds `delivery: {state, description}` to list/get/result and admission
responses. The work state, saved output and output hash remain separate and intact.
Existing Telegram list/status/result commands show the same Dutch description.

A completed work result is not represented as delivered. Acknowledged delivery says
it was delivered to the linked private chat; ambiguous delivery says its outcome is
uncertain and it is not automatically resent. Unbound historical tasks explicitly
say no automatic return is linked. No route is created or changed by reading status.

OwnerService accepts optional `delivery_status_loader: Callable[[str], str]`.
The helper of the same name in `bootstrap/delegated_work_delivery.py` binds the
existing read-only delivery-store status call to the configured owner/binding and
fresh Telegram binding loader. Root owns the runtime constructor wiring; status
reads may remain enabled when the result-delivery send switch is disabled.

An absent loader or unknown return value is shown as unknown. A lookup error is
shown as unavailable/unverifiable, without exception text or destination details.
That separate delivery authority failure does not hide a still-authorized stored
work result. Existing work-authority checks remain before retrieving the work.

Validation: **40 passed in 2.02 seconds**, existing command/composition suites plus
seven new status/lookup/helper cases. Coverage includes acknowledged, ambiguous,
unbound, unknown values, invalid values, failing lookup without secret leakage,
result preservation and fixed owner authority in the read-only helper. Existing
exact-output expectations were updated only for the intentional added status line.
No provider requests, Telegram messages, database migrations or installation.

Integration: import `delivery_status_loader` in runtime and supply the resulting
callback to OwnerService beside the destination callback, independently of the send
switch. Use the same work store, owner/binding and fresh binding-file loader. This
commit changes only the owner service, existing commands, composition helper and
their tests; no storage, runtime, worker or dashboard component was edited.
