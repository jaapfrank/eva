from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest
from pydantic_ai.usage import RequestUsage

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.contracts.canonical_json import canonical_json_bytes
from tests.support.scripted_pydantic_conversation import (
    ScriptedCall,
    ScriptedPhase,
    ScriptedPydanticConversation,
    tool_calls,
    tool_returns,
)
from tools import probe_codex_source_compatibility as probe


COMMIT = "c" * 40
TOKEN = "secret-access-token-must-not-leak"
ACCOUNT = "secret-account-must-not-leak"


def _git_runner(*, commit: str = COMMIT, dirty: str = "") -> Callable:
    def run(command: tuple[str, ...]) -> str:
        if command == ("git", "rev-parse", "HEAD"):
            return commit + "\n"
        if command == (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            return dirty
        raise AssertionError(f"unexpected source command: {command!r}")

    return run


def _valid_lease_reader(observed: dict[str, object]):
    def read(path: str, **scope: object) -> bytes:
        observed["path"] = path
        observed.update(scope)
        return canonical_json_bytes(
            {
                "access_token": TOKEN,
                "account_id": ACCOUNT,
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "a" * 64,
            }
        )

    return read


def _scripted() -> ScriptedPydanticConversation:
    return ScriptedPydanticConversation(
        (
            ScriptedPhase.text(
                "compatibility-",
                "stream-ok",
                usage=RequestUsage(input_tokens=11, output_tokens=3),
            ),
            ScriptedPhase.deferred(
                ScriptedCall(
                    "call:source-compatibility",
                    "probe_candidate_capability",
                    {"capability": "streaming-deferred-tools"},
                ),
                usage=RequestUsage(input_tokens=17, output_tokens=4),
            ),
            ScriptedPhase.text(
                "compatibility-receipt-ok",
                usage=RequestUsage(input_tokens=19, output_tokens=3),
            ),
        )
    )


def test_probe_runs_exactly_three_providerless_phases_without_action_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    budgets: list[object] = []

    def capture_model(**kwargs: object) -> PydanticConversationModel:
        budgets.append(kwargs.get("resource_budget"))
        return PydanticConversationModel(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(probe, "PydanticConversationModel", capture_model)
    monkeypatch.setattr(
        "eva.adapters.model.pydantic_conversation.read_shared_runtime_file_bytes",
        _valid_lease_reader(observed),
    )
    scripted = _scripted()

    receipt = probe.run_probe(
        auth_file=tmp_path / "model-access-lease.json",
        expected_commit=COMMIT,
        expected_source_sha256=probe.source_candidate_sha256(),
        model_backend=scripted.model,
        git_runner=_git_runner(),
    )

    assert receipt["status"] == "passed"
    assert receipt["phase_count"] == 3
    assert receipt["model_attempt_count"] == 3
    assert len(budgets) == 2
    assert budgets[0] is budgets[1]
    assert receipt["resource_budget"]["max_input_tokens"] == 9_000
    assert receipt["resource_budget"]["max_output_tokens"] == 100
    assert receipt["resource_budget"]["unknown_cost_status"] == "blocked"
    assert len(scripted.captures) == 3
    assert scripted.remaining_phases == 0
    assert receipt["phases"]["deferred_tool"]["status"] == (
        "selected_not_executed"
    )
    assert receipt["phases"]["deferred_tool"]["validated_arguments"] == {
        "capability": "streaming-deferred-tools"
    }
    assert scripted.captures[0].offered_tool_names == ()
    assert scripted.captures[1].native_tool_kinds == ("tool_search",)
    assert scripted.captures[2].offered_tool_names == ()
    assert scripted.captures[2].native_tool_kinds == ()
    assert len(tool_calls(scripted.captures[2].messages)) == 1
    assert len(tool_returns(scripted.captures[2].messages)) == 1
    assert tool_returns(scripted.captures[2].messages)[0].content == {
        "contract": "EvaCodexCompatibilitySyntheticReceipt/v1",
        "effect_executed": False,
        "status": "synthetic_only",
    }
    assert observed == {
        "path": str(tmp_path / "model-access-lease.json"),
        "setting_name": "conversation auth lease",
        "owner_uid": 1000,
        "reader_gid": 65532,
        "required_mode": 0o640,
    }


def test_probe_stops_after_provider_reports_over_budget_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "eva.adapters.model.pydantic_conversation.read_shared_runtime_file_bytes",
        _valid_lease_reader({}),
    )
    scripted = ScriptedPydanticConversation(
        (
            ScriptedPhase.text(
                "oversized " * 200,
            ),
            ScriptedPhase.deferred(
                ScriptedCall(
                    "must-not-run",
                    "probe_candidate_capability",
                    {"capability": "streaming-deferred-tools"},
                )
            ),
        )
    )

    with pytest.raises(ConversationModelError):
        probe.run_probe(
            auth_file=tmp_path / "lease.json",
            expected_commit=COMMIT,
            expected_source_sha256=probe.source_candidate_sha256(),
            model_backend=scripted.model,
            git_runner=_git_runner(),
        )

    assert len(scripted.captures) == 1
    assert scripted.remaining_phases == 1


def test_receipt_redacts_lease_prompts_answers_and_wire_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "eva.adapters.model.pydantic_conversation.read_shared_runtime_file_bytes",
        _valid_lease_reader({}),
    )
    receipt = probe.run_probe(
        auth_file=tmp_path / "lease.json",
        expected_commit=COMMIT,
        expected_source_sha256=probe.source_candidate_sha256(),
        model_backend=_scripted().model,
        git_runner=_git_runner(),
    )
    raw = canonical_json_bytes(receipt)

    for forbidden in (
        TOKEN,
        ACCOUNT,
        "Antwoord exact",
        "Selecteer nu",
        "compatibility-stream-ok",
        "compatibility-receipt-ok",
        "Authorization",
        "OpenAI-Account-ID",
    ):
        assert forbidden.encode() not in raw
    assert json.loads(raw)["phases"]["streaming_text"]["delta_count"] == 2


