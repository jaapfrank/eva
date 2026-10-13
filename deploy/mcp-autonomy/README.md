# ContextForge MCP connection pinning

Offline opt-in patch for image `daf923c247fd` (ContextForge 1.0.8). The installer
requires exact SHA256 source files; retain its output hashes with the deployed
read-only file overlay. ContextForge is Apache-2.0; this patch does not redistribute its files.
The new integration code belongs to Eva. Existing upstream URL validation and
IP/Host/SNI rewriting are called directly, not copied.

`prepare.py ROOT` patches four existing HTTP client factories: gateway health,
registration, legacy SSE (shared factory), and tool invocation. Upstream session
registry acquisition receives the invocation factory; every request through an
existing pooled client is pinned again. No global HTTP monkeypatch is installed.

Set `EVA_MCP_PINNING_POLICY` to JSON with exactly `owner_networks` (the owner's
private CIDR scopes, currently `["10.0.20.0/24"]`), `blocked_addresses` (all gateway/management host addresses),
`filesystem_origin` (the existing HTTP hostname and port, no path), and
`filesystem_address` (that container's exact current private IP). Only this
origin may use that filesystem address. Other destinations permit public HTTPS
port 443 and the owner LAN except explicitly blocked management addresses.
Keep the upstream SSRF validator enabled. Ensure its policy also permits those
destinations; its restrictions are additional, never bypassed. No environment
proxy, disabled certificate verification, or redirect following is allowed.
HTTP keepalive and HTTP/2 multiplexing are disabled for this client so two
original hostnames sharing one pinned IP cannot reuse each other's TLS session.
The MCP session registry remains reusable; each HTTP exchange is revalidated.

Absence of this environment setting preserves upstream behavior. Invalid policy
fails client creation; DNS or destination failure prevents the HTTP request.
Container IP replacement requires a new bound policy/readback. This is source
preparation, not proof of deployed egress policy or a live registration.
