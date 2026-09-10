# VB004: restart the existing browser controller and restore its firewall

This extends the existing owner-browser deployment materials without replacing its images, containers, profile, credentials, or session ledger. Two system services make the host controller restartable. They do not run a browser task, start the container pair, replay a tool call, or accept an uncertain previous action.

`eva-owner-browser-firewall.service` runs root-owned `/usr/local/lib/eva-owner-browser/recover_firewall.py` with root-owned `/etc/eva/owner-browser/firewall.json`. A root-owned SHA-256 manifest pins the helper, configuration and both unit files. Every parent of the executable/configuration must also be root-owned and not group/world-writable. Merely changing a script's owner inside a writable home directory is insufficient. The helper imports only the host Python standard library with `-I`; it never runs an interpreter or script inside the browser container.

The root helper checks the exact retained IDs, images, shared namespace, profile mount, network IDs and source addresses. It restores the same IPv4 chains and exact-source hooks used by the delivered deployment. Existing exact rules are retained; an interrupted exact prefix can be completed; different rules or displaced source hooks fail visibly rather than flushing unrelated policy. Docker networks must still have IPv6 disabled. For a running browser, root-owned host Python briefly enters only that process's network namespace to read actual IPv6 state. For a stopped browser, retained static IPAM and disabled-IPv6 network settings allow installing protection before the next explicit owner start. No automatic browser start is used to make the guard pass.

`eva-owner-browser.service` runs as existing `administrator` UID 1000, with Docker group access and the existing private host configuration, token files, profile and state directory. Requires/After order it after the firewall and Docker; BindsTo/PartOf couple its lifecycle to those services. A Docker or firewall-service restart therefore stops the host controller and restarts it only after firewall readiness. No ExecStop removes the firewall. Browser/MCP container restart policies remain `no`.

The controller's existing on-disk revision and StartedAt checks remain authoritative. A mismatched or interrupted state is shown as `recovery_required`; restarting the service does not invent a successful transition. The owner can observe and explicitly take over or stop. Existing durable MCP invocation records remain responsible for effect recovery; systemd does not redispatch them.

## Stage without installing

Run `prepare_recovery.py` as the operator with:

```sh
sudo python3 deploy/owner-browser/recovery/prepare_recovery.py \
  --deployment-directory /absolute/private/existing-browser-directory \
  --host-source-directory /absolute/path/to/eva2 \
  --python /absolute/path/to/existing/venv/bin/python \
  --output-directory /absolute/private/new-recovery-staging-directory
```

The deployment directory contains the existing `host-config.json`. Source/Python should match the currently delivered host controller. Staging reads Docker metadata and preserves the original UID-1000 private files. It writes a new root-owned staging directory and a receipt with exact destinations/hashes; it does not install services, start or stop processes, or write firewall rules. No new credentials are generated. A newer host origin after an A/B core switch must already be reflected in the existing host configuration before staging.

## Activate after reviewing the staged receipt

1. Preserve any prior unit/root-configuration files and their metadata. Confirm the existing standalone controller's PID, UID 1000, full command and config path before replacing its process supervision. Do not signal a guessed PID or launch a second manager; the existing lifetime lock rejects overlap.
2. Create root-owned non-writable-by-users directories `/usr/local/lib/eva-owner-browser` and `/etc/eva/owner-browser`. Install staged `recover_firewall.py` at its recorded destination as root:root mode 0444, `firewall.json` and `recovery.sha256` as root:root mode 0600, and both system-unit files as root:root mode 0644. Keep all root-executed paths outside user-writable parents. Use the exact destination map and hashes in the staging receipt.
3. Run `systemctl daemon-reload`, then `systemctl start eva-owner-browser-firewall.service`. Require success and inspect `/var/lib/eva-owner-browser/firewall-recovery.json` before replacing the old host process. This first step does not interrupt browser/MCP containers. A rule or binding mismatch leaves the existing service/process under operator control and requires investigation.
4. Gracefully terminate only the verified standalone controller, wait for its PID/socket/lock to close, then `systemctl enable --now eva-owner-browser.service eva-owner-browser-firewall.service`. The host process must remain UID 1000; root execution would correctly fail the existing private-file ownership checks. Read actual service status and the authenticated dashboard browser status.
5. Perform the one retained-profile check: keep an owner-created marker in the existing browser profile, record exact container IDs and session state, restart only `eva-owner-browser.service`, and confirm the same marker/profile/IDs and a truthful session state. For dependency ordering, `systemctl restart eva-owner-browser-firewall.service` must restore the guard before the dependent host starts. An unknown browser state must stay visible and produce no automatic MCP start or tool effect. No model or public-web task is needed.

This proves service/firewall restoration for the current installation when actually executed. It is not a claim that a full host reboot was tested. Docker must be the existing system service and the network/container identities must remain the retained pair; absent or replaced containers fail closed and require explicit reprovisioning through the prior maintenance recipe.

For supervision rollback, disable/stop only the host controller service, retain the firewall protection, and restore the prior verified standalone launch as UID 1000 against the same private state. Do not run the old firewall rollback while the pair can browse. Preserve recovery receipts and backups instead of resetting session state or owner revocation.
