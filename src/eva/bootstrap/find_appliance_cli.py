"""Fast, zero-dependency local network radar to find and open an Eva appliance on port 8765.

Solves the classic headless problem where Windows/Android fails to resolve `eva.local` mDNS
or the user does not know which DHCP IP address the router assigned to their mini-PC / Pi.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
from typing import Any
import webbrowser


_PAIR_CODE_RE = re.compile(r"\bEVA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def _local_ipv4_subnets() -> list[ipaddress.IPv4Network]:
    seen_ips: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 80))
            seen_ips.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4] and isinstance(info[4][0], str):
                seen_ips.append(info[4][0])
    except OSError:
        pass

    nets: list[ipaddress.IPv4Network] = []
    for raw in seen_ips:
        if raw.strip() == "10.255.255.254":
            continue
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            not isinstance(addr, ipaddress.IPv4Address)
            or addr.is_loopback
            or addr.is_link_local
            or not addr.is_private
        ):
            continue
        # Skip WSL/Hyper-V/Docker internal 172.16-31 bridges after home LAN ranges
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
        if net not in nets:
            nets.append(net)

    nets.sort(key=lambda n: 0 if str(n).startswith("192.168.") else (1 if str(n).startswith("10.") else 2))
    return nets[:3]


def probe_appliance_host(host: str, port: int = 8765, timeout: float = 0.45) -> dict[str, Any] | None:
    conn = http.client.HTTPConnection(host, port=port, timeout=timeout)
    try:
        conn.request("GET", "/internal/v1/appliance/ping", headers={"Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(16_384)
        if resp.status != 200:
            return None
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("eva_appliance") is True:
            return {"host": host, "port": port, **data}
        return None
    except (OSError, http.client.HTTPException, UnicodeDecodeError, ValueError):
        return None
    finally:
        conn.close()


def discover_appliances(*, port: int = 8765, include_loopback: bool = True) -> list[dict[str, Any]]:
    candidates: list[str] = ["eva.local"]
    if include_loopback:
        candidates.append("127.0.0.1")

    # Quick direct check on eva.local & loopback first
    found: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    for quick in candidates:
        hit = probe_appliance_host(quick, port=port, timeout=0.6)
        if hit is not None:
            found.append(hit)
            seen_hosts.add(quick)
            for ip in hit.get("lan_ips") or []:
                seen_hosts.add(str(ip))

    subnets = _local_ipv4_subnets()
    ip_targets = [
        str(ip)
        for net in subnets
        for ip in net.hosts()
        if str(ip) not in seen_hosts
    ]
    if ip_targets:
        with ThreadPoolExecutor(max_workers=64) as pool:
            futs = {pool.submit(probe_appliance_host, ip, port, 0.38): ip for ip in ip_targets}
            for fut in as_completed(futs):
                hit = fut.result()
                if hit is not None:
                    found.append(hit)
    return found


def _read_saved_pairing_code(repo_root: Path) -> str | None:
    for rel in (
        "Open-Eva-HP-Mini-Setup.html",
        "deploy/appliance/eva-usb-output/Open-Eva-Setup.html",
    ):
        cand = repo_root / rel
        if cand.is_file():
            txt = cand.read_text(encoding="utf-8", errors="ignore")
            m = _PAIR_CODE_RE.search(txt)
            if m:
                return m.group(0)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zoek Eva automatisch op je thuisnetwerk.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-loopback", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    saved_code = _read_saved_pairing_code(repo_root)

    print("Eva Netwerk-Radar: zoeken naar Eva op je thuisnetwerk (poort 8765)...", flush=True)
    hits = discover_appliances(port=args.port, include_loopback=not args.skip_loopback)
    if not hits:
        print("\nGeen actieve Eva-installatie gevonden op poort 8765.")
        print("Tips:")
        print("  1. Staat de HP Pro Mini / mini-pc net aan? De eerste opstart duurt ~15-30 sec tot de statusserver reageert.")
        print("  2. Zit deze laptop op hetzelfde thuisnetwerk (wifi/kabel) als de mini-pc?")
        print("  3. Wil je Eva lokaal op déze laptop starten? Dubbelklik dan op Start-Eva-Installer.cmd.")
        return 1

    # Prefer non-loopback LAN appliance when available
    hits.sort(key=lambda h: 1 if h["host"] == "127.0.0.1" else 0)
    print(f"\nGevonden ({len(hits)}):")
    for idx, h in enumerate(hits, 1):
        phase = h.get("phase", "ready")
        step_info = f" — {h.get('step')}" if h.get("step") else ""
        print(
            f"  [{idx}] {h.get('installation_name', 'Eva')} op http://{h['host']}:{h['port']} "
            f"(host={h.get('hostname')}, status={phase}{step_info})"
        )

    chosen = hits[0]
    base_url = f"http://{chosen['host']}:{chosen['port']}"
    if chosen.get("phase") == "bootstrapping":
        target_url = base_url + "/"
    elif saved_code and chosen["host"] != "127.0.0.1":
        target_url = f"{base_url}/pair#{saved_code}"
    else:
        target_url = f"{base_url}/pair"

    print(f"\nOpenen in browser: {target_url}")
    if not args.no_browser:
        webbrowser.open(target_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
