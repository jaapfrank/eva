from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "living-core"


def test_schema_installer_binds_hardened_dsn_read_only() -> None:
    document = yaml.safe_load(
        (DEPLOY / "schema-install.compose.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["schema-installer"]

    assert "secrets" not in service
    assert service["profiles"] == ["schema-install"]
    assert service["restart"] == "no"
    assert service["user"] == "65532:65532"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["tmpfs"] == ["/tmp:rw,nosuid,nodev,noexec,size=16m"]
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "${EVA_DATABASE_DSN_FILE_HOST:?set a runtime-owned PostgreSQL DSN file}",
            "target": "/run/secrets/database_dsn",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
