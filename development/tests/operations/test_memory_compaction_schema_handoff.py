"""The new schema head must reach worker, installer, archive and release checks."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from eva.bootstrap import delegated_work_service as worker
from eva.operations import install_schema as schema
from tools import portable_v2_profile as portable
from tools.managed_b_candidate import release_inputs as release

HEAD="4e_0010_memory_compaction"
PREVIOUS="4e_0009_restore_hold"
ROOT=Path(__file__).resolve().parents[3]


class _Engine:
    def __init__(self,values): self.values=iter(values); self.disposed=False
    @contextmanager
    def connect(self): yield self
    def scalar(self,statement): return next(self.values)
    def dispose(self): self.disposed=True


@pytest.mark.parametrize("revision", [HEAD,PREVIOUS,"unknown"])
def test_worker_health_accepts_actual_head_and_rejects_stale_or_unknown(monkeypatch,revision):
    from eva.bootstrap import delegated_work_provider as provider
    engine=_Engine([revision])
    monkeypatch.setattr(provider,"verify_delegated_provider_dependencies",lambda:None)
    monkeypatch.setattr(provider,"validate_delegated_model_access_lease",lambda path:None)
    monkeypatch.setattr(worker,"read_runtime_secret_file",lambda *a,**kw:"postgresql://unused/unused")
    monkeypatch.setattr(worker.sa,"create_engine",lambda *a,**kw:engine)
    settings=SimpleNamespace(research_enabled=False,digital_enabled=False,
        model_access_lease_file=Path("/unused"),service=SimpleNamespace(database_dsn_file=Path("/unused")))
    if revision==HEAD: worker.check_openai_delegated_work(settings)
    else:
        with pytest.raises(RuntimeError,match="schema head differs"):
            worker.check_openai_delegated_work(settings)
    assert engine.disposed


@pytest.mark.parametrize("revision", [HEAD,PREVIOUS,"4b_0000_core_namespace"])
def test_existing_installer_forward_routes_still_recognize_prior_heads(revision):
    assert schema._current_revision(_Engine([True,revision]))==revision


def test_installer_still_rejects_unregistered_future_revision():
    with pytest.raises(RuntimeError,match="unknown Eva schema"):
        schema._current_revision(_Engine([True,"4e_0011_unregistered"]))


def test_all_current_registrations_match_literal_alembic_head():
    config=Config(str(ROOT/"deploy/alembic.ini"))
    config.set_main_option("script_location",str(ROOT/"deploy/migrations"))
    scripts=ScriptDirectory.from_config(config)
    assert scripts.get_current_head()==HEAD
    assert scripts.get_revision(HEAD).down_revision==PREVIOUS
    assert worker._SCHEMA_HEAD==schema._HEAD_REVISION==portable.SCHEMA_HEAD==HEAD
    assert portable.PROFILE["schema_head"]==HEAD
    assert portable.PROFILE["activation_policy"]=="transactional_database_hold_before_runtime_start"
    assert portable.CONTRACT=="EvaPortableArchive/v2"


def _manifest():
    files={};revisions=[]
    for revision,down,path in release._EXPECTED_MIGRATIONS:
        data=(ROOT/path).read_bytes();files[path]=(data,0o644)
        revisions.append(dict(revision=revision,downRevision=down,path=path,sha256=hashlib.sha256(data).hexdigest()))
    return files,dict(head=HEAD,revisions=revisions)


def test_release_validator_accepts_complete_forward_chain_including_new_function():
    files,manifest=_manifest()
    assert release._EXPECTED_MIGRATIONS[-2][0]==PREVIOUS
    assert release._EXPECTED_MIGRATIONS[-1]==(HEAD,PREVIOUS,"deploy/migrations/versions/4e_0010_memory_compaction.py")
    assert release._validate_migrations(files,manifest)==HEAD


def test_release_validator_rejects_archive_omitting_new_head():
    files,manifest=_manifest()
    manifest["head"]=PREVIOUS
    manifest["revisions"].pop()
    del files["deploy/migrations/versions/4e_0010_memory_compaction.py"]
    with pytest.raises(RuntimeError,match="migration authority"):
        release._validate_migrations(files,manifest)
