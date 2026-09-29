"""Save and resolve pinned Agent Zero presets offline, without creating models.

Run under an isolated network namespace. The output directory must be empty.
Only the native user-data root is redirected; preset validation, persistence,
selection, and ModelConfig construction use unmodified upstream code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

PIN = "e3051fb584b1a36be2b0a0c90606f1c2c2d356ec"


def reject_connection(*args, **kwargs):
    raise RuntimeError("Network connections disabled in preset probe")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() != PIN:
        raise ValueError("Agent Zero source pin differs")
    if output == source or source in output.parents or (output.exists() and any(output.iterdir())):
        raise ValueError("Use a separate empty output directory")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    socket.socket.connect = reject_connection
    socket.socket.connect_ex = reject_connection
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from helpers import files
    files.USER_DIR = str(output)
    from plugins._model_config.helpers import model_config as native
    import models

    work = {"provider": "codex_oauth", "name": "gpt-6-astra",
            "kwargs": {"reasoning_effort": "medium"}}
    contact = {"provider": "codex_oauth", "name": "gpt-6-sol",
               "kwargs": {"extra_body": {"reasoning": {"effort": "none"}}}}
    # An embedding identity is required by the native schema. It is recorded,
    # never instantiated or downloaded by this configuration-only probe.
    embedding = {"provider": "huggingface", "name": "sentence-transformers/all-MiniLM-L6-v2", "kwargs": {}}
    presets = [{"name": "Default", "chat": contact, "utility": work,
                "vision": work, "embedding": embedding},
               {"name": "Eva Research", "chat": work, "utility": work, "vision": work}]
    native.save_presets(presets)
    native.cache.clear(native.PRESETS_CACHE_AREA)
    saved = Path(native._get_presets_path()).resolve()
    if output not in saved.parents:
        raise ValueError("Native preset was saved outside isolated user data")
    observations = []
    for name, expected in [("Default", contact), ("Eva Research", work)]:
        resolved = native.resolve_config_settings({"model_preset": name})
        for slot, wanted in [("chat_model", expected), ("utility_model", work), ("vision_model", work)]:
            config = native.build_model_config(resolved[slot], models.ModelType.CHAT)
            assert config.provider == wanted["provider"]
            assert config.name == wanted["name"]
            assert config.kwargs == wanted["kwargs"]
            observations.append({"preset": name, "slot": slot, "provider": config.provider,
                                 "model": config.name, "kwargs": config.kwargs})
    print(json.dumps({"sourceCommit": PIN, "savedPresetSha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
        "savedPreset": str(saved), "observations": observations, "providerCalls": 0,
        "scope": "Native persisted preset save/load and ModelConfig construction only",
        "limits": ["No agent loop, authentication, helper execution or cumulative accounting verified",
                   "Embedding model is a schema identity only; not loaded or admitted"]}, indent=2))


if __name__ == "__main__":
    main()
