from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_postboot_unit_never_reboots_and_uses_private_fixed_paths() -> None:
    unit = (
        ROOT / "deploy/living-core/eva2-managed-b-postboot-proof.service"
    ).read_text(encoding="utf-8")
    assert "ConditionPathExists=/var/lib/eva2/reboot-proof/armed.json" in unit
    assert "ConditionPathExists=!/var/lib/eva2/reboot-proof/receipt.json" in unit
    assert "--startup-wait-seconds 180" in unit
    assert "UMask=0077" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/eva2/reboot-proof" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=localhost" in unit
    assert "SystemCallFilter=~" in unit
    assert "@reboot" in unit
    lowered = unit.casefold()
    assert "systemctl reboot" not in lowered
    assert "wsl.exe" not in lowered
    assert "terminate" not in lowered


def test_legacy_rollback_consumer_is_recreated_before_docker_start() -> None:
    rule = (ROOT / "deploy/living-core/eva2-hindsight-rollback.tmpfiles").read_text(
        encoding="utf-8"
    )
    active = [line for line in rule.splitlines() if line and not line.startswith("#")]
    assert active == ["d /tmp/eva2-hindsight-consumer-live 0700 1000 1000 -"]
