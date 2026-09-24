# Installation package recovery

The existing restore wizard accepts a validated `EvaInstallationRecovery/v1`
package when its private operator configuration includes `recovery_materialization`.
The ordinary core-only restore path stays available. Use a separate, stopped
restore target; this workflow does not restore over a running installation.

## External component imports

The private launcher configuration may additionally supply `external_recovery`:

```json
{
  "memory": {
    "container": {"name": "target-memory-db", "id": "<full-container-id>", "image": "sha256:<image-id>"},
    "database": {"name": "hindsight", "oid": 12345, "system_identifier": "<database-system-identifier>"},
    "user": "<database-role>"
  },
  "gateway": {"target_path": "/absolute/new-target/contextforge.sqlite"},
  "writers": [
    {"name": "target-memory-service", "id": "<full-container-id>", "image": "sha256:<image-id>"},
    {"name": "target-gateway-service", "id": "<full-container-id>", "image": "sha256:<image-id>"}
  ]
}
```

These values belong to trusted local provisioning, never the archive or HTTP
request. Configure every external writer and keep it stopped. The memory database
container itself must be available for its database tools. The database must be
empty and match its configured identity. The gateway target file must not exist.
The package supplies data; it cannot select a shell command or destination.

The existing restore action first restores the core and stages package files.
If external recovery is configured, it imports the fixed memory dump and copies
the verified SQLite database to the new target. The core restore remains held.
Source and target writer checks are repeated through the existing runtime guard.
This is a point-in-time check, not a lock against another host operator.

## Partial results and activation

The core PostgreSQL transaction, external PostgreSQL transaction and gateway file
publication cannot form one distributed transaction. A later error can leave a
completed or uncertain external import even when the core transaction rolls back.
Those bytes are retained; the wizard reports that the targets need inspection.
Do not retry against the same nonempty targets or delete them automatically.
The original installation is the fallback until the restored target is ready.

Import does not start services or release the core hold. Document provider
mapping, required separate secrets, component readback and deliberate activation
remain required. This delivery does not claim a complete relocation, a successful
restore trial, or a GUI-only fresh-machine installation.
