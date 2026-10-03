# Managed owner browser: delivered deployment materials

This directory preserves the non-secret inputs used for the VB003 browser pair. It is a maintenance recipe, not an installer or a new release system. No new build, container recreation, or test was performed while recording these materials. `provenance.json` records upstream image/source pins and the hashes of the copied files.

The runtime has two containers: LinuxServer Chromium/Selkies and official Playwright MCP. MCP joins **the browser's exact container network namespace** and talks to loopback CDP. The browser profile is persistent. Eva's core receives neither Docker authority nor a new browser execution adapter; the existing MCP connector handles its tools. Owner controls use the existing `eva.operations.browser_session` host controller and the dashboard proxy.

## Retained private state

Keep these outside the repository: `profile/`, `state/`, `secrets/`, `viewer.htpasswd`, `host-config.json`, `container-ids.json`, rendered argv, and all logs. The four original secret files are `control-token`, `viewer-password`, `viewer-master-token`, and `viewer-session-token`. Preserve them and existing profile/state during maintenance. Do not regenerate them as part of a restart. The runtime also needs the root-owned copy `viewer-master-token-root` described below.

A maintenance directory's basename is its instance name. The network helpers intentionally act on only that directory's two named networks and recorded full container IDs. Copy the exact helper files into that private directory before using them; do not run them directly from this repository directory.

## Fixed pins and native source change

Images are immutable references in the argv templates and `provenance.json`; the templates use `--pull never`. There is no custom Dockerfile. Provision the pinned images through the existing operator image process before container creation.

The hash-bound Python change inside the browser image is `selkies-auth.patch`, applied to `/lsiopy/lib/python3.13/site-packages/selkies/selkies.py` **before the browser starts**. `selkies-auth-source.json` records both original and final SHA-256 values. Reconstruct it from the stopped, pinned container rather than relying on an untracked modified module:

```sh
docker cp "$BROWSER_CONTAINER_ID:/lsiopy/lib/python3.13/site-packages/selkies/selkies.py" "$DEPLOYMENT_DIR/selkies-upstream.py"
printf '%s  %s\n' 660a578fc9fca69d2cd06580f1e8bf216ddfe1e2994f3c2eb1acbadeb113d5de "$DEPLOYMENT_DIR/selkies-upstream.py" | sha256sum --check
patch --output "$DEPLOYMENT_DIR/selkies-auth-patched.py" "$DEPLOYMENT_DIR/selkies-upstream.py" < "$RECIPE_DIR/selkies-auth.patch"
printf '%s  %s\n' 79e757b001f65213d5db552629a0f04c7ddcb42506fcde9011e683aee23b4bed "$DEPLOYMENT_DIR/selkies-auth-patched.py" | sha256sum --check
docker cp "$DEPLOYMENT_DIR/selkies-auth-patched.py" "$BROWSER_CONTAINER_ID:/lsiopy/lib/python3.13/site-packages/selkies/selkies.py"
```

An already patched stopped container must match the final hash; do not apply the patch twice. Container recreation loses this copied module, so repeat this bound source step on each new container. Do not replace a mounted source file under a running container.

The patch accepts the private session credential through `X-Eva-Browser-Session`, omits the master secret from browser settings, redacts it in the startup log, and reads it once from inherited FD 3. The exact `selkies-run` opens `/run/eva-browser/master-token` while still root and passes only the open descriptor through `s6-setuidgid abc`; Python consumes and closes that descriptor. No master secret is placed in the exec environment. `autostart` additionally removes legacy secret environment names before starting sandboxed Chromium.

For a new private state directory, the operator supplies four independently generated credentials of at least 32 characters in mode-0600 files owned by the controller user. Existing installations retain their original files. Run `sudo python3 "$DEPLOYMENT_DIR/provision-master.py"` to create/verify the identical root-owned mode-0400 master copy. The helper does not print its contents. Generate the private Nginx password hash without placing the password on a command line:

```sh
openssl passwd -apr1 -stdin < "$DEPLOYMENT_DIR/secrets/viewer-password" > "$DEPLOYMENT_DIR/viewer-password.hash"
{ printf 'eva-browser-owner:'; cat "$DEPLOYMENT_DIR/viewer-password.hash"; } > "$DEPLOYMENT_DIR/viewer.htpasswd"
chmod 0444 "$DEPLOYMENT_DIR/viewer.htpasswd"
```

