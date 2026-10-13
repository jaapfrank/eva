# AU-006 existing service recovery — 22 September 2026

The existing document provider and document goal worker are running and healthy again. This recovery starts the two existing containers; it does not install new images or admit a document project. The coordinator independently verified the live container IDs, images, running/healthy states, unchanged Config/HostConfig/mounts, all 28 artifact hashes, both full stored goal states against the historical snapshot, and the journal hash. Workboard integration records recovery separately from new project completion.

## Cause and authority

Both containers recorded exit 255 at 2026-09-14 23:09:34 UTC, coinciding with Docker restoration during the 15 September WSL boot. Their restart policy is `no`. This supports recovery after host interruption; it does not identify the underlying reason for the host interruption. No explicit pause or revocation was found in the reviewed AU-006 handoff, health installation evidence or current AU-006 workboard entries. The coordinator explicitly authorized starting these existing services after checking their configuration and preserved state.

The container IDs and image digests match `document-health-r2-live-20260913.json`. Historical Config and mounts match; historical Docker DNS null values have become empty lists. The apparent catalog mismatch was a comparison against the core catalog hash: the worker has a separate historical project catalog. The core catalog still matches its expected hash. Mandate, endpoint, token and plan hashes match the historical readback. The provider fixed IP was available, and the existing database and gateway dependencies were running.

## Recovery and verification

Started `eva2-document-workspace-au006`, passed its existing HTTP health and MCP `tools/list` metadata probe, then started `eva2-document-goal-worker-au006` and passed its existing process, plan and scoped database probe. Docker independently recorded healthy results for both services. The readback used a read-only database transaction with a statement timeout.

The attached evidence records exact IDs, images and health results. Config, HostConfig and mounts are unchanged before/after recovery (mounts compared by destination). Both goal states equal the September 13 snapshot: the earlier attempt remains `needs_help`, and the successful r2 project remains `completed`. All 28 historical artifact hashes and the native journal hash match. No model calls, document tool calls, new projects, migrations, recreations or deletions were performed.

## Operation and rollback

Existing service health can be inspected with `docker inspect` for the two names above. No repeat document execution is needed to try this recovery: the completed project's existing files remain available. Owner evaluation of their usefulness remains open.

The rollback for this recovery is `docker stop eva2-document-goal-worker-au006`, followed by `docker stop eva2-document-workspace-au006`. It returns these services to their prior stopped state while preserving images, configuration, project states, files and journal. Do not reset goals or replay old operations.

Open operational defect: restart policy remains `no`, so a future host restart can leave these services stopped again. Changing persistence behavior was outside this bounded existing-container recovery. Health proves process/configuration/database/MCP metadata readiness, not new task completion or general multi-day autonomy.
