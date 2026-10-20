# Optional programming workspace

DesktopCommander MCP 0.2.52 supplies file, terminal and process tools. Eva keeps
its existing model, durable task, identity, cancellation and result lifecycle.
This is an executor, not an additional agent framework or a complete release host.

`Dockerfile` and `package-lock.json` pin the upstream distribution and base images.
`upstream.json` records the npm tarball integrity. `host-requirements.lock` pins
the MCP Python SDK in a separate host-service environment; core dependencies do
not change. The image contains read-only Eva source and installed Python libraries
from the recorded reference image. No private runtime mounts are copied.

Configure `programming_workspace` in the existing component evaluation service:
immutable image ID, absolute `workspace_root` and `receipt_root`, and appropriate
resource limits. The host must be able to create task directories owned by UID
10001 and manage the exact isolated containers. Use the isolated host environment
and packaged service files, not a mutable source checkout. The service's existing
bearer token also protects `/programming`.

Set `EVA_PROGRAMMING_WORKSPACE_CLIENT_FILE` in both core and delegated worker to a
read-only JSON file with `url`, absolute `token_file`, and `timeout_seconds`.
Both sides must use matching code/configuration fingerprints. The default remains
disabled. The installed client refers to the existing evaluation token, not a new
owner login or provider. Host and core response bounds should match.

The container has no network, host credentials, Docker socket, independent tests
or release authority. It has one persistent `/workspace`; its other filesystem
is read-only except bounded temporary storage. Trusted task identities choose
workspace/session keys. Explicit resume retains files but uses a new session;
uncertain command receipts are not blindly replayed. Closing/cancelling the task
terminates its container. On host restart, reconcile only this root's exact owned
containers before allowing another attempt to use the same workspace.

Prefer the typed `write_programming_file(path, content, mode)` and
`start_programming_process(command, timeout_ms)` descriptors for source text and
commands. They pass native arguments to the same pinned MCP `write_file` and
`start_process` operations; source code has only the provider's ordinary JSON
encoding layer. `timeout_ms` is the initial output wait, not a process deadline.
Use the existing discovery/general call for readback, process output and other
operations. Legacy `arguments_json` calls and durable receipts remain supported.
The aliases preserve task admission, request identity, stop and host isolation;
both runtime services need the matching descriptors and source.

A successful MCP call does not prove completed work. Before task completion, the
host queries the upstream process registry; running processes prevent success.
Idle state is lifecycle evidence only. Eva must inspect actual program results;
independent assessment and any live application remain separate trusted steps.
The model cannot modify the evaluator or its protected holdout.

Upstream: https://github.com/wonderwhy-er/DesktopCommanderMCP (MIT).
Maintenance: project coordinator; verify the exact lock, compatibility and relevant
advisories at the next dependency change. Upstream activity alone is no security
or quality guarantee. Network-enabled dependency retrieval and automatic channel
activation are not supplied by this workspace.

## Optional Python semantic edit feedback

Set trusted `programming_workspace.python_diagnostics` to `true` with an image
containing the pinned `python-language-server` distribution. The default is false;
`diagnostics_timeout_seconds` defaults to 5. The existing sandbox runs the fixed
`pyright-langserver --stdio` process as its existing UID, without network or a new
agent. `python-language-server/upstream.json` and its npm lock record Pyright and
bundled license provenance. The Linux build omits the macOS-only optional fsevents.

After direct `write_file` or `edit_block` calls on canonical `/workspace/*.py`
paths, `eva_diagnostics` adds before/after source hashes, monotone document versions
and new, preexisting and resolved diagnostics to the ordinary MCP result. Diagnostic
text remains untrusted. Shell-created edits are outside this first automatic hook.
Missing server, timeout, versionless/stale feedback, oversized or unsafe sources
are unavailable, never a clean check. Oversized optional feedback becomes a compact
unavailable summary or is omitted if even that cannot fit the existing result bound;
an acknowledged write is not retried because diagnostics could not be returned.

Receipts retain the exact returned feedback across replay. Closing/cancelling the
existing session removes its language-server child with the same sandbox. Semantic
feedback helps Eva revise code before independent final checks; it grants no extra
capability and does not prove runtime correctness or installation.
