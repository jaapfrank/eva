"""Project confirmed optional provider choices onto an existing target graph."""
from copy import deepcopy

from eva.contracts.canonical_json import canonical_sha256

# These are existing installer capability IDs and maintained Compose bindings.
# This projection removes optional consumers; it never supplies credentials.
_BINDINGS = {
    "telegram": {
        "services": {"telegram-ingress", "telegram-proactive"},
        "prefix": "EVA_TELEGRAM_",
        "mounts": {"/run/secrets/telegram_bot_token", "/run/secrets/telegram_principal_bindings",
                   "/run/secrets/telegram_pairing_secret"},
    },
    "home_assistant": {
        "services": set(),
        "prefix": "EVA_HOME_ASSISTANT_",
        "mounts": {"/run/secrets/home_assistant_token"},
    },
}


def project_optional_capabilities(graph, plan, expected_plan_sha256):
    """Return a copy and exact omitted bind keys; preserve the source graph.

    Only the trusted, freshly rebuilt installer plan is accepted. Email and
    adaptive-connector selection do not own services in this maintained base.
    Their separate connector preparation remains outside this projection.
    """
    if (plan.get("contract") != "EvaInstallerInstallationPlan/v2"
            or plan.get("status") != "ready_for_release_preparation"
            or plan.get("blockers") != []
            or plan.get("plan_sha256") != expected_plan_sha256
            or canonical_sha256({k: v for k, v in plan.items() if k != "plan_sha256"}) != expected_plan_sha256):
        raise ValueError("optional selection requires the confirmed current plan")
    states = {}
    for capability in plan.get("capabilities", []):
        key = capability["step_id"]
        if key in states:
            raise ValueError("duplicate installer capability")
        states[key] = capability["state"]
    if any(states.get(key) not in {"ready", "skipped"} for key in _BINDINGS):
        raise ValueError("optional provider choice is incomplete")
    result = deepcopy(graph)
    omitted = set()
    for capability, rule in _BINDINGS.items():
        if states[capability] != "skipped":
            continue
        for name, service in list(result["services"].items()):
            if name in rule["services"]:
                omitted.update((name, mount["target"]) for mount in service.get("volumes", [])
                               if mount.get("type") == "bind")
                del result["services"][name]
                continue
            environment = service.get("environment", {})
            service["environment"] = {key: value for key, value in environment.items()
                                      if not key.startswith(rule["prefix"])}
            kept = []
            for mount in service.get("volumes", []):
                if mount.get("target") in rule["mounts"]:
                    omitted.add((name, mount["target"]))
                else:
                    kept.append(mount)
            if "volumes" in service:
                service["volumes"] = kept
    # A retained consumer must not silently lose a dependency.
    retained = set(result["services"])
    for service in result["services"].values():
        if set(service.get("depends_on", {})) - retained:
            raise ValueError("retained service depends on an omitted optional provider")
    return result, omitted
