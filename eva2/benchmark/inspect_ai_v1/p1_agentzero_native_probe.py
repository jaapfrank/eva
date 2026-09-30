"""Pinned native Agent Zero loop/helper/control with mocked SDK responses only.

Run with network disabled. Native presets, agent/context, prompting, model
selection, transport parsing and final response tool remain upstream code.
The SDK seam is mocked; this neither authenticates OAuth nor admits inference.
"""
import argparse
import asyncio
import contextvars
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from functools import wraps

from cp001_accounting import ProviderSampleLedger, SampleStopped

PIN = "e3051fb584b1a36be2b0a0c90606f1c2c2d356ec"
ROLE = contextvars.ContextVar("p1_native_role", default=None)
CONTEXT = contextvars.ContextVar("p1_native_context", default=None)
MOCKS = {}
PROFILES = {"foreground_contact": ("gpt-6-sol", "none"),
    "helper": ("gpt-6-astra", "medium")}


def reject_connection(*args, **kwargs):
    raise RuntimeError("External network is disabled in this native mock probe")


def tag_method(original, role):
    @wraps(original)
    async def wrapped(*args, **kwargs):
        token = ROLE.set(role)
        context_token = CONTEXT.set(args[0].context.id)
        try:
            return await original(*args, **kwargs)
        finally:
            ROLE.reset(token)
            CONTEXT.reset(context_token)
    return wrapped


class MockSDK:
    def __init__(self, ledger, *, block=False):
        self.ledger = ledger
        self.block = block
        self.started = {role: threading.Event() for role in PROFILES}
        self.cancelled = {role: threading.Event() for role in PROFILES}
        self.settled = {role: threading.Event() for role in PROFILES}
        self.requests = []

    async def call(self, mode, **request):
        role = ROLE.get()
        model = request["model"].split("/")[-1]
        effort = request.get("reasoning_effort") or request.get("reasoning", {}).get("effort") or request.get("extra_body", {}).get("reasoning", {}).get("effort")
        attempt = self.ledger.begin(role, model, effort)
        self.requests.append({"role": role, "model": model, "effort": effort,
            "mode": mode, "stream": bool(request.get("stream"))})
        self.started[role].set()
        try:
            if self.block:
                await asyncio.Future()
            text = (json.dumps({"thoughts": [], "tool_name": "response", "tool_args": {"text": "Native protocol fixture completed."}})
                if role == "foreground_contact" else "[]")
            usage = {"input_tokens": 21, "output_tokens": 9, "total_tokens": 30}
            response = {"id": f"resp_fixture_{attempt}", "model": model,
                "status": "completed", "usage": usage,
                "output": [{"type": "message", "id": f"msg_{attempt}", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}]}
            if request.get("stream"):
                async def events():
                    try:
                        if mode == "responses":
                            yield {"type": "response.output_text.delta", "delta": text, "output_index": 0, "content_index": 0}
                            self.ledger.finish(attempt, usage)
                            yield {"type": "response.completed", "response": response}
                        else:
                            yield {"id": response["id"], "model": model, "choices": [{"index": 0, "delta": {"content": text}}]}
                            self.ledger.finish(attempt, usage)
                            yield {"id": response["id"], "model": model, "choices": [], "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30}}
                    except asyncio.CancelledError:
                        self.cancelled[role].set()
                        raise
                    finally:
                        self.ledger.incomplete(attempt, "stream_closed_usage_unknown")
                        self.settled[role].set()
                return events()
            self.ledger.finish(attempt, usage)
            self.settled[role].set()
            if mode == "responses":
                return response
            return {"id": response["id"], "model": model,
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 9, "total_tokens": 30}}
        except asyncio.CancelledError:
            self.ledger.incomplete(attempt, "cancelled_usage_unknown")
            self.cancelled[role].set()
            self.settled[role].set()
            raise
        except BaseException:
            self.ledger.incomplete(attempt, "failed_usage_unknown")
            self.settled[role].set()
            raise

    async def responses(self, **request):
        return await self.call("responses", **request)

    async def completion(self, **request):
        return await self.call("chat", **request)


async def native_responses(**request):
    return await native_dispatch("responses", **request)


async def native_completion(**request):
    return await native_dispatch("chat", **request)


