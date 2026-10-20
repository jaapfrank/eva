"""Closed lazy lookup for optional providers' pure retained-receipt policies.

Execution enablement is deliberately irrelevant here: reading existing immutable
history does not authorize an external tool. Missing optional policy code leaves
literal receipts usable. Other failures are surfaced rather than hidden.
"""
from importlib import import_module


_POLICY_MODULES = {
    "home_assistant_request": "eva.providers.home_assistant_receipts",
    "read_home_assistant_inventory": "eva.providers.home_assistant_receipts",
}


def receipt_policy(action_name):
    module_name = _POLICY_MODULES.get(action_name)
    if module_name is None:
        return None
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return None


def retains_observation(action_name):
    policy = receipt_policy(action_name)
    return policy is not None and action_name in policy.OBSERVATION_ACTION_NAMES


def retains_action_body(action_name):
    return action_name in {"save_workspace_document", "use_connector_tool"} or retains_observation(action_name)


def retained_source_action_names():
    """Select existing historical readers without constructing provider grants."""
    return tuple(name for name in _POLICY_MODULES if retains_observation(name))


def retained_source_observation(action_name, arguments):
    policy = receipt_policy(action_name)
    if policy is None:
        return None, "provider_receipt_policy_unavailable"
    return policy.retained_source_observation(action_name, arguments)


def retained_source_attention_selectors(action_name, arguments, observation):
    """Provider-owned exact read targets; neither subscriptions nor authority."""
    policy = receipt_policy(action_name)
    select = None if policy is None else getattr(policy, "retained_source_attention_selectors", None)
    return () if select is None else select(action_name, arguments, observation)
