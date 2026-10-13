# Continuous speaker service activation handoff

This prepares software only. No firmware is flashed, no core/worker configuration
is changed, and no owner-media, LiveKit or Windows firewall service is changed by
preparation. Device-specific USB identification, full-flash backup and verified
physical recovery remain separate prerequisites before firmware installation.

## Exact candidate

- Source reference: `c7f4804913678e6ab4931a0ed84a3c598835ab94` plus this deployment delta.
- Private stage: `/var/lib/eva2/native-speaker/candidates/c7f48049-service-v4`.
- Corrected image:
  `sha256:1e3445797bcdc1afe350370c14d91f07ccb1bc818ea9fa1a8d1af737d9ec1aba`.
- Parent image:
  `sha256:1aab254f21a58cf97844679f2b0a0f82ea6507989badf3229796fb471193dd51`.

The parent image's `/app/deploy/home-assistant/custom_components/eva2_voice/client.py`
was mode 0600/root and failed under UID 65532. The earlier root-only config test
was insufficient. `Dockerfile.permissions` corrects this file to 0644; its directory
chmod operations are idempotent (already 0755). No file content or dependency
changes. Before rebuilding, verify the Dockerfile's retained local parent tag
resolves to the exact parent ID above; do not substitute an updated tag. The
resulting candidate is referenced by immutable ID in Compose and the stage.

The service uses UID/GID 65532, a read-only root filesystem, dropped capabilities,
no new privileges, private tmpfs credentials and the existing speaker launcher.
There is no published Docker port or new SFU. Host networking binds the existing
TLS endpoint `10.0.20.46:44715`. The only LAN sources to permit are Woonkamer
`10.0.20.202` and Overkapping `10.0.20.53`.

`household-voice` and `identity` are read-only **directory** mounts of the current
authoritative core paths. Policy, household token and identity replacement remain
visible. Runtime validation fails closed on changed/revoked authority; restart the
speaker after a coordinated valid rotation to load the new snapshot. Account
tokens are read on startup into a UID-65532-only tmpfs file, never persisted in the
template or printed. HA token is a single-file mount to avoid exposing other core
secrets; after replacing that file atomically, recreate the speaker container to
refresh this particular mount. Policy/household-token/identity do not have that
single-inode limitation. Owner credentials are never mounted.

## Activation after review

Use the existing root management route. The core's ongoing asset update and other
coordinator releases must finish first. Use the coordinator's existing quiescence
and release checks before any core restart. This package does not replace those
checks or restart the worker.

1. Set `STAGE` to the exact private stage above. Run:
   `sudo -n python3 "$STAGE/deploy/activation.py" capture --stage "$STAGE"`.
   This is the first time the rollback baseline is captured. It copies current
   Compose inputs/environment into a fresh private activation directory, pins
   the **then-running** core image, verifies the rendered rollback environment
   matches that runtime, and saves the owner media/SFU container identities.
   It refuses to adopt an existing speaker container. Keep the returned baseline
   path as `BASELINE`; nothing has been activated yet.
2. Run `activation.py check --stage "$STAGE" --baseline "$BASELINE"` through
   the same root route immediately before changing the core. Inspect the private
   `core-commands.json` without publishing its backups. Its `enable` argv applies
   only `EVA_HOUSEHOLD_VOICE_SESSION_ENABLED=true` to service `core`, pins the
   captured image, uses `--no-deps --pull never`, and leaves worker/owner-media/SFU
   alone. The coordinator executes this argv through its reviewed release
   procedure. The static `core-flag.compose.json` documents the exact delta;
   do not use it against an assumed historic image or project.
   **Before either generated core argv is executed, the coordinator's existing
   release validator must compare the complete effective core configuration with
   the captured running core, including volumes/bindings, ports, command and
   entrypoint, user, capabilities/security options, networking and resources.**
   `baseline.json` contains the actual inspection and `core-restore.rendered.json`
   contains the full Compose projection. The expected enable delta is the one
   flag only. This helper's environment/provenance checks are not that complete
   comparison: a pending Compose edit to ports or security must be rejected by
   the release procedure, not carried into either enable or restore.
3. Run `activation.py start --stage "$STAGE" --baseline "$BASELINE"` through
   the root route. It rechecks image, environment, mount and source-file drift,
   verifies both authenticated household readiness endpoints (no model call),
   and starts only the speaker service. It checks UID/image and the existing
   launcher's listening event. Startup failure removes only this stage's speaker
   container. Core flag restoration remains the coordinator's next step below.
4. On the existing elevated Windows PowerShell management route, run
   `network-route.ps1 -Action Apply -BaselinePath <new-private-json-path>`.
   The script captures the actual firewall baseline at this moment, then adds or
   enables only `Eva2-ContinuousSpeaker-Devices`, TCP from the two exact IPs to
   `10.0.20.46:44715`. It rejects any same-name rule with different bindings.
   No port proxy, public listener, default-policy change or general subnet rule
   is required. Windows may classify a WSL UNC script as remote; run the reviewed,
   hash-checked file from a normal local management directory, without changing
   execution policy.
5. Verify the installed container's read-only mounts, UID, image and service log,
   and the exact network rule. No genuine speaker session is proven until its
   device-specific firmware and physical trial are separately completed.

Execute argv arrays directly, not by joining them into a shell string. Baseline
files contain private runtime configuration and must remain root-only. Re-run
capture after any conflicting release rather than forcing an old baseline through.

## Rollback

1. Run `activation.py stop --stage "$STAGE"` through the root route. It removes
   only the matching image/project/stage speaker container; it refuses to stop a
   replacement installed from another stage.
2. Run `network-route.ps1 -Action Rollback -BaselinePath <the-same-json-path>`
   through the elevated route. It removes a newly created rule or restores the
   original enabled/disabled state of an exact pre-existing rule. It never
   restores or removes unrelated firewall policy.
3. Run the baseline `check` again. If a later core release changed image,
   environment, source files, mounts or owner voice, stop and reconcile with the
   coordinator instead of overwriting it. Otherwise execute the saved `restore`
   argv using the same coordinated core-release procedure. This restores the
   actual previous flag value and image, not an assumed disabled flag or old
   image from this document. Confirm core readiness and existing HA/owner routes.

The scripts neither alter core data nor restore databases. Firmware rollback is
the separate per-device physical procedure in `README.md`. No additional general
account or management credentials are needed; the only remaining external input
for hardware deployment is physical device access.
