from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "src/tools" / "verify_postgres_password_file_contract.py"
DEPLOY = ROOT / "deploy" / "living-core"


def _module():
    specification = importlib.util.spec_from_file_location(
        "postgres_password_file_contract", TOOL
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_contract_tool_pins_postgres_digest_and_uses_disposable_resources() -> None:
    module = _module()

    assert module.POSTGRES_IMAGE == (
        "docker.io/library/postgres@sha256:"
        "472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
    )
    assert "TemporaryDirectory" in TOOL.read_text(encoding="utf-8")
    assert "uuid4" in TOOL.read_text(encoding="utf-8")
    assert "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256" in TOOL.read_text(
        encoding="utf-8"
    )
    assert '[docker, "volume", "rm", volume]' in TOOL.read_text(encoding="utf-8")


def test_contract_tool_never_prints_secret_or_docker_logs() -> None:
    source = TOOL.read_text(encoding="utf-8")

    assert "completed.stdout" not in source
    assert "completed.stderr" not in source
    assert "token_urlsafe" in source
    assert "bootstrap-password" not in source.split("print(", maxsplit=1)[1]


def test_bootstrap_compose_binds_password_file_without_docker_secrets() -> None:
    document = yaml.safe_load(
        (DEPLOY / "postgres-bootstrap.compose.yml").read_text(encoding="utf-8")
    )
    service = document["services"]["postgres"]

    assert "secrets" not in document
    assert "secrets" not in service
    assert service["image"] == _module().POSTGRES_IMAGE
    assert service["environment"] == {
        "POSTGRES_DB": "${EVA_STACK_POSTGRES_DATABASE:-eva2}",
        "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
        "POSTGRES_PASSWORD_FILE": "/run/eva2/postgres-password",
        "POSTGRES_USER": "postgres",
    }
    assert service["volumes"][0] == {
        "type": "bind",
        "source": (
            "${EVA_POSTGRES_PASSWORD_FILE_HOST:?set a root-owned mode-0600 "
            "PostgreSQL password file}"
        ),
        "target": "/run/eva2/postgres-password",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
