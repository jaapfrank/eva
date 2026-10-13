from __future__ import annotations

from dataclasses import replace

import pytest

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.installer_budget_workflow import InstallerBudget, InstallerBudgetStatus
from eva.operations.installer_capability_workflow import (
    CapabilityStepStatus,
    InstallerCapabilityStatus,
)
from eva.operations.installer_installation_plan import (
    build_installer_installation_plan,
)
from eva.operations.installer_profile_workflow import (
    InstallerProfile,
    InstallerProfileStatus,
)
from eva.operations.installer_secret_store import InstallerSecretSlotStatus


def _preflight(status: str = "ready") -> dict[str, object]:
    return {
        "contract": "EvaInstallerPreflight/v1",
        "read_only": True,
        "status": status,
    }


def _profile() -> InstallerProfileStatus:
    return InstallerProfileStatus(
        phase="ready",
        revision=2,
        profile_sha256="a" * 64,
        profile=InstallerProfile(
            installation_name="Thuis",
            owner_label="Eigenaar",
            language="nl-NL",
            time_zone="Europe/Amsterdam",
        ),
        updated_at="2026-08-29T04:30:00Z",
    )


def _budget() -> InstallerBudgetStatus:
    budget = InstallerBudget(
        recall_context_tokens=3_000,
        retrieval_nodes=50,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        profile_context_tokens=800,
        memory_records_per_person=2_000,
        memory_bytes_per_person=20_971_520,
        memory_records_per_group=2_000,
        memory_bytes_per_group=20_971_520,
        memory_records_per_project=2_000,
        memory_bytes_per_project=20_971_520,
        memory_records_per_installation=20_000,
        memory_bytes_per_installation=209_715_200,
        retention_days=365,
        background_reflections_per_person_day=4,
        background_reflections_per_installation_day=40,
        external_monthly_spend_cap_eur_cents=0,
    )
    return InstallerBudgetStatus(
        phase="ready",
        revision=2,
        budget_sha256=canonical_sha256(budget.as_dict()),
        budget=budget,
        updated_at="2026-08-29T04:31:00Z",
    )


def _capabilities(
    *, budget_sha256: str | None = None, browser_state: str = "skipped"
) -> InstallerCapabilityStatus:
    states = {
        "model_provider": (True, "ready"),
        "telegram": (False, "skipped"),
        "home_assistant": (False, "skipped"),
        "browser": (False, browser_state),
        "email": (False, "skipped"),
        "adaptive_connector": (False, "skipped"),
        "budgets": (True, "ready"),
    }
    return InstallerCapabilityStatus(
        revision=8,
        head_sha256="b" * 64,
        steps=tuple(
            CapabilityStepStatus(
                step_id=step_id,
                required=required,
                state=state,  # type: ignore[arg-type]
                evidence_sha256=(
                    budget_sha256
                    if step_id == "budgets"
                    else "c" * 64 if state == "ready" else None
                ),
                updated_at="2026-08-29T04:31:00Z",
            )
            for step_id, (required, state) in states.items()
        ),
    )


def _secret(generation: int = 1, state: str = "present") -> tuple[InstallerSecretSlotStatus, ...]:
    return (
        InstallerSecretSlotStatus(
            slot_id="model_provider",
            state=state,  # type: ignore[arg-type]
            reference=("installer-secret://model_provider/v1" if state == "present" else None),
            created_at="2026-08-29T04:32:00Z",
            generation=generation if state == "present" else None,
        ),
    )


def test_plan_is_deterministic_ready_and_contains_no_secret_bytes() -> None:
    budget = _budget()
    arguments = {
        "preflight": _preflight(),
        "identity": load_current_identity_pack(),
        "profile": _profile(),
        "capabilities": _capabilities(budget_sha256=budget.budget_sha256),
        "secrets": _secret(),
        "budgets": budget,
    }
    first = build_installer_installation_plan(**arguments)
    second = build_installer_installation_plan(**arguments)

    assert first == second
    assert first["contract"] == "EvaInstallerInstallationPlan/v2"
    assert first["read_only"] is True
    assert first["status"] == "ready_for_release_preparation"
    assert first["blockers"] == []
    assert first["inputs"]["secret_bindings"] == [  # type: ignore[index]
        {
            "generation": 1,
            "reference": "installer-secret://model_provider/v1",
            "slot_id": "model_provider",
        }
    ]
    assert "synthetic-secret" not in repr(first)


def test_plan_blocks_all_unfinished_state_without_side_effect_claims() -> None:
    plan = build_installer_installation_plan(
        preflight=_preflight("blocked"),
        identity=load_current_identity_pack(),
        profile=None,
        capabilities=None,
        secrets=None,
        budgets=None,
    )

    assert plan["status"] == "blocked"
    assert [item["code"] for item in plan["blockers"]] == [
        "preflight_blocked",
        "profile_not_ready",
        "required_capability_not_ready",
        "budget_profile_not_ready",
    ]
    assert plan["non_claims"][:2] == [
        "Dit plan heeft niets geïnstalleerd of gewijzigd.",
        "Dit plan heeft Docker, containers, databases, diensten en providers niet aangeroepen.",
    ]


