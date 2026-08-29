from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.eva2_canary_cutover import (
    CanaryCutoverError,
    EVA1_PATHS,
    canonical_dropin,
    switch_canary,
)


CURRENT = "http://127.0.0.1:44701"
TARGET = "http://127.0.0.1:44702"


def _dropin(tmp_path: Path) -> Path:
    path = tmp_path / "30-eva2-current-candidate.conf"
    path.write_bytes(canonical_dropin(CURRENT))
    path.chmod(0o644)
    return path


def _healthy() -> dict[str, int]:
    return {path: 200 for path in EVA1_PATHS}


def test_switches_only_after_target_ready_and_attests_eva1(tmp_path: Path) -> None:
    dropin = _dropin(tmp_path)
    commands: list[tuple[str, ...]] = []

    def runner(command):  # type: ignore[no-untyped-def]
        commands.append(tuple(command))
        if "show" in command:
            return f"EVA_DATA_DIR=/data EVA2_CANARY_URL={TARGET}\n"
        return ""

    receipt = switch_canary(
        dropin_path=dropin,
        expected_current_url=CURRENT,
        target_url=TARGET,
        owner_uid=os.getuid(),
        runner=runner,
        ready_probe=lambda url: url == TARGET,
        eva1_probe=_healthy,
        waiter=lambda check: check(),
    )

    assert dropin.read_bytes() == canonical_dropin(TARGET)
    assert receipt["status"] == "switched"
    assert receipt["sourcePort"] == 44701
    assert receipt["targetPort"] == 44702
    assert [command[1] for command in commands] == [
        "daemon-reload",
        "restart",
        "show",
    ]


def test_refuses_unready_target_without_writing_or_restarting(tmp_path: Path) -> None:
    dropin = _dropin(tmp_path)
    commands: list[tuple[str, ...]] = []

    with pytest.raises(CanaryCutoverError, match="not ready"):
        switch_canary(
            dropin_path=dropin,
            expected_current_url=CURRENT,
            target_url=TARGET,
            owner_uid=os.getuid(),
            runner=lambda command: commands.append(tuple(command)) or "",
            ready_probe=lambda _url: False,
            eva1_probe=_healthy,
            waiter=lambda check: check(),
        )

    assert dropin.read_bytes() == canonical_dropin(CURRENT)
    assert commands == []


def test_restores_exact_previous_dropin_when_eva1_stays_unhealthy(tmp_path: Path) -> None:
    dropin = _dropin(tmp_path)
    loaded_urls: list[str] = []

    def runner(command):  # type: ignore[no-untyped-def]
        if "show" in command:
            loaded = TARGET if dropin.read_bytes() == canonical_dropin(TARGET) else CURRENT
            loaded_urls.append(loaded)
            return f"EVA2_CANARY_URL={loaded}\n"
        return ""

    calls = 0

    def eva1_probe() -> dict[str, int]:
        nonlocal calls
        calls += 1
        if dropin.read_bytes() == canonical_dropin(TARGET):
            return {path: 0 for path in EVA1_PATHS}
        return _healthy()

    with pytest.raises(CanaryCutoverError, match="did not become healthy"):
        switch_canary(
            dropin_path=dropin,
            expected_current_url=CURRENT,
            target_url=TARGET,
            owner_uid=os.getuid(),
            runner=runner,
            ready_probe=lambda _url: True,
            eva1_probe=eva1_probe,
            waiter=lambda check: check(),
        )

    assert calls > 1
    assert loaded_urls == [TARGET, CURRENT]
    assert dropin.read_bytes() == canonical_dropin(CURRENT)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:44702",
        "http://localhost:44702",
        "http://127.0.0.1:80",
        "http://user@127.0.0.1:44702",
        "http://127.0.0.1:44702/path",
        "http://127.0.0.1:44702?x=1",
    ],
)
def test_refuses_noncanonical_or_nonloopback_targets(tmp_path: Path, url: str) -> None:
    with pytest.raises(CanaryCutoverError):
        switch_canary(
            dropin_path=_dropin(tmp_path),
            expected_current_url=CURRENT,
            target_url=url,
            owner_uid=os.getuid(),
            runner=lambda _command: "",
            ready_probe=lambda _url: True,
            eva1_probe=_healthy,
            waiter=lambda check: check(),
        )
