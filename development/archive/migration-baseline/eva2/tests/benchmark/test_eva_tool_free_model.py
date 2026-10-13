from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from eva.application.conversation import ConversationModelTiming, ConversationModelUsage


BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import serve_eva_tool_free_model as server  # noqa: E402


def test_decodes_only_the_closed_request_contract() -> None:
    prompt = "Return exact JSON."
    assert server._decode_request(
        json.dumps(
            {"contract": "EvaToolFreeModelRequest/v1", "prompt": prompt}
        ).encode()
    ) == prompt

    with pytest.raises(server.ToolFreeModelServerError):
        server._decode_request(
            json.dumps(
                {
                    "contract": "EvaToolFreeModelRequest/v1",
                    "prompt": prompt,
                    "tools": [],
                }
            ).encode()
        )


def test_reply_preserves_tool_free_usage_and_hash() -> None:
    result = SimpleNamespace(
        provider="chatgpt-codex:test-model",
        text='{"name":"commit_final_answer","arguments":{"answers":["7"]}}',
        timing=ConversationModelTiming(10, 20),
        usage=ConversationModelUsage.measured(
            model_attempts=1, tool_calls=0, input_tokens=12, output_tokens=8
        ),
    )

    class Model:
        def generate_tool_free_text(self, *, prompt: str, instructions: str):
            assert prompt == "bounded prompt"
            assert "no tools" in instructions
            return result

    reply = server._reply(Model(), "bounded prompt")  # type: ignore[arg-type]

    assert reply["usage"]["tool_calls"] == 0
    assert reply["tools_exposed"] == 0
    assert reply["text_sha256"] == hashlib.sha256(result.text.encode()).hexdigest()