@pytest.mark.parametrize(
    ("lease", "expected_status"),
    [
        (
            {
                "access_token": TOKEN,
                "account_id": ACCOUNT,
                "contract": "WrongLease/v1",
                "source_sha256": "a" * 64,
            },
            "lease_invalid",
        ),
        (
            {
                "access_token": TOKEN,
                "account_id": ACCOUNT,
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": "not-a-hash",
            },
            "lease_invalid",
        ),
    ],
)
def test_probe_fails_closed_on_lease_schema_before_any_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease: dict[str, object],
    expected_status: str,
) -> None:
    monkeypatch.setattr(
        "eva.adapters.model.pydantic_conversation.read_shared_runtime_file_bytes",
        lambda *_args, **_kwargs: canonical_json_bytes(lease),
    )
    scripted = _scripted()

    with pytest.raises(probe.CompatibilityProbeError, match=expected_status):
        probe.run_probe(
            auth_file=tmp_path / "lease.json",
            expected_commit=COMMIT,
            expected_source_sha256=probe.source_candidate_sha256(),
            model_backend=scripted.model,
            git_runner=_git_runner(),
        )

    assert scripted.captures == []


def test_probe_fails_closed_when_existing_lease_scope_check_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def reject_scope(path: str, **scope: object) -> bytes:
        observed["path"] = path
        observed.update(scope)
        raise RuntimeError("private file scope differs")

    monkeypatch.setattr(
        "eva.adapters.model.pydantic_conversation.read_shared_runtime_file_bytes",
        reject_scope,
    )
    scripted = _scripted()

    with pytest.raises(probe.CompatibilityProbeError, match="lease_invalid"):
        probe.run_probe(
            auth_file=tmp_path / "lease.json",
            expected_commit=COMMIT,
            expected_source_sha256=probe.source_candidate_sha256(),
            model_backend=scripted.model,
            git_runner=_git_runner(),
        )

    assert scripted.captures == []
    assert observed["owner_uid"] == 1000
    assert observed["reader_gid"] == 65532
    assert observed["required_mode"] == 0o640


@pytest.mark.parametrize(
    ("commit", "source_hash", "runner", "status"),
    [
        ("bad", "a" * 64, _git_runner(), "expected_commit_invalid"),
        (COMMIT, "bad", _git_runner(), "expected_source_hash_invalid"),
        (COMMIT, "a" * 64, _git_runner(commit="d" * 40), "source_commit_differs"),
        (COMMIT, "a" * 64, _git_runner(dirty=" M source.py\n"), "source_not_clean"),
        (COMMIT, "a" * 64, _git_runner(), "source_hash_differs"),
    ],
)
def test_probe_fails_closed_on_source_commit_cleanliness_and_hash(
    tmp_path: Path,
    commit: str,
    source_hash: str,
    runner: Callable,
    status: str,
) -> None:
    scripted = _scripted()

    with pytest.raises(probe.CompatibilityProbeError, match=status):
        probe.run_probe(
            auth_file=tmp_path / "lease.json",
            expected_commit=commit,
            expected_source_sha256=source_hash,
            model_backend=scripted.model,
            git_runner=runner,
        )

    assert scripted.captures == []
