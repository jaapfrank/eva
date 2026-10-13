# Selected e-mail installer — independent source review, 3 October 2026

**Decision: SOURCE GO for the three frozen files below, against base `5b6a60168e0c010cc4d2807e76a33fc5216c1cc5`.** This covers ordinary selected AgentMail configuration through the existing read-only provider. Installed acceptance remains pending delivery of the corrected provider in the actual target image.

The early independent check confirmed a real privacy gap: the existing AgentMail factory supplied no `authorize_turn`; its planner ignored the turn, and the shared wrapper did not supply an ordinary private-owner check on its behalf. Read-only inspection of the running packaged image `sha256:e2d6af7ca0cba49761efe54c082ee1fab988f9de06680c01bfaa4fb6469a21d4` confirmed that same implementation. Its AgentMail provider SHA256 was `8921d3459f3628429a40a44a4c7270006975de5aba49b9c6e0da595dc7172c57`. The actual adapter, provider and wrapper imported successfully without provider/model calls.

The frozen correction uses the existing wrapper callback. It requires exact runtime tenant, installation and principal IDs, a `private_person` audience, and the same audience principal. The installer already derives these IDs from its fresh owner authority. Enabled AgentMail without valid UUID owner configuration fails before reading the credential; absent AgentMail configuration remains disabled. The shared wrapper rechecks the callback for catalog availability, execution, observation and replay, so this is an execution boundary rather than prompt guidance.

Selected e-mail uses the existing verified-provider receipt to bind the inbox configuration hash, secret reference and generation to the confirmed plan. The existing materializer's `provider:email` role supplies the API-key file through a read-only core mount. The skipped choice adds no AgentMail environment or mount. The existing provider exposes bounded list/read operations; no sending, new service or separate execution route was introduced. The pre-start configuration check imports and loads AgentMail settings in the actual selected image with networking disabled.

The three direct test diffs were inspected, including selected/skipped projection, materialized role binding, five wrong-owner/audience cases rejected before database/provider access, and missing owner configuration rejected before secret access. The executor reports 40 involved cases ultimately passing across corrected focused runs; earlier fixture/dependency-harness failures are retained by the controller. No tests were rerun by this reviewer, and no inbox read, provider/model call or service start was performed.

No remaining source blocker was identified. Delivery must include the exact corrected provider hash in the target image and the two host tools in the package; updating only installer environment/configuration would leave the old image's privacy gap. This source decision does not claim live inbox access, cloud verification, a newly started consumer, or complete O1.2 acceptance.

Paths below are relative to `eva2/`.

| Product file | SHA256 |
| --- | --- |
| `tools/packaged_installer_target.py` | `083d751a92c7db278562dcce04fbc1e4cda54862cc8e1cb9010852f45a9650ca` |
| `tools/installer_target_bindings.py` | `2a20450ccc5c8004cb641b19a771fc38f9f48595c15fc3bd67522705ee729d8b` |
| `src/eva/providers/agentmail.py` | `601ee78ff4c0f642fbeac372796082e554f330784dad55a7bb366600a1830696` |

| Direct test file | SHA256 |
| --- | --- |
| `tests/operations/test_packaged_installer_target.py` | `185bb5666ba0ef20a599eca8ffb91efe433113419588d752bd092239fcd38bd0` |
| `tests/operations/test_installer_target_bindings.py` | `b04da3d73707adbcedfc52e5566babfe2e985f1dd3790185ac180d2ec294ea7a` |
| `tests/connectors/test_provider_runtime.py` | `076318daa86fd10f354cab9486ffe6d3490181c58919738340d5ee7a7606e127` |

## Installed package and image addendum

**Decision: INSTALLED GO for delivery of the reviewed installer selection/configuration and private-owner provider wiring.** Independently read the three installed package files and their `package-staging.json` entries: all match the frozen hashes above. The existing development-candidate manifest now binds `living-core` to `sha256:759b638cc283465c10c9130f2aa45df44ee24c3f375d39f055a90f15ec046b02`; the existing package consumer uses that manifest's image mapping. Docker inspection confirmed this image exists.

The supplied actual-image compatibility receipt binds that image to provider source SHA256 `601ee78ff4c0f642fbeac372796082e554f330784dad55a7bb366600a1830696`. Its synthetic, network-disabled check reports owner availability, denial of a different owner before dispatch, and absence of an omitted provider, with zero provider/model calls. This existing check was inspected, not repeated.

Independently inspected wizard PID `1074960`: bundled Python runs the existing wizard on port `44727` with the retained workspace and package lock. Its recorded provider-status HTTP 200 response keeps e-mail `skipped`, unconfigured and without a secret, while requiring configuration if selected. The existing browser target was separately inspected as healthy on unchanged image `sha256:e2d6af7ca0cba49761efe54c082ee1fab988f9de06680c01bfaa4fb6469a21d4`.

No remaining package/image binding blocker was identified. This installs the prepared path for new selections; it does not replace the existing target, start an e-mail consumer, access an inbox, establish cloud verification or demonstrate a live e-mail conversation. No tests, provider/model calls or service starts were performed by this review. Actual inbox access and owner acceptance remain separate.

| Evidence | SHA256 |
| --- | --- |
| `installer-selected-email-20261003/package-installed.json` | `6c602ad8216caf766672f8d5439ace42faf6dcc0fccd05325be79088b9f79082` |
| `installer-selected-email-20261003/image/build.json` | `95929699468528ce42da7565112fbf95534fd91b49a14b82f81748ff7549b749` |
| `installer-selected-email-20261003/image/compatibility-result.json` | `fc17cf718faa9089f484994bc678dae175b01619666713c3039291927f8349a6` |
| `installer-selected-email-20261003/provider-status.private.json` | `a8db4b7b487960381c5248f182337cab3dfc77e968209b0c71282854cc3de228` |
| Installed package `package-staging.json` | `c854b4a301f29c1b03b19773d61f3d876a140e2e00bf0c02e21ac3f118320aae` |
| Installed package `release/platform-lock-wsl-candidate.json` | `545836dde8b73d087a2280e19b934a6b1006254a4db7bf74bf9ca488c6c639b0` |

The build receipt's `installed: false` records its preparation-time state; the later package receipt records the manifest/wizard delivery. The image has not been activated as a new live e-mail consumer.
