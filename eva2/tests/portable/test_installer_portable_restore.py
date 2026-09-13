import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from eva.operations.install_schema import install_schema
from tools.installer_portable_restore import InstallerPortableRestore
from tools.portable_transport import seal_portable_archive
from tools.read_eva_portable_archive import validate_and_derive
from tests.portable.test_portable_transport import PASSPHRASE
from tests.portable.test_portable_restore_full_schema import (
    _exact_fixture_row,
    _synthetic_memory_command,
)
from tests.support.noor_4d_repository import PostgresNoor4DRepository
from eva.adapters.postgres.catalog import metadata
from eva.application.memory import MemoryAuthority
from tools.postgres_memory_repository import PostgresMemoryRepository
from tools.portable_export import export_portable_archive
from tests.portable.restore_authority_fixture import restore_authority_for_archive

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path):
    dsn = os.getenv("EVA2_IN002_TEST_ADMIN_DSN")
    if not dsn:
        pytest.skip("isolated IN002 database required")
    admin = create_engine(dsn, isolation_level="AUTOCOMMIT")
    name = "in002_" + uuid4().hex
    with admin.connect() as c:
        c.exec_driver_sql("CREATE DATABASE " + name)
    target = admin.url.set(database=name)
    engine = create_engine(target)
    install_schema(
        target.render_as_string(hide_password=False), alembic_ini=ROOT / "alembic.ini"
    )
    source_name = name + "_source"
    with admin.connect() as c:
        c.exec_driver_sql("CREATE DATABASE " + source_name)
    source_engine = create_engine(admin.url.set(database=source_name))
    install_schema(
        source_engine.url.render_as_string(hide_password=False),
        alembic_ini=ROOT / "alembic.ini",
    )
    tenant = str(_exact_fixture_row("static/tenant").values["tenant_id"])
    installation = str(
        _exact_fixture_row("static/installation").values["installation_id"]
    )
    principal = str(_exact_fixture_row("static/principal-owner").values["principal_id"])
    evidence, conversation, message, command, _, _ = _synthetic_memory_command(
        tenant_id=tenant, installation_id=installation, principal_id=principal
    )
    with source_engine.begin() as c:
        c.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        c.execute(text("SET LOCAL ROLE eva_dbos"))
        repo = PostgresNoor4DRepository(c, tenant, installation)
        for key in (
            "static/tenant",
            "static/installation",
            "static/identity-pack",
            "static/policy-pack",
            "static/principal-owner",
        ):
            repo.upsert_exact(_exact_fixture_row(key))
        for table, row in [
            ("eva_evidence.evidence_envelopes", evidence),
            ("eva_core.conversations", conversation),
            ("eva_core.messages", message),
        ]:
            c.execute(metadata.tables[table].insert().values(**row))
        MemoryAuthority(PostgresMemoryRepository(c, tenant, installation)).remember(
            command
        )
    archive = tmp_path / "portable"
    with source_engine.connect() as c:
        export_portable_archive(
            c,
            tenant_id=tenant,
            installation_id=installation,
            export_sequence=1,
            created_at="2026-09-13T00:00:00Z",
            destination=archive,
            object_blob_root=None,
        )
    source_engine.dispose()
    with admin.connect() as c:
        c.exec_driver_sql("DROP DATABASE " + source_name)
    sealed = tmp_path / "backup.eva"
    seal_portable_archive(
        archive, sealed, passphrase=PASSPHRASE, validator=validate_and_derive
    )
    authority = [
        restore_authority_for_archive(
            (archive / "archive.json").read_bytes(), generation=0
        )
    ]
    workspace = tmp_path / "private"
    workspace.mkdir(mode=0o700)
    workflow = InstallerPortableRestore(
        engine=engine,
        authority_reader=lambda: authority[0],
        workspace=workspace,
        object_store_root=tmp_path / "objects",
    )
    yield workflow, sealed.read_bytes(), engine, authority, archive
    engine.dispose()
    with admin.connect() as c:
        c.exec_driver_sql("DROP DATABASE " + name)
    admin.dispose()


def count(engine):
    with engine.connect() as c:
        return c.execute(
            text("SELECT count(*) FROM eva_core.installations")
        ).scalar_one()


def test_real_archive_restores_identity_and_refuses_overwrite(setup):
    w, blob, engine, authority, archive = setup
    from tests.api.test_installer_restore import client, login
    from base64 import b64encode

    c, b = client(w)
    assert login(c, b).status_code == 204
    response = c.post(
        "/internal/v1/onboarding/restore/prepare",
        json={"archive": b64encode(blob).decode(), "passphrase": PASSPHRASE},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert count(engine) == 0
    response = c.post(
        "/internal/v1/onboarding/restore/execute",
        json={"confirmation": preview["confirmation"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "restored" and result["inserted_records"] > 0
    with engine.connect() as c:
        assert (
            str(
                c.execute(
                    text("SELECT installation_id FROM eva_core.installations")
                ).scalar_one()
            )
            == preview["summary"]["installation"]
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM eva_core.identity_pack_versions")
            ).scalar_one()
            > 0
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM eva_memory.current_facts")
            ).scalar_one()
            == 1
        )
    with pytest.raises(ValueError):
        w.execute(preview["confirmation"])
    with pytest.raises(ValueError):
        w.prepare(blob, PASSPHRASE)
    assert count(engine) == 1


@pytest.mark.parametrize("failure", ["password", "tamper", "revocation"])
def test_failed_restore_keeps_target_empty(setup, failure):
    w, blob, engine, authority, archive = setup
    if failure == "revocation":
        p = w.prepare(blob, PASSPHRASE)
        authority[0] = restore_authority_for_archive(
            (archive / "archive.json").read_bytes(), admit=False
        )
        with pytest.raises(ValueError):
            w.execute(p["confirmation"])
    else:
        if failure == "tamper":
            blob = blob[:-1] + bytes([blob[-1] ^ 1])
        with pytest.raises(ValueError):
            w.prepare(blob, "wrong password" if failure == "password" else PASSPHRASE)
    assert count(engine) == 0
    assert not list(w.workspace.iterdir())
