"""Systemd launcher: verify immutable inputs, then resolve the active core slot."""
from __future__ import annotations

import asyncio
import argparse
from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import sys

# -I intentionally omits the script directory; this fixed root-managed sibling
# is the verifier, before any dependency or application path is admitted.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from service_bundle import verify


def selected_core(environment: str) -> str:
    routes = re.findall(r"(?:^|\s)EVA2_CANARY_URL=([^\s]+)", environment)
    if len(routes) != 1 or routes[0] not in {"http://127.0.0.1:44710", "http://127.0.0.1:44711"}:
        raise ValueError("loaded Eva route is not one retained slot")
    return routes[0]


def active_core(source_service: str = "eva.service") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.service", source_service):
        raise ValueError("invalid managed source service")
    state = subprocess.check_output(["systemctl", "is-active", source_service], text=True).strip()
    if state != "active":
        raise ValueError("Eva ingress is not active")
    environment = subprocess.check_output(["systemctl", "show", source_service,
        "--property=Environment", "--value"], text=True)
    return selected_core(environment)


def main(*, source_service: str = "eva.service") -> None:
    root = Path(__file__).resolve().parents[3]
    verify(root)
    # Isolated Python (-I) ignores shell/site PYTHONPATH. Add only verified files.
    sys.path[:0] = [str(root / "dependencies"), str(root / "eva2/src")]
    from speaker_service import ServiceConfig, main as run
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    config = ServiceConfig.load(credentials / "config.json")
    config = replace(config, eva_url=active_core(source_service), model=root / "silero_vad.onnx",
        certificate=credentials / "tls.crt", private_key=credentials / "tls.key")
    asyncio.run(run(config, check_only=False))


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source-service", default="eva.service")
        main(**vars(parser.parse_args()))
    except Exception:
        print('{"state":"failed","detail":"Speaker bundle, credentials or active core unavailable."}', file=sys.stderr)
        raise SystemExit(1) from None
