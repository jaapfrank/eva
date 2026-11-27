"""CLI entrypoint for Eva's 1-click character, module, and appliance wizard."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn

from eva.adapters.api.appliance_wizard import create_appliance_wizard_app
from eva.operations.appliance_setup import ApplianceSetupService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eva-appliance-wizard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", type=Path, default=Path("/var/lib/eva2-appliance"))
    parser.add_argument("--runtime-root", type=Path, default=Path("/var/lib/eva2-local"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument("--codex-auth-file", type=Path)
    parser.add_argument("--core-url", default="http://127.0.0.1:44710")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:44711")
    parser.add_argument("--hindsight-url", default="http://127.0.0.1:44672")
    parser.add_argument("--print-pairing-link", action="store_true")
    args = parser.parse_args(argv)

    if not 1024 <= args.port <= 65535:
        parser.error("poort moet tussen 1024 en 65535 liggen")

    service = ApplianceSetupService(
        workspace=args.workspace,
        runtime_root=args.runtime_root,
        repo_root=args.repo_root,
        codex_auth_file=args.codex_auth_file,
        core_base_url=args.core_url,
        dashboard_proxy_url=args.dashboard_url,
        hindsight_url=args.hindsight_url,
    )
    if args.print_pairing_link:
        link_file = args.workspace / "pairing-link.private.txt"
        if link_file.is_file():
            print(link_file.read_text(encoding="utf-8").strip())
        elif service.initial_pairing_code():
            print(f"http://127.0.0.1:{args.port}/pair#{service.initial_pairing_code()}")
        return 0

    code = service.initial_pairing_code()
    if code:
        print(f"Eva Wizard gestart. Koppel-link: http://127.0.0.1:{args.port}/pair#{code}")
    else:
        print(f"Eva Wizard gestart op http://127.0.0.1:{args.port}/pair")

    app = create_appliance_wizard_app(service)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
