# Independent review: installed autonomous browser, 3 October 2026

**Decision: GO for the recorded current-installation outcome. No blocking finding.**

Reviewer: `/root/completion_diagnosis`. Integration base: `eb78d763`.
Evidence: [autonomous-browser-installed-20261003.json](../evidence/product/autonomous-browser-installed-20261003.json).

## Scope and source review

An ordinary owner conversation delegates a public website task to the existing worker. Authorized tool use starts Eva's own stopped Chromium, follows a real link, reads the destination and returns the observed title and URL without personal Chrome sharing or an owner UI action.

The bundled source review covered the managed-browser autostart, shared context/composition, exact native compositor dependency and browser-bound MCP session correction. Autostart validates authority and input before the lifecycle operation, starts only the stopped state, respects owner/recovery states and retains cancellation and uncertain-result boundaries. The private HMAC session identity binds the configured owner-derived connector and validated browser revision under the existing gateway credential. It is shared across browser tools and core/worker instances, rotates with the generation/credential, and is sent only on RPC POSTs. Stored-result replay does not start the browser or repeat effects. Other connectors retain their existing transport behavior.

The exact Pixelflux snapshot wheel is `8583b1baf6119d38908a353affc7c6e7efca518009dc694fa78814adf0d30971`, from upstream commit `f9d49bfcb84d4f27d92eddf29781840298ab9d9e`. Independent source inspection confirmed idle callbacks and the retained settings/frame protocol. This approval does not apply to the incompatible PyPI wheel with the same 2.1.0 version label.

## Recorded installed result

I read the final evidence, the recorded tool events, terminal work state, before/after controller records, installed profiles and independent MCP readback. All seven private-artifact hashes listed in the evidence matched. All six recorded installed source hashes matched the current repository; the recorded core and worker profiles/source hashes agree.

Work `work:eva-chat:b9e6f0ec-d627-5bf8-a405-2f1416bdae5a` succeeded at `2026-10-03 00:39:08 UTC`. The controller moved from stopped revision 70 to Eva-controlled revision 72 with profile preservation reported. The trace records only the own connector for browser navigation, snapshot, click on `e13` and destination snapshot. The click and final snapshot both returned the IANA destination. A separate observer session then called only discovery/snapshot operations, with no observer navigation or click, and confirmed `https://www.iana.org/help/example-domains`, the level-one heading `Example Domains` and the supporting page text. The returned answer agrees with that observation.

The record reports no owner UI action or manual intervention during the final task. Two preceding failures remain visible: missing idle rendering, then loss of snapshot references between separate upstream MCP sessions. Their results are not counted as successes.

Existing focused checks are retained: 47 autostart checks, 27 composition checks and 102 session/gateway checks; these overlap and must not be summed as distinct tests. I did not rerun tests, models or browser actions for this final review.

## Delivery boundary

The measured final request took 81.506 seconds. Recorded usage is 101,588 input and 782 output tokens with partial accounting; monetary cost, credits and Codex development consumption remain unknown. This is a working autonomous browser result, not a speed or cost advantage.

Approval is limited to this installed public-page task. It does not establish universal website/login/CAPTCHA/download support, concurrent-job isolation, viewer acceptance, reboot/firewall recovery or owner/family acceptance. Old ContextForge session generations have no TTL; revision rotation alone does not clean them up. The native overlay is present in the current container with an exact reconstruction pin; fresh optional installation remains separate O1 work. Previous T3 acceptances are preserved; this review invents no additional task completion.
