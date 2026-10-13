# Selected Telegram installer — independent source review, 3 October 2026

**Decision: SOURCE GO for the corrected five-file candidate.** Reviewed against `daf6c5e8`, tracing ordinary selection, configured `getMe` verification, plan evidence, secret and owner-file materialization, and the existing core/Telegram ingress start. The concrete startup defect below is resolved in the final hashes. No provider calls, Telegram messages, poller starts, tests or model runs were performed by this review. Package installation and actual Telegram delivery are separate acceptance steps.

## Resolved startup finding

The initial candidate added `telegram-ingress` to the fresh-start writer services while inheriting the selected core image's HTTP healthcheck. Read-only inspection of image `sha256:e2d6af7ca0cba49761efe54c082ee1fab988f9de06680c01bfaa4fb6469a21d4` showed that check targets `http://127.0.0.1:8080/healthz`, which the native polling ingress does not serve. The initial source decision was HOLD; starting that unchanged candidate would not satisfy readiness.

The corrected target sets `healthcheck.disable=True` only for selected Telegram ingress. Existing `prepare_restore_target` retains the service graph, writes it to `compose.json`, and passes it directly to `docker compose create`; the override is not filtered. The new bounded readiness check requires both planned and inspected entrypoint `/usr/local/bin/python`, command `-m eva.bootstrap.telegram_ingress`, and inspected Docker `Config.Healthcheck.Test=["NONE"]`. It also requires a running, unpaused, non-restarting process with a positive integer PID. Other services without healthchecks remain refused except the existing credential-lease producers.

The existing prepared image, container identity, mounts, network and database-lineage guards remain in force. The receipt now accurately describes Docker health or explicitly bound portless-process readiness, without claiming channel, model or owner acceptance. No new health server or channel-specific execution route is introduced.

## Other reviewed boundaries

The explicit numeric owner account is privately stored and hashed; it is not inferred from the bot identity or first sender. The existing read-only probe performs `getMe` only. The v4 verification receipt binds the owner configuration hash and secret generation; target preparation checks it against the confirmed capability evidence. The existing plan and materializer continue to bind those choices.

The generated v2 binding uses the fresh installation's same private principal and binding IDs, restricted to the chosen user's private chat. Core and ingress receive the same binding file and the existing materialized Telegram secret. Selected Telegram adds only the existing ingress to startup; skipped Telegram remains projected out. The patch adds no proactive service and does not mutate the earlier skipped target. The UI explicitly says bot verification does not prove the user's identity and requests a bot not already used by another Eva.

Read the direct tests for configuration privacy, verification evidence, numeric owner rejection, same-person binding, other-user/group rejection and selected/skipped wiring. The reported earlier 43 focused checks across relevant runs and the final 44 checks in 1.31 seconds were not rerun here. The final readiness cases cover exact native process acceptance and rejection of inherited healthchecks, wrong commands/entrypoints, invalid PID, paused/restarting state and an unrelated portless service.

No remaining source blocker was found in this bounded review. Source GO is not a fresh cloud installation or Telegram conversation result. The existing bot must not acquire a second poller; real provider delivery remains unverified without a separate available bot.

## Exact final reviewed bytes

Paths below are relative to `eva2/`.

| Product file | SHA256 |
| --- | --- |
| `tools/packaged_installer_target.py` | `94f8b759e19564fbe15f6828badc2b8be27f69f962859d39052ec5644ba3c381` |
| `tools/installation_fresh_start.py` | `73d63c4f90b94b634f58259e21fb148fa46aac2c6e8ddbce75fd93c9e195dad3` |
| `src/eva/operations/installer_provider_configuration.py` | `7b2db33d4a1e9c497475433ab1794e293cdbd649902ec99bc6bad126faabede0` |
| `src/eva/adapters/api/installer_provider_api.py` | `d921cf33dd50cb18d9fabf2aac1c131f3553c1763d04654f106a6e90382d4159` |
| `src/eva/adapters/connectors/installer_configured_probe_factory.py` | `1d2f6a71f0870eb5c252f088ff488a6a646fde67dbff67e4f3790f2fe831812a` |

Direct tests: `test_packaged_installer_target.py` SHA256 `001095d6d67ea6ff6f5c1f321a186c6d4d2592f9ff4f4b2edadb5dd684e5344a`; `test_installation_fresh_start.py` SHA256 `7feb310772882cdd4092d17952feb6639b8502565f982a386c2fbf6fe01e7aee`; `test_installer_provider_configuration.py` SHA256 `25a9b4397d40ef1a804c8cf67f628435b855bfcd9745b1c25bf63c54ee0fee46`; `test_installer_provider_api.py` SHA256 `d3a3e348164b0fdf7e5d0a918d93a04c9565193207effed4bd24cb1d825ae808`.

For provenance, the initial target hash at the HOLD decision was `cd91e8bb58ad4b8ee6b911a5f8dc3655d6a1dc8dee26e8938cd3a3fe57ec4518`; the other three initial product files are unchanged.

## Installed package addendum

**Decision: INSTALLED GO for the ordinary installer selection/configuration and reviewed start wiring.** Independently re-read all five installed package files: their hashes exactly match the final source table above. The live wizard PID `910265` runs the bundled Python and existing `tools.installer_restore_wizard` on port `44727`, using the retained workspace and packaged platform lock.

The recorded ordinary provider API returned HTTP 200 and reports Telegram `configuration_required: true`. The earlier target's `skipped` choice remains unchanged, with no Telegram secret or configuration present; its disabled configure/verify actions are consistent with that skipped state. The delivered HTML readback confirms the explicit account-ID and separate-bot instructions. The supplied actual-image compatibility receipt records successful loading of the candidate-generated synthetic v2 owner binding, with zero model/provider calls.

This closes delivery of the reviewed installer wiring. It does **not** establish a started Telegram consumer, live Telegram chat, cloud verification, or an end-to-end fresh selected-Telegram installation. No Telegram poller or cloud request was started for this delivery; the existing core image and bots were retained. A separate available bot remains necessary for live channel acceptance. No additional source blocker was identified, and no further tests, model calls or broad checks were performed for this addendum.

Evidence read from `installer-selected-telegram-20261003/`:

| Receipt | SHA256 |
| --- | --- |
| `package-installed.json` | `f0bd8888044f08dbf0688d3bfca7c0879c214fbafba9dc70e9c8cf4e9dd57005` |
| `live-provider-readback.json` | `9ea20d2892f05b3a3f9fce55e3fd1e4331cba5c096481572f3a2015e7281fb00` |
| `provider-status.private.json` | `573ed568970771498db91941e6916f33e409d560609af24ad8e5c32f52aa8ba9` |
| `image-compatibility/result.json` | `d94fe79dd9e278730542a5e4cbfcc04dd6749eb6a58ccfee41bc1176244691b9` |
| `delivery-plan.json` | `d29c7f6f32ac5d76e686497bea325172c6ac925b2b2d383dc6484caf3038d32d` |
