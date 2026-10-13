from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel


PATH = Path(__file__).with_name("candidate.py")
SPEC = importlib.util.spec_from_file_location("pydantic_mcp_candidate", PATH)
assert SPEC is not None and SPEC.loader is not None
candidate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = candidate
SPEC.loader.exec_module(candidate)


class ProviderCandidateTests(unittest.TestCase):
    def test_provider_catalogs_are_unrelated_and_provider_owned(self) -> None:
        result = asyncio.run(candidate.provider_smoke())
        self.assertEqual(
            result["environment_tools"],
            ["adjust_space_comfort", "observe_space"],
        )
        self.assertEqual(
            result["focus_tools"],
            ["find_focus_windows", "read_focus_reservation", "reserve_focus_window"],
        )
        self.assertEqual(result["environment_call_count"], 3)
        self.assertEqual(result["environment_mutations"], 1)
        self.assertTrue(result["tool_results_present"])

    def test_agent_instructions_contain_no_provider_or_protocol_names(self) -> None:
        lowered = candidate.AGENT_INSTRUCTIONS.casefold()
        forbidden = (
            "home assistant",
            "unifi",
            "tuya",
            "heater",
            "switch",
            "mcp",
            "api",
            "agenda",
            "calendar",
        )
        self.assertFalse({term for term in forbidden if term in lowered})

    def test_result_evaluation_requires_readback_for_both_effect_providers(
        self,
    ) -> None:
        comfort = {
            "case": "implicit_current_comfort",
            "environment_mutations": 1,
            "environment_readback_after_mutation": True,
            "focus_mutations": 0,
        }
        self.assertTrue(candidate._evaluate(comfort))
        comfort["environment_readback_after_mutation"] = False
        self.assertFalse(candidate._evaluate(comfort))
        focus = {
            "case": "implicit_focus_request",
            "focus_mutations": 1,
            "focus_readback_after_mutation": True,
            "environment_mutations": 0,
        }
        self.assertTrue(candidate._evaluate(focus))
        focus["focus_readback_after_mutation"] = False
        self.assertFalse(candidate._evaluate(focus))

    def test_negative_evaluation_rejects_mutation(self) -> None:
        historical = {
            "case": "historical_observation",
            "environment_mutations": 0,
            "focus_mutations": 0,
        }
        self.assertTrue(candidate._evaluate(historical))
        historical["environment_mutations"] = 1
        self.assertFalse(candidate._evaluate(historical))
        preference = {
            "case": "general_preference",
            "environment_mutations": 0,
            "focus_mutations": 0,
        }
        self.assertTrue(candidate._evaluate(preference))
        preference["focus_mutations"] = 1
        self.assertFalse(candidate._evaluate(preference))
        ambiguous = {
            "case": "ambiguous_current_need",
            "environment_mutations": 0,
            "focus_mutations": 0,
            "output_is_question": True,
        }
        self.assertTrue(candidate._evaluate(ambiguous))
        ambiguous["output_is_question"] = False
        self.assertFalse(candidate._evaluate(ambiguous))

    def test_tool_callback_can_keep_provider_io_behind_evas_ledger(self) -> None:
        state = candidate.EnvironmentState()
        provider = candidate.build_environment_provider(state)
        intercepted: list[tuple[str, dict[str, object]]] = []

        async def ledger_first(_context, _direct_call, name, arguments):
            intercepted.append((name, arguments))
            return {
                "accepted": True,
                "source": "durable-ledger-test-double",
                "provider_io_started": False,
            }

        async def run() -> None:
            agent = Agent(
                TestModel(call_tools=["adjust_space_comfort"]),
                toolsets=[
                    MCPToolset(provider, process_tool_call=ledger_first),
                ],
            )
            await agent.run("Maak de overkapping warmer.")

        asyncio.run(run())
        self.assertEqual(
            intercepted,
            [
                (
                    "adjust_space_comfort",
                    {"space": "a", "direction": "warmer"},
                )
            ],
        )
        self.assertEqual(state.audit.calls, [])
        self.assertEqual(state.audit.successful_mutations, 0)


if __name__ == "__main__":
    unittest.main()
