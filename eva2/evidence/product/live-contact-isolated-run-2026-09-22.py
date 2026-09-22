"""Load candidate modules only in a disposable interpreter, then run one probe.

This does not copy files into the installed core, restart services or modify config.
Invoke explicitly with --candidate. Never run it as an automatic repeated check.
"""
import json
from pathlib import Path
import subprocess
import sys

if sys.argv[1:] != ["--candidate"]:
    raise SystemExit("Expected --candidate (one model-backed greeting)")
root = Path(__file__).resolve().parents[2]
names = ["eva.application.conversation", "eva.adapters.projection.hindsight",
         "eva.adapters.model.pydantic_conversation", "eva.bootstrap.runtime"]
sources = {name: (root / "src" / Path(*name.split("."))).with_suffix(".py").read_text() for name in names}
prefix = """
import sys, importlib.abc, importlib.machinery
class CandidateLoader(importlib.abc.Loader):
    def __init__(self, source, origin): self.source, self.origin = source, origin
    def create_module(self, spec): return None
    def exec_module(self, module): exec(compile(self.source, self.origin, 'exec'), module.__dict__)
class CandidateFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in candidate_sources: return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        spec.loader = CandidateLoader(candidate_sources[fullname], spec.origin)
        return spec
sys.meta_path.insert(0, CandidateFinder())
"""
script = "candidate_sources = " + repr(sources) + "\n" + prefix + Path(__file__).with_name("live-contact-route-probe-2026-09-22.py").read_text()
result = subprocess.run(["docker", "exec", "-i", "eva2-managed-b-slot3-core", "python", "-"],
                        input=script, text=True, capture_output=True)
print(result.stdout)
print(result.stderr[-1500:])
if result.returncode == 0:
    json.loads(result.stdout)
    Path(__file__).with_name("live-contact-route-after-2026-09-22.json").write_text(result.stdout)
raise SystemExit(result.returncode)
