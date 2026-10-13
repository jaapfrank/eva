from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import sys
import unittest

from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolResults, ToolDefinition


sys.path.insert(0, str(Path(__file__).resolve().parent))

from candidate import (  # noqa: E402
    DeferredBoundaryError,
    DeferredSelection,
    DurableActionReceipt,
    SelectedAction,
    rebuild_message_history,
    resume_after_receipt,
    select_one_action,
)


TOOL = ToolDefinition(
    name="adjust_space_comfort",
    description="Make one named space warmer and return acceptance evidence.",
    parameters_json_schema={
        "additionalProperties": False,
        "properties": {
            "direction": {"enum": ["warmer", "cooler"], "type": "string"},
            "space": {"type": "string"},
        },
        "required": ["direction", "space"],
        "type": "object",
    },
    strict=True,
)


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


class ScriptedModel:
    def __init__(self, *, calls_in_first_phase: int = 1) -> None:
        self.requests = 0
        self.offered_tool_names: list[tuple[str, ...]] = []
        self.calls_in_first_phase = calls_in_first_phase
        self.model = FunctionModel(self._respond, model_name="providerless-script")

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests += 1
        self.offered_tool_names.append(tuple(item.name for item in info.function_tools))
        returns = _returns(messages)
        if returns:
            self.assert_no_tools_in_receipt_phase(info)
            return ModelResponse(parts=[TextPart("De overkapping wordt nu warmer.")])
        calls = [
            ToolCallPart(
                "adjust_space_comfort",
                {"direction": "warmer", "space": "overkapping"},
                f"call-{index + 1}",
            )
            for index in range(self.calls_in_first_phase)
        ]
        return ModelResponse(parts=calls)

    @staticmethod
    def assert_no_tools_in_receipt_phase(info: AgentInfo) -> None:
        if info.function_tools:
            raise AssertionError("receipt phase offered an executable tool")


class DeferredExternalToolPrototypeTests(unittest.TestCase):
    def test_exact_framework_version_is_used(self) -> None:
        self.assertEqual(version("pydantic-ai-slim"), "2.36.0")

    def test_roundtrip_stops_for_external_execution_and_resumes_once(self) -> None:
        scripted = ScriptedModel()
        selected = select_one_action(
            scripted.model,
            prompt="Het is koud in de overkapping.",
            tools=[TOOL],
        )
        self.assertIsInstance(selected, DeferredSelection)
        assert isinstance(selected, DeferredSelection)
        self.assertEqual(selected.model_requests, 1)
        self.assertEqual(selected.external_execution_attempts, 0)
        self.assertEqual(selected.call.name, "adjust_space_comfort")
        self.assertEqual(selected.call.arguments["space"], "overkapping")

        # This is the only stand-in for ConversationActionPort.execute().
        durable_executor_calls = 1
        receipt = DurableActionReceipt(
            selected.call,
            {"accepted": True, "observed_temperature_c": 20.0},
        )
        answer = resume_after_receipt(
            scripted.model,
            prompt="Het is koud in de overkapping.",
            receipt=receipt,
            message_history=selected.message_history,
        )

        self.assertEqual(answer, "De overkapping wordt nu warmer.")
        self.assertEqual(durable_executor_calls, 1)
        self.assertEqual(scripted.requests, 2)
        self.assertEqual(
            scripted.offered_tool_names,
            [("adjust_space_comfort",), ()],
        )

    def test_crash_resume_rebuilds_history_from_durable_call(self) -> None:
        scripted = ScriptedModel()
        call = SelectedAction(
            "durable-call-7",
            "adjust_space_comfort",
            {"direction": "warmer", "space": "overkapping"},
        )
        history = rebuild_message_history(
            prompt="Het is koud in de overkapping.",
            call=call,
        )
        self.assertEqual(
            [type(item).__name__ for item in history],
            [
                "ModelRequest",
                "ModelResponse",
            ],
        )
        answer = resume_after_receipt(
            scripted.model,
            prompt="Het is koud in de overkapping.",
            receipt=DurableActionReceipt(call, {"accepted": True}),
        )
        self.assertEqual(answer, "De overkapping wordt nu warmer.")
        self.assertEqual(scripted.requests, 1)
        self.assertEqual(scripted.offered_tool_names, [()])

    def test_multiple_deferred_calls_are_rejected_before_execution(self) -> None:
        scripted = ScriptedModel(calls_in_first_phase=2)
        with self.assertRaisesRegex(DeferredBoundaryError, "exactly one external call"):
            select_one_action(
                scripted.model,
                prompt="Maak het hier comfortabel.",
                tools=[TOOL],
            )
        self.assertEqual(scripted.requests, 1)

    def test_pydantic_rejects_a_result_for_the_wrong_call_id(self) -> None:
        scripted = ScriptedModel()
        selected = select_one_action(
            scripted.model,
            prompt="Het is koud in de overkapping.",
            tools=[TOOL],
        )
        assert isinstance(selected, DeferredSelection)
        agent = Agent(scripted.model, retries=0)
        with self.assertRaisesRegex(UserError, "need to be provided"):
            agent.run_sync(
                None,
                output_type=str,
                message_history=selected.message_history,
                deferred_tool_results=DeferredToolResults(
                    calls={"wrong-call-id": {"accepted": True}}
                ),
            )

    def test_receipt_must_match_the_selected_name_and_arguments(self) -> None:
        scripted = ScriptedModel()
        selected = select_one_action(
            scripted.model,
            prompt="Het is koud in de overkapping.",
            tools=[TOOL],
        )
        assert isinstance(selected, DeferredSelection)
        altered = SelectedAction(
            selected.call.call_id,
            selected.call.name,
            {"direction": "cooler", "space": "overkapping"},
        )
        with self.assertRaisesRegex(DeferredBoundaryError, "continuation call differs"):
            resume_after_receipt(
                scripted.model,
                prompt="Het is koud in de overkapping.",
                receipt=DurableActionReceipt(altered, {"accepted": True}),
                message_history=selected.message_history,
            )
        self.assertEqual(scripted.requests, 1)


if __name__ == "__main__":
    unittest.main()