def test_plan_blocks_missing_or_interrupted_selected_provider_secret() -> None:
    budget = _budget()
    capabilities = _capabilities(budget_sha256=budget.budget_sha256)
    missing = build_installer_installation_plan(
        preflight=_preflight(),
        identity=load_current_identity_pack(),
        profile=_profile(),
        capabilities=capabilities,
        secrets=(),
        budgets=budget,
    )
    interrupted = build_installer_installation_plan(
        preflight=_preflight(),
        identity=load_current_identity_pack(),
        profile=_profile(),
        capabilities=capabilities,
        secrets=_secret(state="needs_help"),
        budgets=budget,
    )

    assert missing["status"] == "blocked"
    assert missing["blockers"] == [
        {
            "code": "secret_missing",
            "message": "Voer de toegang voor de gekozen koppeling veilig in.",
            "step_id": "model_provider",
        }
    ]
    assert interrupted["blockers"][0]["code"] == "secret_needs_help"


def test_secret_generation_changes_plan_fingerprint() -> None:
    budget = _budget()
    shared = {
        "preflight": _preflight(),
        "identity": load_current_identity_pack(),
        "profile": _profile(),
        "capabilities": _capabilities(budget_sha256=budget.budget_sha256),
        "budgets": budget,
    }
    first = build_installer_installation_plan(**shared, secrets=_secret(1))
    rotated = build_installer_installation_plan(**shared, secrets=_secret(2))

    assert first["plan_sha256"] != rotated["plan_sha256"]
    assert rotated["inputs"]["secret_bindings"][0]["generation"] == 2  # type: ignore[index]


def test_plan_refuses_budget_without_exact_capability_binding() -> None:
    budget = _budget()
    plan = build_installer_installation_plan(
        preflight=_preflight(),
        identity=load_current_identity_pack(),
        profile=_profile(),
        capabilities=_capabilities(budget_sha256="d" * 64),
        secrets=_secret(),
        budgets=budget,
    )

    assert plan["status"] == "blocked"
    assert any(item["code"] == "budget_binding_mismatch" for item in plan["blockers"])
    assert plan["inputs"]["budgets"] is None  # type: ignore[index]


def test_browser_choice_is_bound_to_plan_without_provider_receipt_or_secret() -> None:
    budget = _budget()
    shared = {
        "preflight": _preflight(),
        "identity": load_current_identity_pack(),
        "profile": _profile(),
        "secrets": _secret(),
        "budgets": budget,
    }
    selected = build_installer_installation_plan(
        **shared,
        capabilities=_capabilities(
            budget_sha256=budget.budget_sha256, browser_state="selected"
        ),
    )
    skipped = build_installer_installation_plan(
        **shared,
        capabilities=_capabilities(
            budget_sha256=budget.budget_sha256, browser_state="skipped"
        ),
    )

    for plan in (selected, skipped):
        assert plan["status"] == "ready_for_release_preparation"
        assert plan["blockers"] == []
        assert [item["slot_id"] for item in plan["inputs"]["secret_bindings"]] == [
            "model_provider"
        ]
    assert next(
        item for item in selected["capabilities"] if item["step_id"] == "browser"
    ) == {
        "step_id": "browser",
        "required": False,
        "state": "selected",
        "evidence_sha256": None,
    }
    assert selected["inputs"] == skipped["inputs"]
    assert selected["plan_sha256"] != skipped["plan_sha256"]


def test_browser_still_requires_explicit_selection_or_skip() -> None:
    budget = _budget()
    plan = build_installer_installation_plan(
        preflight=_preflight(),
        identity=load_current_identity_pack(),
        profile=_profile(),
        capabilities=_capabilities(
            budget_sha256=budget.budget_sha256, browser_state="pending"
        ),
        secrets=_secret(),
        budgets=budget,
    )

    assert plan["status"] == "blocked"
    assert [(item["code"], item["step_id"]) for item in plan["blockers"]] == [
        ("capability_pending", "browser")
    ]


@pytest.mark.parametrize(
    "step_id",
    ["model_provider", "telegram", "home_assistant", "email", "adaptive_connector", "budgets"],
)
def test_selected_browser_does_not_bypass_other_capability_verification(
    step_id: str,
) -> None:
    budget = _budget()
    capabilities = _capabilities(
        budget_sha256=budget.budget_sha256, browser_state="selected"
    )
    capabilities = replace(
        capabilities,
        steps=tuple(
            replace(step, state="selected", evidence_sha256=None)
            if step.step_id == step_id else step
            for step in capabilities.steps
        ),
    )
    plan = build_installer_installation_plan(
        preflight=_preflight(),
        identity=load_current_identity_pack(),
        profile=_profile(),
        capabilities=capabilities,
        secrets=_secret(),
        budgets=budget,
    )

    assert plan["status"] == "blocked"
    assert any(
        item["code"] == "capability_selected" and item["step_id"] == step_id
        for item in plan["blockers"]
    )
    assert all(item.get("step_id") != "browser" for item in plan["blockers"])
