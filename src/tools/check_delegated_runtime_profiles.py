"""Compare installed shared-provider and public-research release fingerprints.

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
print(json.dumps({
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
              "scope": "Shared provider, private and household public research; no task success claim."}
    if args.write:
        args.write.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("contract", "compatible", "mismatched_profiles", "scope")}))
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
