from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "living-core"


def test_durable_goal_worker_binds_hardened_dsn_read_only() -> None:
    document = yaml.safe_load(
        (DEPLOY / "durable-goals.compose.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["durable-goal-worker"]

    assert "secrets" not in service
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "${EVA_DATABASE_DSN_FILE_HOST:?set a runtime-owned PostgreSQL DSN file}",
            "target": "/run/secrets/database_dsn",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
