"""Presentation groups for already-authorized deferred functions, not routing rules."""
from __future__ import annotations

# Keep groups small and stable across different authorized subsets. Unknown
# extension functions retain their original individual namespace and description.
_GROUPS = (
    ("eva_work_start", "Start background research, digital tasks, or tool-free reasoning.",
     ("start_digital_work", "start_background_research", "start_delegated_work")),
    ("eva_work_control", "Find, inspect, correct, stop, or resume existing work.",
     ("list_delegated_work", "get_delegated_work", "search_delegated_work", "stop_delegated_work",
      "resume_delegated_work", "get_related_work", "stop_related_work", "revise_digital_work")),
    ("eva_work_schedule", "Schedule follow-up work and manage scheduled tasks or chains.",
     ("schedule_digital_work", "schedule_work_resume", "list_scheduled_work", "cancel_scheduled_work", "cancel_work_chain")),
    ("eva_initiatives", "Read Eva's own contributions, give feedback, stop initiatives, or adopt an interest.",
     ("list_own_initiatives", "get_own_initiative_result", "give_initiative_feedback", "stop_own_initiative", "adopt_personal_interest")),
    ("eva_documents", "Read, find, inspect, save, or edit workspace files and document checkpoints.",
     ("save_workspace_document", "read_workspace_document", "list_workspace_directory", "find_workspace_files",
      "inspect_workspace_file", "checkpoint_document_task", "convert_text_batch_to_markdown", "edit_text_file_with_focused_validation")),
    ("eva_projects", "Start, inspect, list, or stop document projects and prepared projects.",
     ("start_document_project", "get_document_project", "stop_document_project", "list_document_projects",
      "start_prepared_document_project", "get_prepared_document_project", "stop_prepared_document_project")),
    ("eva_project_templates", "Create workspace document projects from available templates.",
     ("workspace_create_document_project_1", "workspace_create_document_project_2", "workspace_create_document_project_dynamic")),
    ("eva_connections", "Connect services and discover or use their available tools.",
     ("connect_service", "manage_connected_services", "discover_connector_tools", "use_connector_tool")),
    ("eva_procedures", "Find, designate, resolve, or run learned reusable procedures.",
     ("designate_reusable_procedure", "list_reusable_procedures", "resolve_reusable_procedure", "run_reusable_procedure")),

)
def _presentation():
    from eva.providers.bundled import bundled_presentations
    modules = bundled_presentations()
    groups = (*_GROUPS, *(group for module in modules for group in module.TOOL_GROUPS))
    by_function = {}
    for namespace, description, names in groups:
        for name in names:
            if name in by_function:
                raise ValueError("provider presentation tool is duplicated")
            by_function[name] = (namespace, description)
    direct = frozenset(name for module in modules for name in module.DIRECT_ACTIONS)
    return by_function, direct



def function_defer_loading(name):
    """Keep immediate voice reactions available without a discovery round."""
    _, direct = _presentation()
    return name not in (direct | {"speak_and_continue"})


def deferred_namespaces(tools):
    """Preserve every definition and native tool; return replay namespace mapping."""
    by_function, _ = _presentation()
    names = {tool["name"] for tool in tools if tool.get("type") == "function"}
    occupied = names | {tool.get("name") for tool in tools if tool.get("type") == "namespace"}
    result, groups, mapping = [], {}, {}
    for tool in tools:
        if tool.get("type") != "function":
            result.append(tool)
            continue
        name = tool["name"]
        if not function_defer_loading(name):
            # Direct functions do not need a discovery namespace. Keep their
            # native flat shape, including when replaying a retained tool call.
            result.append({**tool, "defer_loading": False})
            continue
        namespace, description = by_function.get(name, (name, name.replace("_", " ")))
        if namespace != name and namespace in occupied:
            namespace, description = name, name.replace("_", " ")
        mapping[name] = namespace
        if namespace not in groups:
            groups[namespace] = {"type": "namespace", "name": namespace,
                                 "description": description, "tools": []}
            result.append(groups[namespace])
        groups[namespace]["tools"].append({**tool, "defer_loading": True})
    return result, mapping
