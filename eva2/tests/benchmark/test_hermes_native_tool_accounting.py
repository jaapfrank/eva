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
    def test_original_packet_retains_all_turns_and_rejects_reordered_input(self):
        # Exercise the packet boundary without loading a provider or private case.
        import ast
        probe = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1/p1_hermes_startup_probe.py"
        definition = next(n for n in ast.parse(probe.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == "native_hermes_turns")
        namespace = {}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(probe), "exec"), namespace)
        decode = namespace["native_hermes_turns"]
        for count in (3, 4):
            turns = [{"ordinal": n, "public_payload": {"unchanged": n}} for n in range(count)]
            self.assertIs(decode({"turns": turns}), turns)
        for malformed in ([], {}, [{"ordinal": 0}, {"ordinal": 0}],
                [{"ordinal": 1}, {"ordinal": 0}], [{"ordinal": 0}, {"ordinal": 2}],
                [{"ordinal": False}], [{}]):
            with self.assertRaises(ValueError):
                decode({"turns": malformed})

    def test_native_origins_share_profile_without_added_privacy_policy(self):
        source = Path(os.environ["HERMES_NATIVE_SOURCE"])
        bench = Path(__file__).resolve().parents[2] / "benchmark" / "inspect_ai_v1"
        with tempfile.TemporaryDirectory(prefix="hermes-public-origin-fixture-", dir="/tmp") as fixture:
            profile = Path(fixture)
            os.environ["HERMES_HOME"] = fixture
            os.environ["CODEX_HOME"] = str(profile / "empty-codex-home")
            sys.path[:0] = [str(bench), str(source)]
            from p1_hermes_startup_probe import native_hermes_identity, readback_hermes_identity
            from run_agent import AIAgent
            from hermes_state import SessionDB
            from tools.memory_tool import get_memory_dir
            from unittest.mock import patch

            participant = {"provisioning": {"actors": [
                {"actor_index": 0, "user_id": "public-person-a"},
                {"actor_index": 1, "user_id": "public-person-b"}]}}
            turns = [{"actor_index": actor_index, "native_identity": {
                "user_id": f"public-person-{letter}", "chat_id": f"public-dm-{letter}",
                "chat_type": "dm", "gateway_session_key": f"cli:dm:public-dm-{letter}"}}
                for actor_index, letter in ((0, "a"), (1, "b"), (0, "a"))]
            shared = profile / "memories"
            shared.mkdir()
            (shared / "MEMORY.md").write_text("Public shared memory sentinel")
            db = SessionDB(profile / "state.db")
            try:
                with patch("socket.socket.connect", side_effect=AssertionError("fixture must not use network")):
                    for n, turn in enumerate(turns):
                        identity = native_hermes_identity(participant, turn)
                        actor = AIAgent(model="gpt-6-sol", provider="openai-codex",
                            api_key="public-test-not-a-secret", base_url="http://127.0.0.1:9",
                            reasoning_config={"effort": "none"}, enabled_toolsets=["memory"],
                            quiet_mode=True, load_soul_identity=True, skip_memory=False,
                            skip_background_review=False, session_id=f"public-origin-session-{n}",
                            session_db=db, cwd=fixture, **identity)
                        try:
                            self.assertEqual(actor._user_id, identity["user_id"])
                            self.assertEqual(actor._chat_id, identity["chat_id"])
                            self.assertEqual(actor._chat_type, "dm")
                            self.assertEqual(actor._gateway_session_key, identity["gateway_session_key"])
                            actor._ensure_db_session()
                            row = db.get_session(actor.session_id)
                            readback_hermes_identity({"session": row}, identity)
                            self.assertEqual(get_memory_dir(), shared)
                            self.assertEqual((shared / "MEMORY.md").read_text(), "Public shared memory sentinel")
                            wrong = dict(identity, user_id="public-wrong-person")
                            with self.assertRaisesRegex(ValueError, "person/audience differs"):
                                readback_hermes_identity({"session": row}, wrong)
                        finally:
                            actor.close()
                with self.assertRaisesRegex(ValueError, "fixed actor fallback"):
                    native_hermes_identity(participant, {})
                with self.assertRaisesRegex(ValueError, "original actor binding"):
                    native_hermes_identity(participant, dict(turns[0], actor_index=1))
                malformed = dict(turns[0]["native_identity"])
                del malformed["chat_id"]
                with self.assertRaisesRegex(ValueError, "every original origin field"):
                    native_hermes_identity(participant, dict(turns[0], native_identity=malformed))
            finally:
                db.close()

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
