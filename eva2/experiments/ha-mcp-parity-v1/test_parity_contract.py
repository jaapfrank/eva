from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ha_mcp_parity_contract", HERE / "check_parity_contract.py"
)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("candidateId",), "other"),
        (("contract",), "Other/v1"),
        (("image",), "ghcr.io/example/other@sha256:00"),
        (("currentDirectProduction", "baselineGitCommit"), "0" * 40),
        (("currentDirectProduction", "files"), {}),
        (("excludedFromReplacement",), []),
        (("excludedMethods",), {}),
        (("mapping",), {}),
        (("nonclaims", "provedCodeRemoval"), True),
        (("promotion", "requiresIndependentReadback"), False),
        (("toolInputSchemaSha256",), {}),
        (("toolDescriptorSha256",), {}),
    ],
)
def test_contract_metadata_mutation_fails_closed(
    path: tuple[str, ...], replacement: Any
) -> None:
    value = json.loads((HERE / "parity-contract.json").read_text(encoding="utf-8"))
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    with pytest.raises(RuntimeError):
        checker.validate_contract(value)