Keep that hash inside the private directory. Chromium and renderers run as UID/GID 1000, with no effective capabilities, `NoNewPrivs=1`, and their own namespace/seccomp sandbox. The browser container's init process retains the upstream permissions required to initialize the desktop. `seccomp-browser.json` is the pinned Moby profile plus the documented namespace rule, with the precise baseline/delta limitations in `seccomp-delta.json`. It is not `seccomp=unconfined` or `--no-sandbox`.


## Viewer-independent native frame callbacks

`pixelflux-idle-source.json` pins one upstream wheel for the existing CPython
3.13/Linux x86_64 container. Its source supplies idle Wayland frame callbacks
without a viewer or capture stream while retaining the existing Selkies settings
and JPEG/H264 wire format. This records a reproducible dependency correction;
live browser/task results and owner acceptance are separate evidence.

**The version string `2.1.0` is insufficient.** Use the exact `f9d49bf` release
asset and SHA below. The later PyPI 2.1.0 wheel changes settings and the video
header. It is not interchangeable with this artifact.

Apply during operator maintenance with no admitted browser work or active viewer.
Keep the recorded container IDs, profile, network, controller state and auth
overlay. The container must be running for `docker exec`; restart its already
loaded Selkies process through controller stop/start immediately after installation.
Do not admit new browser work in between. If the final extension hash already
matches the pin, retain that installation and its original backup.

Use operator-chosen `DEPLOYMENT_DIR` and the existing full `BROWSER_CONTAINER_ID`.
The backup directory below must be new; never overwrite an earlier backup.
Run with failure propagation, so a failed hash or copy stops the procedure.

```sh
set -eu
WHEEL=pixelflux-2.1.0-cp313-cp313-manylinux_2_28_x86_64.whl
SITE=/lsiopy/lib/python3.13/site-packages
NATIVE_BACKUP="$DEPLOYMENT_DIR/pixelflux-before-f9d49bf"
mkdir -m 0700 "$NATIVE_BACKUP"
curl --fail --location --output "$NATIVE_BACKUP/$WHEEL" \
  "https://github.com/selkies-project/pixelflux/releases/download/f9d49bf/$WHEEL"
printf '%s  %s\n' 8583b1baf6119d38908a353affc7c6e7efca518009dc694fa78814adf0d30971 \
  "$NATIVE_BACKUP/$WHEEL" | sha256sum --check

docker cp "$BROWSER_CONTAINER_ID:$SITE/pixelflux.cpython-313-x86_64-linux-gnu.so" "$NATIVE_BACKUP/"
docker cp "$BROWSER_CONTAINER_ID:$SITE/pixelflux.libs" "$NATIVE_BACKUP/"
docker cp "$BROWSER_CONTAINER_ID:$SITE/pixelflux-2.0.0.dist-info" "$NATIVE_BACKUP/"
printf '%s  %s\n' ce60a6ccaa2a35695c3b8bacf168e1db6c969d97e5b3aca526e8063ed635b29f \
  "$NATIVE_BACKUP/pixelflux.cpython-313-x86_64-linux-gnu.so" | sha256sum --check
docker cp "$NATIVE_BACKUP/$WHEEL" "$BROWSER_CONTAINER_ID:/tmp/$WHEEL"

# Separate preflight target; the active package remains unchanged.
docker exec --user 0 "$BROWSER_CONTAINER_ID" test ! -e /tmp/eva-pixelflux-f9d49bf-preflight
docker exec --user 0 "$BROWSER_CONTAINER_ID" /lsiopy/bin/python3 -m pip install \
  --no-deps --no-index --target /tmp/eva-pixelflux-f9d49bf-preflight "/tmp/$WHEEL"
docker exec --user 0 "$BROWSER_CONTAINER_ID" /lsiopy/bin/python3 -c \
  'import sys; sys.path.insert(0, "/tmp/eva-pixelflux-f9d49bf-preflight"); from pixelflux import CaptureSettings, ScreenCapture, StripeFrame, ensure_wayland_display; s=CaptureSettings(); assert s.output_mode == 0 and s.use_openh264 is False; s.output_mode=1; s.use_openh264=True; assert all(hasattr(ScreenCapture,n) for n in ("start_capture","stop_capture","set_cursor_callback","inject_key","inject_mouse_move","inject_mouse_button","inject_mouse_scroll")); assert callable(ensure_wayland_display); print("Native API import passed; no display started")'

# Only the verified local wheel; no dependency resolution or image replacement.
docker exec --user 0 "$BROWSER_CONTAINER_ID" /lsiopy/bin/python3 -m pip install \
  --no-deps --no-index --force-reinstall "/tmp/$WHEEL"
docker exec "$BROWSER_CONTAINER_ID" /lsiopy/bin/python3 -c \
  'import hashlib,pathlib,pixelflux; p=pathlib.Path(pixelflux.__file__); assert hashlib.sha256(p.read_bytes()).hexdigest()=="7d4fd0db93e48fd4e83eed1b4f6407f914f646d08c7a562b485d6d32ab89f281"; print("Pinned native extension verified")'
```

