# O1.2 selected installer modules review

Disposition: **final GO for selected Home Assistant installed**, with the terminal 2 October evidence recorded below. The earlier conditional source review is preserved; full O1.2 and the other optional-module boundaries remain open. Review is against the working-tree changes on `b60880497be753fa22ff8f18214220d7f35ba1b7`, superseding the earlier review against `85401486`. The reviewer edited only this review and made no source edits, test replay, provider/authentication requests or Docker mutations.

The selected-provider helper reads current private configuration through the trusted store and requires its digest to match the selected capability's actual v4 verification receipt. Receipt bytes/hash, slot, ready/verified outcome, secret reference and generation are compared with the confirmed plan. Existing plan contracts are retained. The runtime binder checks the live plan and current secret generations; the helper borrows no unrelated installed household endpoint or credential. The ordinary configuration API accepts a changed endpoint only while the capability is selected/blocked, so changing a ready selection through that route changes the live plan before a new verification.

The earlier transition blocker is fixed. The configuration validator now runs before the final `bind()` recheck and `resource_preparation`. A changed plan or secret generation at that boundary prevents persistent resource preparation. The focused workflow test changes the plan inside the validator and asserts retained material/access files with no resource event. Validator failure likewise preserves the phase and files, records only the failure type, and prevents resource preparation. The existing fresh-start boundary still checks current inputs before starting the prepared target.

The ordinary CLI forwards both optional provider ports; both preparation factories accept them. For selected HA, the package enables the existing general `home_assistant_request` route and binds its inventory/API principal allowance to SHA-256 of the fresh verified owner's principal ID. That matches the existing authorization hash. Existing guards require a bound private-person/household audience whose principal matches the turn, or a separately verified native initiative mandate. The entity-specific effect flag remains false; the independently enabled general API supports reads and writes within its existing contract. No new entity whitelist or task-specific script was introduced. Actual house mutations and owner acceptance are not established by this source review.

The selected endpoint reaches the maintained core template; its selected secret follows the existing materialization/binding and reader-ownership route. Skip projects out HA settings and credential mounts. The validator uses the selected immutable image and the actual graph's environment/bind files with `--network none` and `--pull never`, loading `RuntimeSettings` and HA provider settings without constructing a provider request. The maintained image's default reader is `65532:65532`, matching the core graph's explicit user. Actual image identity and execution still belong to the final target evidence. This check creates one transient configuration container; it is not a zero-Docker proof.

Reported evidence, not replayed by this reviewer: 19 focused cases passed on local Python 3.12.3; the latest overlapping workflow subset passed 11 cases in 1.34 s, including the validator-plan-change boundary. These results are not additive and are distinct from actual selected-image Python 3.12.14 validation. The coordinator also reports ordinary wizard HA selection and read-only verification through the current APIs in 0.025 s, with the existing model receipt reused and no model calls or house actions. No target workflow receipt or resources had been created when this conditional review was requested.

This source GO is bound to the following file bytes:

| File | SHA-256 |
| --- | --- |
| `eva2/tools/packaged_installer_target.py` | `b53ba03c21f33c6ceb16bd0936354abc610ba6db820af7b8650a689a7038351e` |
| `eva2/tools/installer_target_workflow.py` | `5c681efc6f9366d0b5279ee82f5652072a4bd809717d9f79e5f912b889373d9d` |
| `eva2/src/eva/bootstrap/installer_wizard_cli.py` | `5e8dbaed7ccf1fa978667c1b69e2296fbbcd71621e0c64cd055e7a7dd904ab7f` |
| `eva2/tests/operations/test_installer_target_workflow.py` | `26b1a940703abc6a6af028fd361425b811de719e723329ee8d2caff7b911a875` |
| `eva2/tests/operations/test_packaged_installer_target.py` | `f5a3485f17ef77f654090aab0e78ad7b63af00221b96981efb60d3837b0dfc11` |

The conditional source GO required these corrected package bytes to bind to the actual ordinary wizard-to-target workflow receipt, selected-image configuration acceptance, runtime startup and installed selected-HA consumer/access evidence. That requirement is now satisfied by the terminal review below. Read-only evidence must remain distinguished from actual house actions and owner acceptance. Selected Telegram/email/adaptive connector, voice/browser, public release/support and full O1.2 completion remain open. The separate I2.1 generic discover/observe/create-automation outcome introduces no camera-selection requirement or hardcoded installer feature into this review.

