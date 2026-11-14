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
current_run=""
if [[ -f "/run/eva2-holostage-public-origin" ]]; then
  current_run="$(tr -d '[:space:]' < "/run/eva2-holostage-public-origin" || true)"
fi
if [[ "${current_run}" != "${origin}" ]]; then
  tmp_run="/run/eva2-holostage-public-origin.tmp.$$"
  printf '%s\n' "${origin}" > "${tmp_run}"
  chmod 0644 "${tmp_run}"
  mv -f "${tmp_run}" "/run/eva2-holostage-public-origin"
fi

check_funnel_configured() {
  local status_payload
  status_payload="$(tailscale funnel status --json 2>/dev/null || echo '{}')"
  DNS_TARGET="${dns_name}:443" python3 -c '
import json, os, sys
try:
    d = json.loads(sys.stdin.read() or "{}")
    key = os.environ.get("DNS_TARGET", "")
    allow = bool((d.get("AllowFunnel") or {}).get(key))
    proxy = (((d.get("Web") or {}).get(key) or {}).get("Handlers") or {}).get("/", {}).get("Proxy")
    print("yes" if (allow and proxy == "http://127.0.0.1:44714") else "no")
except Exception:
    print("no")
' <<< "${status_payload}"
}

funnel_active="$(check_funnel_configured)"

# Only reconfigure tailscaled when Funnel is not yet active on 127.0.0.1:44714
# so active WebSocket/HTTPS voice calls never experience serve-config reloads.
if [[ "${funnel_active}" != "yes" ]]; then
  funnel_out="$(timeout 8 tailscale funnel --bg --yes 44714 2>&1 || true)"
  enable_url="$(printf '%s\n' "${funnel_out}" | grep -Eo 'https://login\.tailscale\.com/f/funnel\?[^[:space:]]+' | head -n 1 || true)"
  if [[ -n "${enable_url}" ]]; then
    tmp_enable="/run/eva2-tailscale-funnel-enable-url.tmp.$$"
    (umask 0077; printf '%s\n' "${enable_url}" > "${tmp_enable}")
    chmod 0600 "${tmp_enable}"
    mv -f "${tmp_enable}" "/run/eva2-tailscale-funnel-enable-url"
  fi
  funnel_active="$(check_funnel_configured)"
fi

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
