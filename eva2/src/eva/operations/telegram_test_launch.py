"""The only supported host-side launch command for the Telegram test ingress."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from typing import NoReturn

from eva.operations.telegram_test_preflight import (
    TelegramTestPlan,
    load_telegram_test_plan,
    preflight_telegram_test,
)


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "deploy" / "living-core" / "telegram-test-ingress.compose.yml"
PROJECT_NAME = "eva2-telegram-native-test"


def launch_telegram_test(plan: TelegramTestPlan) -> None:
    """Run host preflight, then start only the isolated ingress service."""

    preflight_telegram_test(plan)
    environment = {"LC_ALL": "C"}
    with _materialize_environment(plan) as directory:
        command = [
            plan.value("EVA_TEST_DOCKER_BINARY"),
            "compose",
            "--project-name",
            PROJECT_NAME,
            "--env-file",
            str(Path(directory) / "sealed.env"),
            "-f",
            str(MANIFEST),
            "--profile",
            "telegram-native-test",
            "up",
            "--detach",
            "--no-deps",
            "--pull",
            "never",
            "telegram-ingress-test",
        ]
        try:
            subprocess.run(command, check=True, env=environment, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Telegram test ingress launch failed") from error


def _materialize_environment(plan: TelegramTestPlan) -> tempfile.TemporaryDirectory[str]:
    """Give Compose one private immutable snapshot, never the mutable source file."""

    directory = tempfile.TemporaryDirectory(prefix="eva2-telegram-test-", dir="/tmp")
    os.chmod(directory.name, 0o700)
    environment_file = Path(directory.name) / "sealed.env"
    descriptor = os.open(
        environment_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    try:
        os.chmod(environment_file, 0o600)
        payload = "".join(
            f"{name}={value}\n" for name, value in plan.values
        ).encode("utf-8")
        _write_all(descriptor, payload)
    finally:
        os.close(descriptor)
    return directory


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("unable to write Telegram test environment")
        remaining = remaining[written:]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        plan = load_telegram_test_plan(arguments.env_file)
        launch_telegram_test(plan)
    except RuntimeError as error:
        _die(str(error))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-launch: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
