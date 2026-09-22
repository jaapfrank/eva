# Document service recovery

The document provider and document worker use `restart: unless-stopped` in
`document-health.compose.json`. Include that overlay after the existing document
runtime overlays. Keep the existing image pins, authority files, project data and
journal. Explicitly stopping a container remains respected by Docker.

For an existing installation, changing only the restart policy does not require
recreating or restarting either container. Verify the current container IDs and
images, save the effective Compose configuration and overlay, update that same
overlay, and check that the effective configuration changes only the two restart
policies. Apply `docker update --restart unless-stopped` to the verified IDs.
Check unchanged IDs, images, start times, mounts and configuration, plus healthy
services. Do not reconstruct the installation from an inspect dump.

Rollback restores the exact saved overlay and sets both existing containers back
to their previous restart policies. Attempt each restoration even if another
fails, then inspect the actual resulting state. Do not delete projects or replay
completed operations as part of recovery.

This policy keeps service processes available after Docker or host interruption;
it does not prove that every interrupted task resumes. The current document
worker can mark a waiting project `needs_help` when its provider is unavailable
during startup. Dependency recovery and the real host-restart trial remain open
product checks. Health proves readiness, not progress or owner acceptance.
