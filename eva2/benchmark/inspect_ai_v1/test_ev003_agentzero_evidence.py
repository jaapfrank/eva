"""Focused checks using pinned native auth code and actual FAISS disk format.

Run with the existing Agent Zero environment and the pinned calendar dependency
on PYTHONPATH. No real credentials, provider calls or sealed scenarios are used.
"""
import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pickle
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path("/home/administrator/project/.eva2-artifacts/p1-participants-20260929/agent-zero-source")
sys.path.insert(0, str(SOURCE))
from plugins._oauth.helpers import codex
from ev003_python_calendar import ScenarioCalendar, protect_native_auth
from ev003_agentzero_readback import read_agentzero_snapshot


def synthetic_jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return "synthetic." + payload + ".not-a-real-signature"


class NativeEvidenceTests(unittest.TestCase):
    def test_native_credential_checks_keep_real_time_and_restore(self):
        real_now = datetime.now(timezone.utc)
        fresh = real_now.isoformat()
        valid = synthetic_jwt(real_now.timestamp() + 3600)
        expired = synthetic_jwt(real_now.timestamp() - 60)
        old_refresh = (real_now - timedelta(hours=2)).isoformat()
        before = {name: getattr(codex, name) for name in ("should_refresh", "utc_now_iso")}
        self.assertFalse(codex.should_refresh(valid, fresh))
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder) / "clock.json"
            control.write_text(json.dumps({"contract": "EvaScenarioCalendar/v1", "generation": 0,
                                           "offsetMilliseconds": 30 * 86400 * 1000}))
            with ScenarioCalendar(control):
                self.assertTrue(codex.should_refresh(valid, fresh))  # Native bug without protection.
                with ExitStack() as stack:
                    protect_native_auth(stack, agentzero_auth_module=codex)
                    self.assertFalse(codex.should_refresh(valid, fresh))
                    self.assertTrue(codex.should_refresh(expired, fresh))
                    self.assertTrue(codex.should_refresh(valid, old_refresh))
                    stamp = datetime.fromisoformat(codex.utc_now_iso().replace("Z", "+00:00"))
                    self.assertLess(abs((stamp - real_now).total_seconds()), 5)
                    for name, original in before.items():
                        self.assertIs(getattr(codex, name).__code__, original.__code__)
                self.assertTrue(codex.should_refresh(valid, fresh))
        for name, original in before.items():
            self.assertIs(getattr(codex, name), original)

    def test_disk_snapshot_ignores_live_docstore_and_wrong_context(self):
        import faiss
        import numpy as np
        from langchain_community.docstore.in_memory import InMemoryDocstore
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        from langchain_core.embeddings import Embeddings
        from plugins._memory.helpers.memory import Memory

        class LocalEmbedding(Embeddings):
            def embed_documents(self, texts):
                raise AssertionError("Readback must not embed anything")
            def embed_query(self, text):
                raise AssertionError("Readback must not embed anything")

        with tempfile.TemporaryDirectory() as folder:
            user_dir = Path(folder) / "usr"
            chat = user_dir / "chats/session-a/chat.json"
            chat.parent.mkdir(parents=True)
            chat.write_text(json.dumps({"id": "session-a", "log": {"logs": [
                {"type": "response", "agentno": 0, "content": "Public fixture reply"},
                {"type": "response", "agentno": 1, "content": "Subordinate response"}]}}))
            empty = read_agentzero_snapshot(user_dir=user_dir, context_id="session-a")
            self.assertFalse(empty["memory_index"]["saved"])
            self.assertEqual(empty["reply_text"], "Public fixture reply")
            vector = faiss.IndexFlatL2(2)
            vector.add(np.array([[1.0, 0.0]], dtype="float32"))
            document = Document(page_content="Public fixture memory", metadata={"id": "m1"})
            store = InMemoryDocstore({"m1": document})
            db = FAISS(LocalEmbedding(), vector, store, {0: "m1"})
            memory_dir = user_dir / "memory/default"
            with patch("plugins._memory.helpers.memory.abs_db_dir", return_value=str(memory_dir)):
                Memory._save_db_file(db, "default")
            document.page_content = "Changed only in process cache"
            snapshot = read_agentzero_snapshot(user_dir=user_dir, context_id="session-a")
            self.assertEqual(snapshot["memory_records"][0]["content"], "Public fixture memory")
            self.assertTrue(snapshot["memory_index"]["native_hash_matches"])
            self.assertTrue(snapshot["memory_index"]["mapping_matches_documents"])
            self.assertEqual(len(snapshot["file_hashes"]), 4)
            chat.write_text(json.dumps({"id": "different-actor-context", "log": {"logs": []}}))
            with self.assertRaisesRegex(ValueError, "context ID differs"):
                read_agentzero_snapshot(user_dir=user_dir, context_id="session-a")

    def test_unexpected_pickle_global_is_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            user_dir = Path(folder) / "usr"
            memory = user_dir / "memory/default"
            memory.mkdir(parents=True)
            (memory / "index.faiss").write_bytes(b"isolated-fixture-vector")
            (memory / "index.pkl").write_bytes(pickle.dumps((eval, {})))
            with self.assertRaises(pickle.UnpicklingError):
                read_agentzero_snapshot(user_dir=user_dir, context_id="session-a")


if __name__ == "__main__":
    unittest.main()
