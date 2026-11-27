"""CLI entrypoint for Eva's local authenticated MCP Gateway & Universal Capability Host."""

from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import sys

from eva.operations.appliance_mcp_gateway import (
    ApplianceMcpGatewayServer,
    UniversalIntegrationHub,
)
from eva.operations.installer_identity_storage import atomic_write_private


def _read_or_create_token(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_file():
        atomic_write_private(path, (secrets.token_urlsafe(36) + "\n").encode("utf-8"))
    return path.read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eva-appliance-mcp-gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=44712)
    parser.add_argument("--runtime-root", type=Path, default=Path("/var/lib/eva2-local"))
    args = parser.parse_args(argv)

    secrets_dir = args.runtime_root / "secrets"
    gw_token = _read_or_create_token(secrets_dir / "mcp-gateway-token")
    reg_token = _read_or_create_token(secrets_dir / "mcp-registration-token")

    hub = UniversalIntegrationHub(args.runtime_root)
    server = ApplianceMcpGatewayServer(
        (args.host, args.port),
        hub=hub,
        gateway_token=gw_token,
        registration_token=reg_token,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
