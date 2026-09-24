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

## Document project preparation

With both `recovery_materialization` and `external_recovery` configured, the
launcher can also accept `document_recovery`, containing a `providers` list.
Each entry selects existing package paths and an explicitly pinned native
document image:

| Field | Value |
| --- | --- |
| `original_binding`, `catalog_path` | Relative files under `config/` with provider binding/catalog roles |
| `document_directory`, `journal_directory` | Separate relative directories under `documents/` |
| `logical_catalog_sha256` | Canonical hash of the preserved provider catalog |
| `project_keys` | Exact list of project hashes present in the journal directory |
| `runtime_uid`, `runtime_gid` | Owners of the staged document and journal directories |
| `image` | Locally available `sha256:` image identity with the existing native relocation implementation |
| `output_directory` | New absolute directory outside the package, beneath an operator-owned private parent |

The wizard performs this preparation after the external imports. It invokes the
existing native relocation implementation with read-only package mounts and no
network, then preserves the binding and project snapshots in the new directory.
The same restore authority and runtime guard apply before and after preparation.
The receipt says `prepared_not_connected`: it does not start providers, register
them with the gateway or release the restored installation. Provisioning must
connect these exact outputs and verify the preserved project results before
activation. No document preparation occurs without this optional configuration.

## Read-only document connection

An optional `document_connection` configuration can follow `document_recovery`:

```json
{
  "providers": [{
    "preparation_index": 0,
    "container_name": "eva-restored-document-check",
    "network_id": "<full ID of an existing internal Docker network>",
    "jwt_secret_file": "/absolute/fresh-secrets/jwt",
    "encryption_secret_file": "/absolute/fresh-secrets/encryption",
    "admin_password_file": "/absolute/fresh-secrets/admin"
  }]
}
```

Include every preparation provider exactly once. Each target name must be new;
an existing target is retained for inspection rather than replaced. Secret files
must be separately provisioned outside the package and preparation output, owned
by the configured runtime UID/GID, mode `0400`, without line breaks. They are not
restored from the archive. Images and document paths come from the verified
preparation configuration, not from this connection request.

The wizard starts the native provider with read-only document, journal, binding
and snapshot mounts on the pinned internal network, with no published ports.
It checks the actual container and obtains `tools/list` using the existing MCP
SDK. The returned catalog must match the preserved canonical catalog hash.
`provider_verified` covers that startup/catalog check only. The same read-only
target then reruns the existing native relocation preparation against every
preserved project. The native journal parser observes the actual retained files;
generated binding and snapshot bytes must equal the mounted preparation exactly.
Changed material, history, physical binding or ambiguous pending effects cause
failure without replaying an operation. Only after this check does
`documents_verified` become true, scoped to native retained material/history.
It does not certify the quality of the original task or a full conversation.

The receipt is `connected_read_only`, and the core stays held. This step neither
registers or remaps the gateway nor enables document writes. Gateway mapping,
writable runtime activation and the full restore trial remain requirements
before ordinary use.

## Partial results and activation

The core PostgreSQL transaction, external PostgreSQL transaction and gateway file
publication cannot form one distributed transaction. A later error can leave a
completed or uncertain external import even when the core transaction rolls back.
Those bytes are retained; the wizard reports that the targets need inspection.
Document preparation receipts, including partial outputs, remain attached to
the operator exception; private paths and receipts are not exposed in the HTTP
error. The browser explicitly reports when document preparation was attempted.
If connection fails, its named read-only targets can already exist or be running.
The connection receipt remains on the operator exception and the browser says
that target inspection is required. No retry, deletion or core activation occurs.
Do not retry against the same nonempty targets or delete them automatically.
The original installation is the fallback until the restored target is ready.

Import does not start services or release the core hold. Document provider
mapping, required separate secrets, component readback and deliberate activation
remain required. This delivery does not claim a complete relocation, a successful
restore trial, or a GUI-only fresh-machine installation.
