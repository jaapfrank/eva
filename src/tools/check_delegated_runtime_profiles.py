"""Compare installed provider, research and digital-work release fingerprints.

No model request, task admission, token content or authority change is involved.
Run after a shared provider deployment, before declaring its worker route ready.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import subprocess

PROBE = """
from dataclasses import asdict
from pathlib import Path
import json
import sys
from eva.bootstrap.runtime import RuntimeSettings
from eva.bootstrap.delegated_work_service import _provider_release
from eva.bootstrap.delegated_research import (
    build_public_research_composition, HouseholdResearchProfile,
)
if sys.argv[1] == 'worker':
    from eva.bootstrap.delegated_work_service import OpenAIDelegatedWorkSettings, _research_composition
    settings = OpenAIDelegatedWorkSettings.from_environment()
    if not settings.research_enabled:
        raise RuntimeError('The installed worker has public research disabled')
    _, profile = _research_composition(settings)
else:
    s = RuntimeSettings.from_environment()
    if not s.delegated_research_enabled:
        raise RuntimeError("The installed core has public research disabled")
    _, profile = build_public_research_composition(
        model_access_lease_file=Path(s.conversation_auth_file),
        resource_budget=s.runtime_resource_budget,
    )
# Initiative sources also belong to the digital-work release; research-only
# agreement cannot prove that a worker will claim newly admitted digital tasks.
import sqlalchemy as sa
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.bootstrap.delegated_digital_composition import _digital_provider, build_digital_profile
from eva.bootstrap.completed_contributions import with_completed_contributions
from eva.providers.runtime import build_external_provider_bundle
from eva.providers.contracts import ExternalProviderSettings
runtime = RuntimeSettings.from_environment()
if sys.argv[1] == 'worker':
    from uuid import UUID
    from eva.adapters.postgres.operational_timeline import TimelineScope
    if not settings.digital_enabled:
        raise RuntimeError('The installed worker has digital work disabled')
    if TimelineScope(UUID(runtime.dashboard_tenant_id), UUID(runtime.dashboard_installation_id)) != settings.service.scope:
        raise RuntimeError('The installed digital worker scope differs')
if not runtime.delegated_digital_enabled:
    raise RuntimeError('The installed runtime has digital work disabled')
engine = sa.create_engine(sqlalchemy_postgres_dsn(runtime.database_dsn))
try:
    providers = ExternalProviderSettings(tuple(p for p in runtime.external_provider.providers
                                              if _digital_provider(p)))
    bundle = with_completed_contributions(runtime, engine,
        build_external_provider_bundle(providers, engine))
    digital_release = asdict(build_digital_profile(runtime, bundle).release)
finally:
    engine.dispose()
print(json.dumps({
    'digital_work': digital_release,
    'shared_provider': asdict(_provider_release()),
    'public_research': asdict(profile.release),
    'household_research': asdict(HouseholdResearchProfile(**asdict(profile)).release),
}))
"""


def installed_profiles(container: str, role: str) -> dict:
    result = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-", role],
        input=PROBE, text=True, capture_output=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", default="eva2-managed-b-slot3-core")
    parser.add_argument("--worker", default="eva2-managed-b-slot3-delegated-work-worker-1")
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    core, worker = installed_profiles(args.core, "core"), installed_profiles(args.worker, "worker")
    mismatches = [name for name in sorted(core.keys() | worker.keys())
                  if core.get(name) != worker.get(name)]
    report = {"contract": "EvaInstalledResearchCompatibility/v1",
              "compatible": not mismatches, "mismatched_profiles": mismatches,
              "core": core, "worker": worker,
              "scope": "Shared provider, digital work, private and household public research; no task success claim."}
    if args.write:
        args.write.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("contract", "compatible", "mismatched_profiles", "scope")}))
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
