"""Offline pre-fix reproduction: urllib timeout is not a stream deadline."""
import json
from pathlib import Path
from time import monotonic, sleep
from eva.adapters.model import pydantic_conversation as module

if hasattr(module, "open_research_response"):
    raise SystemExit("This offline reproducer requires the pre-fix adapter at c748391e; do not run against the new transport.")

class SlowResponse:
    closed = False
    def __iter__(self):
        for _ in range(12):
            sleep(0.02)
            yield b'data: {"type":"response.output_text.delta","delta":"x"}\n'
        yield b'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n'
    def close(self): self.closed = True

response = SlowResponse()
calls = []
def offline_open(request, *, timeout):
    calls.append(timeout)
    return response
module.urllib.request.urlopen = offline_open
model = module.PydanticConversationModel(auth_file=Path("/never-read"))
model._load_auth = lambda: ("offline-token", "offline-account")
start = monotonic()
model._request_using({"tools":[{"search_context_size":"low","type":"web_search"}]},
    model._parse_research_sse, timeout_seconds=0.05, include_usage=True,
    provider_managed_research_input=True)
print(json.dumps({"external_requests":0,"simulated_opens":len(calls),
    "timeout_seconds":calls[0],"elapsed_seconds":round(monotonic()-start,3),
    "returned_successfully_after_deadline":True,"response_closed":response.closed},indent=2))
