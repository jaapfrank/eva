"""One synthetic model decision, no tool dispatch, real journal or message delivery."""
import json
import sys
from pathlib import Path
from time import monotonic
sys.path[:0] = ["/workspace/src", "/workspace"]
sys.path.extend(["/testdeps", "/hostlibs"])
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationModelResult, LivingConversation
from eva.contracts.runtime_budget import RuntimeResourceBudget
from tests.application.test_contact_model import IDENTITY, turn
from tests.application.test_conversation import _Memory

class Capture:
    def generate(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        return ConversationModelResult("Captured", "fixture")

if sys.argv[1:] != ["--run-one-decision"]:
    raise SystemExit("Explicit --run-one-decision required")
question = "Ik wil een gedegen advies over XRP. Onderzoek ook actuele betaalgroei, netto-aanboddruk en orderboekdiepte voordat je tot een conclusie komt."
capture = Capture()
LivingConversation(_Memory(), capture, IDENTITY).respond(turn("research-gap-probe", question))
call = ConversationActionCall("synthetic-completed-research", "research_current_options", {"question":question,"title":"Analyse","options":["XRP"],"research_mode":"topic","public_review":"not_applicable"})
receipt = ConversationActionReceipt(call, {"status":"verified","verification_scope":"research_artifact_only","artifact":{"title":"Analyse","content":"De geraadpleegde productpagina zegt dat Ripple Payments ook stablecoins ondersteunt. Actuele betaalgroei, netto-aanboddruk en orderboekdiepte zijn niet onderzocht. Voorwaarde voor een gedegen advies: eerst die gegevens controleren.","sources":[{"title":"Ripple Payments","url":"https://ripple.com/products/cross-border-payments/"}]}})
policy = json.loads(Path("/run/eva/household-voice/current.json").read_text())
budget = RuntimeResourceBudget.from_bytes(Path("/run/eva/runtime-budget/current.json").read_bytes(), expected_tenant_id=policy["tenant_id"],expected_installation_id=policy["installation_id"])
model = PydanticConversationModel(auth_file=Path("/run/eva/model-lease/auth.json"),model="gpt-6-astra",reasoning_effort="medium",timeout_seconds=60,resource_budget=budget)
kwargs = dict(capture.kwargs)
kwargs.update(completed_action=receipt,available_actions=("research_current_options",))
started=monotonic()
result=model.generate(*capture.args,**kwargs)
print(json.dumps({"scope":"one synthetic continuation decision; no tool executed","wall_ms":round((monotonic()-started)*1000),"action":None if result.action_call is None else result.action_call.name,"arguments":None if result.action_call is None else result.action_call.arguments,"text":result.text,"usage":result.usage.to_wire(),"timing":result.timing.to_wire(),"cost_eur":None},ensure_ascii=False))
