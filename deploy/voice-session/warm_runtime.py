"""Prepare local model libraries before accepting requests; never call a model.

Invoke with the existing Python -m entrypoint arguments after this file.
Credentials, provider sessions and remote requests remain request-owned.
"""
import runpy
import sys
from time import monotonic


def prepare():
    from eva.adapters.model.pydantic_conversation import _input_tokenizer
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.native_tools import NATIVE_TOOL_TYPES
    from pydantic_ai.profiles.openai import openai_model_profile
    from pydantic_ai.providers.openai import OpenAIProvider
    _input_tokenizer()


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "-m":
        raise SystemExit("Expected the existing -m module entrypoint")
    started = monotonic()
    prepare()
    print(f"Local model libraries ready in {monotonic() - started:.3f}s; no model request", flush=True)
    sys.argv = sys.argv[2:]
    runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
