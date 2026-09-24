"""Offline serialized-request check; transport throws before any network access.
Input JSON on stdin contains the candidate provider source and bounded probe source.
Run inside the existing image to retain the installed Pydantic dependency version.
"""
import hashlib
import json
import sys
from pathlib import Path
from eva.adapters.model import openai_codex_provider as provider

payload = json.load(sys.stdin)
exec(compile(payload["provider_source"], provider.__file__, "exec"), provider.__dict__)
source = payload["probe_source"]
source = source.replace('output = Path("/tmp/contact-comparison-20260924.json")',
                        'output = Path("/tmp/contact-profile-offline-intermediate.json")')
a = source.index("original = provider._CodexTransport.handle_async_request")
b = source.index("if sys.argv[1:]", a)
replacement = """class OfflineCapture(Exception): pass
captured = []
async def offline_transport(self, request):
    body = json.loads(await request.aread())
    captured.append(body)
    raise OfflineCapture("Network disabled before transport")
provider._CodexTransport.handle_async_request = offline_transport
"""
source = source[:a] + replacement + source[b:]
source = source.replace('    kwargs=dict(capture.kwargs)',
                        '    kwargs=dict(capture.kwargs)\n    kwargs["available_actions"]=tuple(t.name for t in PostgresDelegatedWorkActions.conversation_tools())')
source = source.replace('        kwargs["completed_action"]=ConversationActionReceipt',
                        '        kwargs["available_actions"]=()\n        kwargs["completed_action"]=ConversationActionReceipt')
source = source.replace('            report["stopped_reason"]="First failed comparison case; no repeated failure or extra discovery calls";save();raise SystemExit(0)', '            pass')
sys.argv = ["offline", "--run-model-probe"]
namespace = {"__name__": "offline_capture"}
exec(compile(source, "bounded-probe-offline", "exec"), namespace)
rows = []
bodies = namespace["captured"]
assert len(bodies) == 6
for index, name in enumerate(("greeting", "small_talk_during_work", "accepted_work")):
    pair = bodies[index * 2:index * 2 + 2]
    normalized = [{k: v for k, v in body.items() if k != "model"} for body in pair]
    differences = sorted(k for k in set(normalized[0]) | set(normalized[1])
                         if normalized[0].get(k) != normalized[1].get(k))
    rows.append({"case": name, "models": [body["model"] for body in pair],
                 "body_difference_keys_except_model": differences,
                 "different_body_fields": {k: [normalized[0].get(k), normalized[1].get(k)] for k in differences},
                 "tools_equal": pair[0].get("tools") == pair[1].get("tools"),
                 "reasoning_equal": pair[0].get("reasoning") == pair[1].get("reasoning"),
                 "reasoning": pair[0].get("reasoning"),
                 "tool_types": [tool["type"] for tool in pair[0].get("tools", [])],
                 "tool_names": [tool.get("name", tool["type"]) for tool in pair[0].get("tools", [])],
                 "deferred_function_count": sum(bool(tool.get("defer_loading")) for tool in pair[0].get("tools", []))})
    assert pair[0].get("tools") == pair[1].get("tools"), name
    assert pair[0].get("reasoning") == pair[1].get("reasoning"), name
    assert set(differences).issubset({"include"}), (name, differences)
result = {"actual_provider_requests": 0, "network_transport_disabled": True,
          "provider_source_sha256": hashlib.sha256(payload["provider_source"].encode()).hexdigest(),
          "scope": "Installed dependency version, candidate source loaded only inside isolated Python process; no runtime installation or live action. Shared synthetic identity, memory tool and all delegated-work descriptors. No latency claim for corrected route.",
          "rows": rows}
Path("/tmp/contact-profile-compatibility-20260924.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
print("OFFLINE_RESULT="+json.dumps(result, ensure_ascii=False))