async def native_dispatch(mode, **request):
    mock = MOCKS.get(CONTEXT.get())
    if mock is None:
        raise SampleStopped("Native SDK call has no bound AgentContext")
    return await mock.call(mode, **request)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--embedding-cache", required=True, type=Path)
    parser.add_argument("--dockerized", required=True, choices=["1"])
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != PIN:
        raise ValueError("Native pin changed")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=source).returncode:
        raise ValueError("Native tracked source changed")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Native user data must be fresh")
    if output == source or source in output.parents:
        raise ValueError("Keep synthetic user data separate")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    preset_dir = output / "usr/plugins/_model_config"
    preset_dir.mkdir(parents=True)
    here = Path(__file__).parent
    shutil.copyfile(here / "p1_agentzero_presets.yaml", preset_dir / "presets.yaml")
    shutil.copyfile(here / "p1_agentzero_selection.json", preset_dir / "config.json")
    # Set before transformers imports; sudo does not preserve the caller environment.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_CACHE"] = str(args.embedding_cache.resolve())
    socket.socket.connect = reject_connection
    socket.socket.connect_ex = reject_connection
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from helpers import files, runtime
    runtime.initialize()
    files.USER_DIR = str(output / "usr")
    native_get_abs_path = files.get_abs_path

    def isolated_get_abs_path(*paths):
        resolved = Path(native_get_abs_path(*paths)).resolve()
        try:
            relative = resolved.relative_to(source)
        except ValueError:
            return str(resolved)
        if relative.parts and relative.parts[0] in {"usr", "tmp", "logs"}:
            return str(output / relative)
        return str(resolved)

    # Upstream memory still hardcodes usr/tmp; redirect storage, not behavior.
    files.get_abs_path = isolated_get_abs_path
    workdir = output / "usr/workdir"
    workdir.mkdir(parents=True)
    (output / "usr/settings.json").write_text(json.dumps({"workdir_path": str(workdir)}))
    import agent
    import initialize
    from helpers import litellm_transport as transport
    from helpers.defer import DeferredTask, EventLoopThread
    agent.Agent.call_chat_model_turn = tag_method(agent.Agent.call_chat_model_turn, "foreground_contact")
    agent.Agent.call_chat_model = tag_method(agent.Agent.call_chat_model, "foreground_contact")
    agent.Agent.call_utility_model = tag_method(agent.Agent.call_utility_model, "helper")
    limits = json.loads((here / "p1_participants_20260929.json").read_text())["developmentPlan"]["perSampleLimits"]
    config = initialize.initialize_agent({"mcp_servers": ""})
    # Fail at the actual missing prerequisite before creating native task threads.
    from plugins._model_config.helpers.model_config import build_embedding_model
    embedding = build_embedding_model()
    embedding.embed_query("Synthetic native memory prerequisite")
    transport.aresponses, transport.acompletion = native_responses, native_completion
    results = []
    try:
        ledger = ProviderSampleLedger("native-mock-complete", limits, PROFILES)
        mock = MockSDK(ledger)
        MOCKS["eva-p1-native-complete"] = mock
        context = agent.AgentContext(config=config, id="eva-p1-native-complete")
        # Import the actual active loop extension classes together before work.
        from helpers import extension
        for point in ("monologue_start", "message_loop_start", "system_prompt",
            "message_loop_prompts_after", "before_main_llm_call", "response_stream_chunk",
            "reasoning_stream_chunk", "response_stream_end", "reasoning_stream_end",
            "message_loop_result", "message_loop_end", "monologue_end"):
            extension._get_extension_classes(point, agent=context.agent0)
        task = context.communicate(agent.UserMessage("Complete the offline native protocol check. No external tools."))
        response = task.result_sync(timeout=limits["wallSeconds"])
        assert response == "Native protocol fixture completed.", str(response)
        # Reuse a naturally occurring memory helper if the native loop invoked it.
        # Do not add a third call or disable helpers to fit the declared envelope.
        if not mock.started["helper"].is_set():
            utility = context.run_task(context.agent0.call_utility_model, "Offline utility check", "Return a short fixture result")
            assert utility.result_sync(timeout=limits["wallSeconds"]) == "[]"
        assert mock.settled["helper"].wait(limits["wallSeconds"])
        assert ledger.report()["complete"] and ledger.report()["provider_attempts"] == 2
        results.append({"case": "native_foreground_and_utility", "response": response,
            "helperObserved": True, "ledger": ledger.report(), "requests": mock.requests})
        ledger = ProviderSampleLedger("native-mock-cancel", limits, PROFILES)
        mock = MockSDK(ledger, block=True)
        MOCKS["eva-p1-native-cancel"] = mock
        context = agent.AgentContext(config=config, id="eva-p1-native-cancel")
        from langchain_core.messages import HumanMessage
        # Native direct call control fixture; this does not prove full-monologue
        # or unregistered memory-background cancellation.
        context.run_task(context.agent0.call_chat_model,
            [HumanMessage(content="Wait in the offline control fixture")])
        assert mock.started["foreground_contact"].wait(limits["wallSeconds"])
        child = DeferredTask(thread_name="P1OwnedUtility")
        context.task.add_child_task(child, terminate_thread=True)
        child.start_task(context.agent0.call_utility_model, "Offline helper", "Wait with the parent", background=True)
        assert mock.started["helper"].wait(limits["wallSeconds"])
        context.kill_process()
        assert mock.cancelled["foreground_contact"].wait(limits["wallSeconds"])
        assert mock.cancelled["helper"].wait(limits["wallSeconds"])
        assert not context.is_running() and not child.is_alive()
        assert ledger.report()["total_usage"]["output_tokens"] is None
        results.append({"case": "native_direct_call_parent_and_registered_utility_cancellation",
            "quiescentLocalSDK": True, "ledger": ledger.report(), "requests": mock.requests})
        report = {"sourceCommit": PIN, "scope": "Native full foreground monologue with helpers, and separate direct-call/registered-child stop fixture; SDK is mocked and writable usr/tmp/logs paths are redirected",
            "externalProviderRequests": 0, "mockProviderAttempts": sum(x["ledger"]["provider_attempts"] for x in results), "productionEffects": 0,
            "nativeRuntimeMode": "Co-located --dockerized=1 in a network-isolated host process, not a full Docker installation",
            "cases": results, "liveAdmission": False,
            "remaining": ["Native OAuth login and actual provider usage", "All unregistered/background/embedding provider paths and tool dispatch coverage", "Real provider cancellation and full sample budget admission", "Other participants and complete P1 access"],
            "sourceFiles": {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ["agent.py", "models.py", "helpers/defer.py", "helpers/litellm_transport.py"]}}
        (output / "native-report.json").write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps(report, indent=2))
    except BaseException as error:
        (output / "native-failure.json").write_text(json.dumps({
            "error": type(error).__name__, "detail": str(error),
            "completedCases": results, "lastLedger": ledger.report(),
            "externalProviderRequests": 0, "liveAdmission": False}, indent=2)+"\n")
        raise
    finally:
        for context in list(agent.AgentContext._contexts.values()):
            context.kill_process()
        for name in list(EventLoopThread._instances):
            DeferredTask(thread_name=name).kill(terminate_thread=True)


if __name__ == "__main__":
    main()
