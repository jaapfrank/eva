"""One public-data contract on the pinned native batch/inline executor; no AIAgent startup."""
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import MethodType, SimpleNamespace
import unittest


class NativeToolAccountingContract(unittest.TestCase):
    def test_native_inline_batch_fallback_cap_and_stop(self):
        source = Path(os.environ["HERMES_NATIVE_SOURCE"])
        bench = Path(__file__).resolve().parents[2] / "benchmark" / "inspect_ai_v1"
        with tempfile.TemporaryDirectory(prefix="hermes-public-tool-fixture-") as fixture:
            os.environ["HERMES_HOME"] = fixture
            os.environ["CODEX_HOME"] = str(Path(fixture) / "empty-codex-home")
            sys.path[:0] = [str(bench), str(source)]
            from p1_hermes_startup_probe import HermesSDKAccounting, install_hermes_tool_accounting
            from cp001_accounting import SampleStopped
            from run_agent import AIAgent
            from agent.tool_executor import _ToolCallRef, _resolve_sequential_dispatch
            from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
            from tools.memory_tool import MemoryStore
            import model_tools
            self.assertEqual(list(inspect.signature(AIAgent._execute_tool_calls).parameters),
                ["self", "assistant_message", "messages", "effective_task_id", "api_call_count"])

            class Peer:
                stopped = False
                def stop_async_attempts(self, reason): self.stopped = True
            peer = Peer()
            observer = HermesSDKAccounting(peer, time.monotonic() + 480)
            events, restore, registry_calls = [], [], []
            real_registry = model_tools.handle_function_call
            model_tools.handle_function_call = lambda *a, **k: registry_calls.append(True) or "public fixture registry result"
            actor = AIAgent.__new__(AIAgent)  # No constructor, auth, client, SDK, context or model startup.
            actor.session_id = "public-fixture-session"
            actor._memory_store = MemoryStore(memory_char_limit=10000)
            actor._memory_manager = None
            actor._context_engine_tool_names = set()
            actor.valid_tool_names = ["memory"]
            actor.quiet_mode = False
            native_dispatches = []
            def sequential(actor, message, messages, task_id, api_call_count=0):
                for call in message.tool_calls:
                    ref = _ToolCallRef(call.function.name, json.loads(call.function.arguments), task_id, call.id, [])
                    dispatch = _resolve_sequential_dispatch(actor, ref, messages)
                    native_dispatches.append(call.id)
                    dispatch.execute(ref.args)
            actor._execute_tool_calls_sequential = MethodType(sequential, actor)
            actor._execute_tool_calls_concurrent = MethodType(sequential, actor)
            def batch(n, name="memory"):
                return SimpleNamespace(tool_calls=[SimpleNamespace(id=f"public-{n}-{i}-{len(events)}",
                    function=SimpleNamespace(name=name, arguments=json.dumps({"action":"add", "content":f"Public fixture entry {len(events)} {i}"}))) for i in range(n)])
            try:
                install_hermes_tool_accounting(observer, model_tools, AIAgent, 16, events, restore)
                # Real AIAgent batch/planner signature -> real native inline memory executor/store.
                actor._execute_tool_calls(batch(1), [], "public-task")
                self.assertEqual(len(events), 1)
                self.assertTrue(events[0]["entered"])
                self.assertEqual(len(actor._memory_store.memory_entries), 1)
                self.assertEqual(registry_calls, [])
                # Real native fallback selection reaches the registry hook, without double counting.
                inline = INLINE_TOOL_EXECUTORS.pop("memory")
                try: actor._execute_tool_calls(batch(1), [], "public-task")
                finally: INLINE_TOOL_EXECUTORS["memory"] = inline
                self.assertEqual((len(events), len(registry_calls)), (2, 1))
                actor._execute_tool_calls(batch(14), [], "public-task")
                self.assertEqual(len(events), 16)
                self.assertTrue(all(row["entered"] for row in events))
                before = len(native_dispatches)
                with self.assertRaises(SampleStopped): actor._execute_tool_calls(batch(1), [], "public-task")
                self.assertEqual(len(native_dispatches), before)
                self.assertEqual(len(events), 16)
                # A parent stop is checked before the actual inline body, even for a reserved batch.
                peer.stopped = False
                observer.deadline = time.monotonic() + 480
                events.clear()
                original_sequential = actor._execute_tool_calls_sequential
                def stop_between(actor, *args):
                    peer.stopped = True
                    return original_sequential(*args)
                actor._execute_tool_calls_sequential = MethodType(stop_between, actor)
                memory_before = len(actor._memory_store.memory_entries)
                with self.assertRaises(SampleStopped): actor._execute_tool_calls(batch(1), [], "public-task")
                self.assertFalse(events[0]["entered"])
                self.assertEqual(len(actor._memory_store.memory_entries), memory_before)
                peer.stopped = False
                before = len(native_dispatches)
                with self.assertRaises(SampleStopped): actor._execute_tool_calls(batch(1, "terminal"), [], "public-task")
                self.assertEqual(len(native_dispatches), before)
            finally:
                for target, name, original in reversed(restore): setattr(target, name, original)
                model_tools.handle_function_call = real_registry


if __name__ == "__main__":
    unittest.main()
