#!/usr/bin/env bash
# Synchronizes Tailscale Funnel HTTPS (443 -> 127.0.0.1:44712) and writes the
# resulting https://<node>.<tailnet>.ts.net origin for Eva2 HoloStage pairing links.
set -euo pipefail

MP_DIR="/var/lib/docker/volumes/eva2-managed-b-slot3-work/_data/model-providers"
LAN_FILE="${MP_DIR}/holostage_lan_origin.txt"
PUB_FILE="${MP_DIR}/holostage_public_origin.txt"

if [[ -d "${MP_DIR}" && ! -f "${LAN_FILE}" ]]; then
  tmp_lan="${LAN_FILE}.tmp.$$"
  printf '%s\n' "http://10.0.20.46:44712" > "${tmp_lan}"
  chown 65532:65532 "${tmp_lan}"
  chmod 0600 "${tmp_lan}"
  mv -f "${tmp_lan}" "${LAN_FILE}"
fi

if ! command -v tailscale >/dev/null 2>&1; then
  exit 0
fi

status_json="$(tailscale status --json 2>/dev/null || true)"
if [[ -z "${status_json}" ]]; then
  exit 0
fi

read -r backend_state dns_name < <(
  python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    st = str(d.get("BackendState") or "")
    dns = str((d.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
    print(f"{st} {dns}")
except Exception:
    print("unknown ")
' <<< "${status_json}"
)

if [[ "${backend_state}" != "Running" || -z "${dns_name}" || "${dns_name}" != *.ts.net ]]; then
  exit 0
fi

# Pin expected public *.ts.net origin for holostage-lan.mjs Host validation
origin="https://${dns_name}"
tmp_run="/run/eva2-holostage-public-origin.tmp.$$"
printf '%s\n' "${origin}" > "${tmp_run}"
chmod 0644 "${tmp_run}"
mv -f "${tmp_run}" "/run/eva2-holostage-public-origin"

# Ensure Tailscale Funnel exposes HTTPS 443 -> loopback 127.0.0.1:44714 without blocking if tailnet ACL awaits 1-click enable
funnel_out="$(timeout 8 tailscale funnel --bg --yes 44714 2>&1 || true)"
enable_url="$(printf '%s\n' "${funnel_out}" | grep -Eo 'https://login\.tailscale\.com/f/funnel\?[^[:space:]]+' | head -n 1 || true)"
if [[ -n "${enable_url}" ]]; then
  tmp_enable="/run/eva2-tailscale-funnel-enable-url.tmp.$$"
  (umask 0077; printf '%s\n' "${enable_url}" > "${tmp_enable}")
  chmod 0600 "${tmp_enable}"
  mv -f "${tmp_enable}" "/run/eva2-tailscale-funnel-enable-url"
fi

funnel_status="$(tailscale funnel status --json 2>/dev/null || echo '{}')"
funnel_active="$(
  python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read() or "{}")
    allow = d.get("AllowFunnel") or {}
    print("yes" if any(bool(v) for v in allow.values()) else "no")
except Exception:
    print("no")
' <<< "${funnel_status}"
)"

if [[ "${funnel_active}" != "yes" ]]; then
  exit 0
fi

rm -f "/run/eva2-tailscale-funnel-enable-url"

if [[ -d "${MP_DIR}" ]]; then
  current=""
  if [[ -f "${PUB_FILE}" ]]; then
    current="$(tr -d '[:space:]' < "${PUB_FILE}" || true)"
  fi
  if [[ "${current}" != "${origin}" ]]; then
    tmp_pub="${PUB_FILE}.tmp.$$"
    printf '%s\n' "${origin}" > "${tmp_pub}"
    chown 65532:65532 "${tmp_pub}"
    chmod 0600 "${tmp_pub}"
    mv -f "${tmp_pub}" "${PUB_FILE}"
  fi
fi
