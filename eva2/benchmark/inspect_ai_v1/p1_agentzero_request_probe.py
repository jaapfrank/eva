"""Offline request-shaping evidence for the pinned Agent Zero participant.

Run inside a network-disabled namespace with an empty HOME and the pinned native
checkout on PYTHONPATH. This exercises request preparation, not agent inference,
provider enforcement, authentication, or cumulative task accounting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from plugins._oauth.helpers import codex, config


def main() -> None:
    original_config = codex.codex_config
    results = []
    cases = (
        ("contact_config_only", "gpt-6-sol", "none", {}),
        ("contact_explicit_effort", "gpt-6-sol", "none", {"reasoning_effort": "none"}),
        ("contact_explicit_reasoning", "gpt-6-sol", "none", {"reasoning": {"effort": "none"}}),
        ("work_config_only", "gpt-6-astra", "medium", {}),
    )
    try:
        for name, model, effort, extra in cases:
            # Normalize the real configuration before injecting its lookup. This
            # intentionally avoids disk configuration and all credential lookup.
            settings = config.codex_config({"codex": {
                "reasoning_effort": effort, "reasoning_summary": "off",
            }})
            codex.codex_config = lambda: settings
            request = {
                "model": model, "input": [], "instructions": "Offline probe",
                "max_output_tokens": 256, **extra,
            }
            prepared = codex.prepare_responses_body(request, force_stream=True)
            results.append({
                "case": name,
                "requested_effort": effort,
                "normalized_config_effort": settings["reasoning_effort"],
                "prepared_model": prepared["model"],
                "prepared_reasoning": prepared.get("reasoning"),
                "max_output_tokens_preserved": "max_output_tokens" in prepared,
                "original_output_limit_unchanged": request["max_output_tokens"] == 256,
            })
    finally:
        codex.codex_config = original_config
    hashes = {}
    for module in (codex, config):
        path = Path(module.__file__)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps({
        "scope": "Native configuration normalization and OAuth request preparation only",
        "provider_calls": 0,
        "source_sha256": hashes,
        "cases": results,
    }, indent=2))


if __name__ == "__main__":
    main()
