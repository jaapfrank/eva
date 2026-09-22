"""Explicit, bounded model-only comparison; no action dispatch or live writes.

Run in the isolated candidate container with existing model lease mounted read-only.
Six generate calls, no manual retry. Provider internal retries are counted in usage.
Synthetic context, not an end-to-end voice benchmark or a production prompt replica.
"""
import json
from pathlib import Path
import sys
from time import monotonic

sys.path.extend(["/testdeps", "/hostlibs"])
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    LivingConversation,
)
from tests.application.test_contact_model import IDENTITY, turn
from tests.application.test_conversation import _Memory


class Capture:
    def generate(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        return ConversationModelResult("Captured", "fixture")


def main():
    if sys.argv[1:] != ["--run-model-probe"]:
        raise SystemExit("Explicit --run-model-probe required (six provider generate calls)")
    cases = [("greeting", "Hallo"), ("simple", "Hoeveel is twee plus twee?"),
             ("accepted_work", "Schrijf op de achtergrond een korte opzet voor een buurtfeest.")]
    rows = []
    for name, prompt in cases:
        t = turn(name, prompt)
        capture = Capture()
        LivingConversation(_Memory(), capture, IDENTITY, contact_model=capture).respond(t)
        kwargs = dict(capture.kwargs)
        if name == "accepted_work":
            kwargs["completed_action"] = ConversationActionReceipt(
                ConversationActionCall("probe-start", "start_delegated_work", {"task": prompt}),
                {"status": "verified", "operation_result": {
                    "operation": "start", "work": {"work_id": "work:probe", "state": "admitted"}}})
            # Synthetic receipt, no actual durable task is created by this probe.
        for model_name, effort in [("gpt-6-astra", "medium"), ("gpt-5.6-luna", "low")]:
            model = PydanticConversationModel(
                auth_file=Path("/lease/auth.json"), model=model_name,
                reasoning_effort=effort, timeout_seconds=60,
                conversation_tools=PostgresDelegatedWorkActions.conversation_tools(),
            )
            started = monotonic()
            try:
                result = model.generate(*capture.args, **kwargs)
                row = {"case": name, "model": model_name, "effort": effort,
                       "wall_ms": round((monotonic() - started) * 1000),
                       "timing": result.timing.to_wire(), "usage": result.usage.to_wire(),
                       "text": result.text, "action": None if result.action_call is None else result.action_call.name}
            except Exception as error:
                row = {"case": name, "model": model_name, "error_type": type(error).__name__,
                       "wall_ms": round((monotonic() - started) * 1000), "usage": "unmeasured"}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            Path("/candidate/evidence/product/fast-contact-model-timing-2026-09-22.json").write_text(
                json.dumps({"scope": "six model-only calls; synthetic context and admission receipt; no actions executed",
                            "p95": None, "subscription_cost_eur": None, "rows": rows}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