Stop and start through the **existing controller**, reading a fresh status/revision
before each action. GET `/internal/v1/dashboard/browser/status`; POST
`/internal/v1/dashboard/browser/control` with
`{"action":"stop","revision":REVISION}` and then
`{"action":"start","revision":NEW_REVISION}`. Use the existing private controller
client/credential. Do not print tokens, use raw `docker restart`, or automatically
resume owner/recovery states. Confirm both containers and controller return healthy
in `eva` state.

Check animation frames and an ordinary Playwright click without a viewer, using a
fresh snapshot and actual destination readback. Keep those results separately.
No force-click, Computer Use API, Chromium flag change, or owner-tab sharing is
part of this correction.

Keep the old extension, native libraries and dist-info in `NATIVE_BACKUP`; they
are recovery assets, not an automatic rollback instruction. Container recreation
loses this overlay: repeat the exact wheel step on the pinned base during protected
provisioning, before admitting browsing. Retain wheel licenses and library notices.

## Recreate the same pair without the old scratch scripts

First stop through the existing owner control and verify **both recorded container IDs are stopped**. Preserve the originals and private profile/state before any replacement. Copy `autostart`, `browser-health.py`, `nginx-default.conf`, `selkies-run`, `seccomp-browser.json`, `provision-master.py`, `configure-egress.py`, and `browser-firewall.py` into the private deployment directory; retain their recorded bytes and give shell scripts their execute permission where needed.

The JSON argv templates are the actually used `docker create` argument arrays with only private directory, instance name, full container identity and loopback viewer port parameterized. `host-config.template.json` also exposes the owner origin and control port. Render their `${NAME}` string markers explicitly in Python, never with shell `eval`:

```python
# Inputs are chosen by the operator, never by a model or browser request.
values = {"DEPLOYMENT_DIR": str(deployment_dir), "INSTANCE": deployment_dir.name,
          "VIEWER_PORT": str(viewer_port), "CONTROL_PORT": str(control_port),
          "OWNER_ORIGIN": owner_origin,
          "BROWSER_CONTAINER_ID": browser_id, "MCP_CONTAINER_ID": mcp_id}
def render(value):
    if isinstance(value, str):
        for name, replacement in values.items():
            value = value.replace("${" + name + "}", replacement)
        if "${" in value:
            raise ValueError("unresolved deployment input")
        return value
    if isinstance(value, list):
        return [render(item) for item in value]
    return {key: render(item) for key, item in value.items()}
# Read each JSON template, render, persist privately, then execute an argv list
# with subprocess.run(argv, check=True, capture_output=True), never shell=True.
# For host-config only: cast rendered["listen_port"] to int before saving.
```

Create the internal instance network with `docker network create --internal --subnet 192.168.0.0/20 --gateway 192.168.0.1 "$INSTANCE-net"`, after confirming it does not overlap another deployment. The browser template fixes its address to `192.168.0.2`. Create the browser first, capture its full ID, apply the hash-bound Selkies overlay, and use that ID for MCP's `container:<ID>` namespace. Record both full IDs as `{"browser":"...","mcp":"..."}` in private `container-ids.json`. This is the identity format the controller and firewall helpers already consume.

`configure-egress.py` reproduces the separate `192.168.16.0/20` bridge, browser address `192.168.16.2`, and preferred default route. Both networks have Docker IPv6 disabled. On the delivered host, Docker consequently set `disable_ipv6=1` on both non-loopback container interfaces; no manual sysctl adjustment was used. `browser-firewall.py apply` checks the actual interface settings and verifies that IPv6 addresses/routes are loopback-only before changing rules. A host whose behavior differs stops at that check.