## Final selected-HA target review — 2 October 2026

Final GO for the selected Home Assistant module on the ordinary fresh target `eva-install-f5afb1f07694`. This completes the previously missing selected-HA installation/read boundary only. Existing O1.1 text acceptance remains valid; full O1.2, voice/browser choices, other selected providers, public platform support and owner acceptance remain open.

All five file hashes in the source-review table above were checked again against both the repository working tree and the actual installed package under the existing selected-modules artifact directory. Every value matches exactly. The package deliberately reuses the earlier immutable runtime; its historical base/staging metadata is not a claim that every file from current main was rebuilt or that this is a public release. Actual preparation and startup are established by the new receipts below.

The old model receipt was not reused after its access lease changed. Ordinary reverification returned ready in 2.019 seconds, bound to the current secret generation, with measured 30 input and 9 output tokens. The current plan is ready_for_release_preparation, without blockers, and has SHA-256 `b83d9fd2a0055f5db6f0ec8c9c9527d564eb3a9bee78cdd3338618a7d974e7ec`. HA is ready; Telegram, email and the adaptive connector are explicitly skipped. The stored selected HA endpoint equals the launch graph's endpoint. The actual v4 HA receipt hash, configuration digest, secret reference and generation match the current plan/store.

One ordinary authenticated wizard prepare request returned HTTP 200/runtime_ready after 131.944 seconds. The workflow and preparation receipts bind that same plan and namespace. The retained launch graph hashes to `3df240cdb31c2ea52663fc58749329ea8b686fc2e0e081f012e4c0df3b072c8b`, matching the preparation receipt. No repair, repeated preparation or reinstall is recorded in this target transition.

The selected immutable core image is `sha256:35eba561f26f4745b7995ae849c9341c441361a74106326308781e7e3ffc7a69`. Its no-network configuration check accepted the actual environment/bindings without provider requests. The workflow records fresh authority initialization and successful startup of the model lease, memory lease, memory service and core, with ready observations. The graph and recorded live core both use `65532:65532`. HA is enabled, the selected credential mount is retained, and the general API allowance is the SHA-256 of the freshly created target principal. Telegram environment and consumers are absent. These checks distinguish a configured running consumer from the earlier standalone provider probe.

The ordinary wizard-proxied question requested only the connected HA version. The stored dispatch binds the new message, fresh principal and fresh conversation binding. The event evidence contains exactly one prepared/executed `home_assistant.api.request`: GET /api/config, HTTP 200, success true, untruncated. Its version is 2026.9.2. The final streamed reply reports that same version and says no changes were made; the separate no-model API read agrees. The conversation required two measured model responses, 7177 input tokens, 91 output tokens and one GET, completing in 11.6 seconds. This is a real selected-module conversation/read proof, not a new house-action or performance claim. Money/credit costs and unmeasured background/development consumption remain unknown.

The reviewer read artifacts only, performed no additional runtime/provider/model requests or test replay, and changed only this review. This approval does not infer new physical speaker behavior, personal-browser connectivity, arbitrary selected-provider support or full optional-module installation.

Final evidence anchors:

| Artifact | SHA-256 |
| --- | --- |
| current-plan-20261002.json | `7890ca4267c8378dfe2a2a1515a208933c6b4d637d20f5bd17218f15964270fc` |
| model-reverified-20261002.json | `01b3929a00441a364587347a7ac01dfd2e5a08f85f5c223136f23272c936401d` |
| prepare-result-20261002.json | `9720befe65d8c6ad47d5a2f3db82260df041bd09be9d6f9889d33e5307b56e88` |
| ordinary-target/target/target-workflow.json | `c4420b4af18ca95118a44c0f56d76406a70d062a201f14f33768b87e107771c6` |
| selected-ha-runtime-read-20261002.json | `3f99810ccb8fa2b7101bb1762012de1d21f2d38c0bdcabb75b57ddeb71806723` |
| ha-conversation-summary-20261002.json | `2265e400ad481a11b004f2babc4d290112121164e87c9854e640f98200007282` |
| ha-conversation-events-20261002.private.json | `fc1ca3fcfc18484ac36b2ad3d8ca69f24a3e2759a9eb6d1562543350674cb7e7` |
| ha-conversation-result-20261002.private.json | `713a3e1dbea54c35c42772db964fa082de038ea31b3b1699d48fd1c7f59abc3b` |
