# Local owner dashboard

On 11 September 2026 Jaap requested that dashboard tokens and interactive login
stop interrupting his work. `local-owner.mjs` serves the existing owner dashboard
at http://127.0.0.1:44711/internal/v1/dashboard without a login prompt.

The service binds only to IPv4 loopback and accepts local Host values, same-origin
requests and dashboard paths. It forwards HTTP streams and WebSocket upgrades to
the existing core on 44710. The existing core credential is supplied privately by
systemd LoadCredential; no token is sent to the browser. Core authentication and
the owner binding are unchanged. No core restart is needed.

Install `eva2-local-owner.service` into `/etc/systemd/system`, run daemon-reload,
then enable and start that service. Stop and disable it to undo the temporary
local entry point. Its credential path follows managed-b-slot3; update that path
and the upstream port together when switching the active core slot.

Verification: Windows unauthenticated HTML 200, real Chromium page displays
Jouw geheugenbronnen, dashboard status 200, non-dashboard path and foreign Origin
403. The existing browser/status endpoint returns 503 independently of access.
Owner acceptance of memory behavior is still pending.

## Windows project dashboard address

The project dashboard on port 44712 remains the existing WSL user service.
Windows can resolve `localhost` to `::1` before `127.0.0.1`. On this installation,
Windows IPv6 loopback requests time out while IPv4 requests work. Use
http://127.0.0.1:44712/ for the project dashboard.

Copy `start-project-dashboard.ps1` to `%LOCALAPPDATA%\Eva2\dashboard` and run it
with a hidden window. It starts WSL and the existing project dashboard service.
A Windows Startup shortcut to that script restores the same address after sign-in.
The launcher does not replace Eva,
the dashboard build, its project-state source or the model provider.

Streaming responses flush filtered upstream headers immediately, before any body
chunk. This lets the existing input receipt arrive while Eva is generating the
answer. Verify with `node --test development/tests/api/test_local_owner_streaming.mjs`
from the repository root. A flushed status does not prove a completed answer;
conversation clients still require the final saved-reply stream record.
