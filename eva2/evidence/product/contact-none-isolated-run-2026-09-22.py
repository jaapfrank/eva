"""One authorized none route probe; no deployed file or configuration changes."""
import json
from pathlib import Path
import subprocess
import sys

if sys.argv[1:] != ["--candidate"]:
    raise SystemExit("Expected --candidate (one model-backed greeting)")
root = Path(__file__).resolve().parents[2]
names = ["eva.application.conversation", "eva.adapters.projection.hindsight",
         "eva.adapters.model.pydantic_conversation", "eva.adapters.model.openai_codex_provider",
         "eva.bootstrap.runtime"]
sources = {name: (root / "src" / Path(*name.split("."))).with_suffix(".py").read_text() for name in names}
loader = Path(__file__).with_name("live-contact-isolated-run-2026-09-22.py").read_text()
prefix = loader.split('prefix = """', 1)[1].split('"""', 1)[0]
instrument = '''
import os
os.environ["EVA_CONVERSATION_CONTACT_MODEL"] = "gpt-5.6-luna"
os.environ["EVA_CONVERSATION_CONTACT_REASONING_EFFORT"] = "none"
from eva.adapters.model import openai_codex_provider as provider_module
original_projection = provider_module._codex_compatible_request
requests = []
def observed_projection(request):
    if requests:
        raise RuntimeError("one-request route probe budget exhausted")
    request = original_projection(request)
    body = json.loads(request.content)
    if body["model"] != "gpt-5.6-luna" or body["reasoning"]["effort"] != "none":
        raise RuntimeError("route probe must dispatch Luna none")
    requests.append({"model":body["model"], "reasoning":body["reasoning"], "tools":len(body.get("tools", []))})
    outputs.append({"wire_request":requests[-1]})
    return request
provider_module._codex_compatible_request = observed_projection
'''
probe = Path(__file__).with_name("live-contact-route-probe-2026-09-22.py").read_text()
probe = probe.replace("asyncio.run(main())", instrument + "\nasyncio.run(main())")
script = "import json\ncandidate_sources = " + repr(sources) + "\n" + prefix + probe
result = subprocess.run(["docker", "exec", "-i", "eva2-managed-b-slot3-core", "python", "-"],
                        input=script, text=True, capture_output=True)
print(result.stdout)
print(result.stderr[-1500:])
if result.returncode == 0:
    json.loads(result.stdout)
    Path(__file__).with_name("contact-none-route-after-2026-09-22.json").write_text(result.stdout)
raise SystemExit(result.returncode)
