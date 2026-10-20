"""Home Assistant-owned presentation and ordinary-read conversation policy.

Pure metadata does not configure a provider, grant a capability or contact HA.
"""
READ_ACTIONS = frozenset({"read_home_assistant_inventory", "read_home_assistant_state",
                          "read_home_assistant_history", "home_assistant_request"})
DIRECT_ACTIONS = frozenset({"read_home_assistant_inventory", "home_assistant_request",
                            "set_home_assistant_light", "set_home_assistant_switch"})
TOOL_GROUPS = (
    ("eva_home", "Use Home Assistant devices, services, states, history, and automations.",
     ("home_assistant_request", "read_home_assistant_inventory", "read_home_assistant_state", "read_home_assistant_history",
      "set_home_assistant_light", "set_home_assistant_switch", "create_home_assistant_state_automation", "create_home_assistant_advanced_state_automation")),
    ("eva_localtuya", "Plan or perform approved LocalTuya integration changes.",
     ("plan_tuya_to_localtuya_migration", "register_approved_localtuya_repository", "install_approved_localtuya_package")),
)
LOCAL_SOURCE_GUIDANCE = 'For questions about this household or its local environment, use relevant authorized Home Assistant data before public web lookup. Discover only the needed entity domain, then read the requested data through the offered tools. Check freshness, location and whether the result covers the requested time; a current state does not establish a future prediction. A verified tool receipt with success=false is still a failed API request. Use public lookup when local sources cannot answer. Simple reads stay in this conversation; do not start a background task merely to read a local source.'

API_SOURCE_GUIDANCE = 'For current states of multiple discovered entities, prefer one bounded POST /api/template with body_json containing a template, instead of a separate model/tool round trip per entity. Render explicit JSON records with entity_id, state and all attributes needed for the requested action or restoration; do not serialize HA State objects directly. Template collections returned by state_attr may be sets: convert collections to lists before embedding them in JSON, including group member IDs, and materialize map/select results with |list. Use ordinary JSON scalars, lists and dictionaries only. Include every requested entity, retain unknown/unavailable states, and check completeness, pagination and truncation. A grouped read can verify each target. Make a fresh read after every mutation phase; an earlier snapshot or service acknowledgement is not post-action proof.'


DIRECT_EFFECT_GUIDANCE = ('For ordinary light/switch on or off, choose the offered '
    'set_home_assistant_light or set_home_assistant_switch directly with the requested name or exact entity ID. '
    'Those tools already resolve current targets, check ambiguity and permission, execute via the module, '
    'and independently read the resulting state. Their internal discovery fulfills source discovery: '
    'do not precede them with a separate inventory, state or service lookup merely to resolve that target. '
    'A verified state proof needs no second status lookup unless new evidence contradicts it. '
    'An ambiguous name remains a clarification; do not choose another matching lamp. '
    'For a plain control command fully satisfied by this one action, set finish_without_reply=true: '
    'the household does not want a success reply. Use false if any additional action or question '
    'remains, or the user explicitly asks for confirmation. Failures and ambiguity still require a useful reply. '
    'Set reusable_command=true only when the entire current utterance independently names this target and state, '
    'unconditionally and without needing earlier conversation, remembered preferences or an inferred location. '
    'References such as "that one", confirmations, conditions, corrections and combined requests must use false. '
    'Use the general API for operations beyond these offered effects, within its existing authority.')


def conversation_instructions(turn, available_names):
    instructions = (LOCAL_SOURCE_GUIDANCE,) if READ_ACTIONS.intersection(available_names) else ()
    if {"set_home_assistant_light", "set_home_assistant_switch"}.intersection(available_names):
        instructions += (DIRECT_EFFECT_GUIDANCE,)
    if "home_assistant_request" in available_names:
        instructions += (API_SOURCE_GUIDANCE,)
    return instructions


def contact_reply_allowed(turn, receipt):
    # Dispatch/observation authority remains with the registered tool port.
    return receipt.call.name in READ_ACTIONS
