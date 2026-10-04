"""Offline native credential checks under a shifted evaluator calendar; no real auth."""
import argparse
import base64
from contextlib import ExitStack
from datetime import datetime, UTC
import hashlib
import inspect
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from unittest.mock import patch

from ev003_python_calendar import ScenarioCalendar, protect_native_auth


def token(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"e30.{payload}.synthetic"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", choices=("core", "hindsight"), required=True)
    args = parser.parse_args()
    real_now = time.time()
    cases = []
    with tempfile.TemporaryDirectory(prefix="eva-calendar-auth-") as temporary, ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")))
        clock = Path(temporary) / "clock.json"
        if args.native == "core":
            from eva.adapters.model.pydantic_conversation import PydanticConversationModel, ConversationModelError
            native = PydanticConversationModel
            original = {name: getattr(native, name).__code__ for name in ("credential_status", "_request_using")}
            protect_native_auth(stack, core_model=native)
            assert all(getattr(native, name).__code__ is code for name, code in original.items())
            instance = object.__new__(native)
            instance._credential_lock = threading.Lock()
            instance._rejected_token_sha256 = None
            instance._admit_request = lambda *a, **kw: None
        else:
            from hindsight_api.engine.providers import codex_auth
            native = codex_auth.CodexAuthManager
            original_code = native._token_is_stale.__code__
            protect_native_auth(stack, memory_auth_module=codex_auth)
            assert native._token_is_stale.__code__ is original_code
        source = Path(inspect.getfile(native))
        for label, offset in (("past", -10 * 365 * 86400_000), ("future", 10 * 365 * 86400_000)):
            clock.write_text(json.dumps({"contract": "EvaScenarioCalendar/v1", "generation": 0, "offsetMilliseconds": offset}))
            with ScenarioCalendar(clock):
                assert abs(datetime.now(UTC).timestamp() - real_now - offset / 1000) < 60
                for expired in (False, True):
                    access = token(int(real_now) + (-3600 if expired else 3600))
                    if args.native == "core":
                        instance._load_auth = lambda: (access, "synthetic-account")
                        status = instance.credential_status()
                        assert status.ready is not expired, status
                        if expired:
                            try:
                                instance._request_using({}, lambda *a: None)
                            except ConversationModelError as error:
                                assert "expired" in str(error), str(error)
                            else:
                                raise AssertionError("expired token admitted")
                    else:
                        instance = native(access, "synthetic-account", None, Path(temporary) / "nonexistent-auth.json")
                        assert instance._token_is_stale() is expired
                    cases.append({"calendar": label, "credential": "expired" if expired else "fresh", "passed": True})
        print(json.dumps({"native": args.native, "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "cases": cases, "real_provider_calls": 0, "credential_files_mounted": False}))


if __name__ == "__main__":
    main()
