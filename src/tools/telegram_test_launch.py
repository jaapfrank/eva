"""The only supported host-side launch command for the Telegram test ingress."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from typing import NoReturn

from tools.telegram_test_preflight import (
    TelegramTestPlan,
    load_telegram_test_plan,
    preflight_telegram_test,
    preflight_telegram_test_files,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "living-core" / "telegram-test-ingress.compose.yml"
SUPPORT_MANIFEST = ROOT / "deploy" / "living-core" / "telegram-test-support.compose.yml"
PROJECT_NAME = "eva2-telegram-native-test"
INSPECTION_HINT = (
    "Telegram test support may still be active for inspection; do not rerun it. "
    f"Inspect project {PROJECT_NAME!r} before an explicit operator cleanup."
)


def launch_telegram_test(plan: TelegramTestPlan) -> None:
    """Start, inspect, migrate, and finally attach the isolated test ingress.

    Support resources deliberately remain inspectable after a later failure.
    This command never guesses that it owns a malformed or pre-existing test
    resource, so it never performs automatic destructive cleanup.
    """

    preflight_telegram_test_files(plan)
    environment = {"LC_ALL": "C"}
    support_may_exist = False
    ingress_may_exist = False
    with _materialize_environment(plan) as directory:
        prefix = [
            plan.value("EVA_TEST_DOCKER_BINARY"),
            "compose",
            "--project-name",
            PROJECT_NAME,
            "--env-file",
            str(Path(directory) / "sealed.env"),
            "-f",
            str(SUPPORT_MANIFEST),
            "--profile",
            "telegram-native-test",
        ]
        try:
            support_may_exist = True
            _run_compose(
                [
                    *prefix,
                    "up",
                    "--detach",
                    "--wait",
                    "--pull",
                    "never",
                    "postgres-test",
                    "telegram-test-core",
                ],
                environment,
            )
            # Migration never gets authority until the actually running support
            # containers, their networks, and their test-only volume are bound.
            preflight_telegram_test(plan)
            _run_compose(
                [
                    *prefix,
                    "run",
                    "--rm",
                    "--no-deps",
                    "schema-installer-test",
                ],
                environment,
            )
            preflight_telegram_test(plan)
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
            ingress_may_exist = True
            _run_compose(command, environment)
            preflight_telegram_test(plan, ingress_running=True)
        except RuntimeError as error:
            ingress_status = ""
            if ingress_may_exist:
                ingress_status = _stop_test_ingress(plan, directory, environment)
            if support_may_exist:
                raise RuntimeError(f"{error}. {ingress_status}{INSPECTION_HINT}") from error
            raise


def _run_compose(command: list[str], environment: dict[str, str]) -> None:
    try:
        subprocess.run(command, check=True, env=environment, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Telegram test stack launch failed") from error


def _stop_test_ingress(
    plan: TelegramTestPlan,
    directory: str,
    environment: dict[str, str],
) -> str:
    """Best-effort stop for the one poller that may have reached Telegram.

    The support database/core intentionally remain available for inspection.  A
    stopped ingress container has no polling loop, while its metadata remains
    available to the operator through the explicit test Compose project.
    """

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
        "stop",
        "telegram-ingress-test",
    ]
    try:
        subprocess.run(command, check=True, env=environment, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "Telegram test ingress stop was requested but not confirmed. "
    return "Telegram test ingress was stopped after the failed postflight. "


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
