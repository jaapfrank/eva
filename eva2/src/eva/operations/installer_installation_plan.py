"""Build a deterministic, read-only installation plan from trusted wizard state."""

from __future__ import annotations

from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPack
from eva.operations.installer_budget_workflow import InstallerBudgetStatus
from eva.operations.installer_capability_workflow import InstallerCapabilityStatus
from eva.operations.installer_profile_workflow import InstallerProfileStatus
from eva.operations.installer_secret_store import InstallerSecretSlotStatus


_PROVIDER_STEPS = frozenset(
    {"model_provider", "telegram", "home_assistant", "email"}
)
_BLOCKER_MESSAGES = {
    "preflight_blocked": "De controle van deze computer is nog niet groen.",
    "profile_not_ready": "Bevestig eerst naam, taal en tijdzone van deze installatie.",
    "capability_pending": "Kies of sla iedere optionele koppeling bewust over.",
    "capability_selected": "Controleer de gekozen koppeling voordat je installeert.",
    "capability_blocked": "Herstel de mislukte koppelingscontrole.",
    "required_capability_not_ready": "Een verplichte installatiestap is nog niet klaar.",
    "secret_missing": "Voer de toegang voor de gekozen koppeling veilig in.",
    "secret_needs_help": "Rond de onderbroken wijziging van een koppeling eerst af.",
    "budget_profile_not_ready": "Bewaar en bevestig eerst de harde gebruikslimieten.",
    "budget_binding_mismatch": "De bevestigde limieten horen niet bij de groene budgetcontrole.",
}


def _blocker(code: str, *, step_id: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"code": code, "message": _BLOCKER_MESSAGES[code]}
    if step_id is not None:
        result["step_id"] = step_id
    return result


def build_installer_installation_plan(
    *,
    preflight: dict[str, object],
    identity: IdentityPack,
    profile: InstallerProfileStatus | None,
    capabilities: InstallerCapabilityStatus | None,
    secrets: tuple[InstallerSecretSlotStatus, ...] | None,
    budgets: InstallerBudgetStatus | None = None,
) -> dict[str, object]:
    """Return a fail-closed plan without reading secrets or changing the host."""

    blockers: list[dict[str, object]] = []
    if preflight.get("status") != "ready":
        blockers.append(_blocker("preflight_blocked"))

    profile_payload: dict[str, object] | None = None
    if profile is None or profile.phase != "ready" or profile.profile is None:
        blockers.append(_blocker("profile_not_ready"))
    else:
        profile_payload = {
            "profile": profile.profile.as_dict(),
            "profile_sha256": profile.profile_sha256,
            "revision": profile.revision,
        }

    secret_by_slot = {item.slot_id: item for item in secrets or ()}
    capability_payload: list[dict[str, object]] = []
    secret_bindings: list[dict[str, object]] = []
    if capabilities is None:
        blockers.append(
            _blocker("required_capability_not_ready", step_id="capabilities")
        )
        capability_revision = 0
        capability_head = None
    else:
        capability_revision = capabilities.revision
        capability_head = capabilities.head_sha256
        for step in capabilities.steps:
            capability_payload.append(
                {
                    "evidence_sha256": step.evidence_sha256,
                    "required": step.required,
                    "state": step.state,
                    "step_id": step.step_id,
                }
            )
            if step.state == "pending":
                blockers.append(_blocker("capability_pending", step_id=step.step_id))
            elif step.state == "selected":
                blockers.append(
                    _blocker("capability_selected", step_id=step.step_id)
                )
            elif step.state == "blocked":
                blockers.append(_blocker("capability_blocked", step_id=step.step_id))
            elif step.required and step.state != "ready":
                blockers.append(
                    _blocker("required_capability_not_ready", step_id=step.step_id)
                )

            if step.step_id not in _PROVIDER_STEPS or step.state != "ready":
                continue
            secret = secret_by_slot.get(step.step_id)
            if secret is None or secret.state == "missing":
                blockers.append(_blocker("secret_missing", step_id=step.step_id))
            elif secret.state == "needs_help":
                blockers.append(
                    _blocker("secret_needs_help", step_id=step.step_id)
                )
            elif (
                secret.state == "present"
                and secret.reference is not None
                and secret.generation is not None
            ):
                secret_bindings.append(
                    {
                        "generation": secret.generation,
                        "reference": secret.reference,
                        "slot_id": secret.slot_id,
                    }
                )
            else:
                blockers.append(_blocker("secret_missing", step_id=step.step_id))

    budget_payload: dict[str, object] | None = None
    budget_step = capabilities.step("budgets") if capabilities is not None else None
    if (
        budgets is None
        or budgets.phase != "ready"
        or budgets.budget is None
        or budgets.budget_sha256 is None
    ):
        blockers.append(_blocker("budget_profile_not_ready", step_id="budgets"))
    elif (
        budget_step is None
        or budget_step.state != "ready"
        or budget_step.evidence_sha256 != budgets.budget_sha256
    ):
        blockers.append(_blocker("budget_binding_mismatch", step_id="budgets"))
    else:
        budget_payload = {
            "budget": budgets.budget.as_dict(),
            "budget_sha256": budgets.budget_sha256,
            "revision": budgets.revision,
        }
    inputs = {
        "capability_head_sha256": capability_head,
        "capability_revision": capability_revision,
        "identity_pack_sha256": canonical_sha256(identity),
        "budgets": budget_payload,
        "preflight_sha256": canonical_sha256(preflight),
        "profile": profile_payload,
        "secret_bindings": secret_bindings,
    }
    actions = [
        {
            "action": "validate_release_and_inputs",
            "description": "Controleer de verzegelde release en alle gebonden invoer opnieuw.",
        },
        {
            "action": "prepare_isolated_runtime",
            "description": "Maak de afgescheiden Eva-diensten en privéopslag klaar.",
        },
        {
            "action": "start_and_verify",
            "description": "Start pas daarna en controleer gezondheid vóór omschakeling.",
        },
    ]
    body: dict[str, object] = {
        "actions": actions,
        "blockers": blockers,
        "capabilities": capability_payload,
        "contract": "EvaInstallerInstallationPlan/v2",
        "inputs": inputs,
        "non_claims": [
            "Dit plan heeft niets geïnstalleerd of gewijzigd.",
            "Dit plan heeft Docker, containers, databases, diensten en providers niet aangeroepen.",
            "Een echte installatie vereist een aparte, expliciete eigenaarhandeling.",
        ],
        "read_only": True,
        "status": "ready_for_release_preparation" if not blockers else "blocked",
    }
    return {**body, "plan_sha256": canonical_sha256(body)}