For the initial protected startup, keep MCP and owner access unavailable until the browser network and firewall are ready. The browser starts at `about:blank`; do not give it navigation or untrusted input during this provisioning interval. Attach the egress network, then run `sudo python3 "$DEPLOYMENT_DIR/browser-firewall.py" apply` before public browsing. The copied helper installs only its exact source-IP hooks/chains: it denies private/reserved destinations and new connections to the host, while permitting replies to inbound controller/gateway traffic. `status` records rules; `rollback` removes only this instance's declared hooks and chains. Stop browser/MCP before removing its protection. Firewall persistence across a host reboot is not established by this recipe; reapply and read back before enabling owner/MCP access after reboot.

The present addresses are a deployment choice, not a core authorization rule. Changing them requires coordinated edits to the argv templates, exact-source firewall, network helper, host origins, MCP allowed-hosts, gateway transport policy and separate managed-endpoints file. Do not replace those exact exceptions with a general Docker CIDR grant.

## Existing host controller and Eva integration

Render a private `host-config.json` with the two exact IDs. Its controller-user-owned secret files stay mode 0600. Run the host controller as the existing configuration/state owner (UID 1000, `administrator` in this installation), using the installed Eva dependencies and matching source. A root launch is intentionally refused for these user-owned files; do not change their ownership to bypass that check:

```sh
PYTHONPATH="$EVA_SOURCE_DIR/src" "$EVA_PYTHON" -m eva.operations.browser_session --config "$DEPLOYMENT_DIR/host-config.json"
```

The controller holds its existing lifetime file lock. Do not start a second process against the same state directory or overwrite its container IDs while it is running. The delivered process was launched directly; a staged user-service unit was never activated. This document does **not** claim service or firewall recovery across host reboot.

The delivered core settings use `EVA_BROWSER_CONTROL_URL=http://172.30.0.1:45163`, owner origin `http://localhost:44711`, and the private control token file `/run/eva/connectors/mcp/browser-control-token`. The host and core copies must contain the same existing token, with their respective required owners. The core receives only the control URL/token and the existing read-only connector directory. See `../living-core/mcp-gateway.compose.yml` for optional variables and `../../tools/eva2_quiescent_cutover.py` for exact file/hash admission.

The browser MCP endpoint is `http://192.168.0.2:8931/mcp`. Operator configuration admits that exact endpoint/address in both the gateway transport guard and `EvaManagedConnectorEndpoints/v1`, bound to the unchanged tenant/installation/owner and existing mandate configuration hash. Follow `../mcp-autonomy/` for the already established guarded gateway recreation route; preserve durable mandate revocation. Native browser CDP remains loopback-only inside the shared namespace; neither CDP nor the raw Selkies control port is published to the household LAN.

## Licensing and provenance

Retain the included LinuxServer GPL-3.0 packaging license and Apache-2.0 Playwright/Moby licenses. Selkies' patched module is MPL-2.0; reconstruction preserves its original license header and the included source patch exposes the modification. Upstream image contents retain their own component licenses. `provenance.json` and `selkies-auth-source.json` are source/hash records, not a claim that a later reprovisioned instance has passed user acceptance.


## Autonomous use on the current installation — 3 October 2026

The current core and delegated worker set `EVA_BROWSER_MCP_ENDPOINT` to the
registered own-browser endpoint, alongside the existing control URL and private
token file. The connector remains owner-bound through the existing mandate.
Actual authorized tool use starts a stopped browser through the existing host
controller. Owner takeover and recovery states are not automatically resumed.
Personal Chrome sharing is optional and is not a prerequisite for these tasks.

The same private gateway session must be retained across snapshot and click.
The browser generation and owner-derived connector identity scope that session;
it grants no access by itself. Existing gateway authentication and current
connector authorization still apply to each call. Browser lifecycle changes
rotate the session. ContextForge currently releases old session generations on
session removal or gateway shutdown; generation rotation alone does not collect
them. No new general session platform is included in this correction.

On this host `eva-owner-browser.service` is enabled and running as of this
change. This supersedes the original direct-launch statement above for the
current installation, but does not establish reboot/firewall recovery.
Installed images, source hashes, the native package overlay, observed task
outcome and limitations are recorded separately in
`../../evidence/product/autonomous-browser-installed-20261003.json`.
