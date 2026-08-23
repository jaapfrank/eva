"""Small host CLI surface for Eva installation status and diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from eva.operations.ab_slots import ABSlotStore, state_payload, state_sha256


def _encode(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eva")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "diagnose"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    store = ABSlotStore(arguments.root)
    if arguments.command == "status":
        try:
            state = store.load()
        except (OSError, RuntimeError, ValueError) as exc:
            print(_encode({"status": "unavailable", "error": str(exc)}))
            return 2
        print(
            _encode(
                {
                    "status": "available",
                    "state": state_payload(state),
                    "state_sha256": state_sha256(state),
                }
            )
        )
        return 0
    diagnosis = store.diagnose()
    print(
        _encode(
            {
                "status": "passed" if diagnosis.healthy else "failed",
                "errors": list(diagnosis.errors),
                "state_sha256": (
                    None if diagnosis.state is None else state_sha256(diagnosis.state)
                ),
            }
        )
    )
    return 0 if diagnosis.healthy else 2


if __name__ == "__main__":
    sys.exit(main())
