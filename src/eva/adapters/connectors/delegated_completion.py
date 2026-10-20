"""Adapter-owned completion evidence for the existing participating tool routes.

Stored receipts and wire formats stay unchanged. The generic worker consumes this
result; it does not interpret provider resources, protocol payloads or readbacks.
"""
import json
from eva.adapters.connectors.home_assistant_completion import (
    _automation_config_target, _full_automation_readback, _automation_batch_readbacks,
)
from eva.adapters.postgres.component_improvement_actions import DRAFT, EVALUATE, PREPARE, COMPARE
from eva.adapters.postgres.programming_actions import (
    programming_tool_name, LIST as PROGRAMMING_LIST, CALL as PROGRAMMING_CALL,
    WRITE as PROGRAMMING_WRITE, START as PROGRAMMING_START,
)


def undispatched_native_status_refusal(receipt):
    """A host-proved refused lookup is context, never a successful operation."""
    proof = receipt.output.get("proof")
    evidence = proof.get("evidence") if isinstance(proof, dict) else None
    return (receipt.call.name == "manage_connected_services"
            and receipt.call.arguments == {"action": "status"}
            and receipt.output.get("status") == "failed"
            and receipt.output.get("recoverable_pre_dispatch_refusal") is True
            and isinstance(evidence, dict)
            and evidence.get("source") == "native_connector_status_pre_dispatch_refusal"
            and evidence.get("operation_kind") == "read"
            and evidence.get("provider_execution") == "not_started"
            and isinstance(evidence.get("resume_binding"), dict)
            and bool(evidence["resume_binding"]))


def recoverable_read_error(receipt):
    proof = receipt.output.get("proof")
    evidence = proof.get("evidence") if isinstance(proof, dict) else None
    if isinstance(evidence, dict) and evidence.get("source") == "native_research_explicit_owner_resume":
        return (receipt.call.name == "research_current_options"
                and receipt.output.get("status") == "failed"
                and receipt.output.get("recoverable_read_error") is True
                and evidence.get("operation_kind") == "read"
                and isinstance(evidence.get("resume_binding"), dict))
    return ((receipt.output.get("status") == "failed"
             or (receipt.output.get("status") == "timed_out"
                 and isinstance(evidence, dict)
                 and evidence.get("source") == "home_assistant_api_read_error"))
            and receipt.output.get("recoverable_read_error") is True
            and isinstance(evidence, dict)
            and evidence.get("source") in {"mcp_gateway_read_error", "home_assistant_inventory_read_error", "home_assistant_api_read_error"}
            and evidence.get("operation_kind") == "read")



def recoverable_completion_context(receipt):
    if undispatched_native_status_refusal(receipt):
        return True
    proof = receipt.output.get("proof")
    evidence = proof.get("evidence") if isinstance(proof, dict) else None
    if (receipt.call.name == "research_current_options" and receipt.output.get("status") == "failed"
            and receipt.output.get("recoverable_pre_dispatch_refusal") is True
            and isinstance(evidence, dict)
            and evidence.get("source") == "native_research_certified_pre_dispatch_refusal"
            and evidence.get("certificate_sha256") == "f9707883bf69747df06f1b42fd24fdf4d07e16b87f103108f7e054a75f3256b1"
            and evidence.get("provider_execution") == "not_started"):
        return True
    if recoverable_read_error(receipt):
        return True
    from eva.adapters.postgres.connector_results import READER_PREFIX
    return (receipt.call.name == "use_connector_tool"
            and isinstance(receipt.call.arguments.get("tool_id"), str)
            and receipt.call.arguments["tool_id"].startswith(READER_PREFIX)
            and receipt.output.get("source") == "stored_connector_result"
            and receipt.output.get("status") == "selection_invalid"
            and receipt.output.get("external_state_verified") is False)


def completed_tool_evidence(receipts):
    """Discovery is useful context, but cannot alone prove completed work."""
    component_positions = [index for index, receipt in enumerate(receipts)
                           if receipt.call.name in {DRAFT, EVALUATE, PREPARE, COMPARE}]
    if component_positions:
        index = component_positions[-1]
        component = receipts[index]
        expected = component.output.get("operation_result")
        if component.call.name == COMPARE and (
                component.output.get("status") != "verified"
                or not isinstance(expected, dict)
                or expected.get("contract") != "EvaComponentDevelopmentSelection/v1"
                or expected.get("state") not in {"candidate_selected", "baseline_retained"}):
            return False
        saved = False
        for receipt in receipts[index + 1:]:
            if (receipt.call.name != "save_workspace_document"
                    or receipt.output.get("status") != "verified"):
                continue
            arguments = {key: value for key, value in receipt.call.arguments.items()
                         if key not in {"content", "component_step"} or value is not None}
            if "component_step" in arguments and "content" not in arguments:
                step = arguments["component_step"]
                saved = (type(step) is int and step >= 0
                         and step == component.output.get("component_step"))
            elif "content" in arguments and "component_step" not in arguments:
                try:
                    saved = isinstance(expected, dict) and json.loads(arguments["content"]) == expected
                except (TypeError, ValueError):
                    pass
            if saved:
                break
        if not saved:
            return False
    completed = False
    last_home_assistant_success = None
    last_procedure_success = None
    pending_automation_readbacks = set()
    for receipt in receipts:
        if undispatched_native_status_refusal(receipt):
            # Preserve earlier verified work; this refused lookup proves nothing new.
            continue
        if recoverable_completion_context(receipt):
            # A recoverable read failure is context, not completed work. A later
            # verified result in the same admitted execution may resolve it.
            completed = False
            continue
        output = receipt.output
        if receipt.call.name in {"save_work_progress", "get_delegated_work", PROGRAMMING_LIST}:
            # Planning and reading a predecessor are context, not new completed work.
            continue
        if receipt.call.name == "read_home_assistant_inventory":
            if output.get("status") != "verified":
                return False
            pending_automation_readbacks.difference_update(_automation_batch_readbacks(receipt))
            completed = True
        elif receipt.call.name == "home_assistant_request":
            if output.get("status") != "verified":
                return False
            result = output.get("result")
            if not isinstance(result, dict) or type(result.get("success")) is not bool:
                return False
            last_home_assistant_success = result["success"]
            completed = completed or last_home_assistant_success
            target = _automation_config_target(receipt.call.arguments)
            if target is not None and last_home_assistant_success:
                if receipt.call.arguments.get("method") in {"POST", "PUT", "PATCH"}:
                    pending_automation_readbacks.add(target)
                elif _full_automation_readback(receipt.call.arguments, result):
                    pending_automation_readbacks.discard(target)
        elif receipt.call.name in {"list_reusable_procedures", "resolve_reusable_procedure", "designate_reusable_procedure", "run_reusable_procedure"}:
            if output.get("status") != "verified":
                return False
            output = output.get("operation_result", {})
            if output.get("contract") != "ProcedureReferenceAction/v1":
                return False
            status = output.get("status")
            if receipt.call.name == "list_reusable_procedures":
                if status != "candidates":
                    return False
            elif receipt.call.name == "designate_reusable_procedure":
                if status != "designated" or not output.get("reference"):
                    return False
                completed = True
            elif receipt.call.name == "run_reusable_procedure":
                if status not in {"execution_completed", "execution_failed"}:
                    return False
                last_procedure_success = status == "execution_completed"
                completed = completed or last_procedure_success
            elif status == "resolved":
                if not output.get("references"):
                    return False
                completed = True
            elif status != "unavailable":
                return False
        elif receipt.call.name in {PROGRAMMING_CALL, PROGRAMMING_WRITE, PROGRAMMING_START}:
            result = output.get("programming_result", {})
            if output.get("status") != "verified" or result.get("isError") is not False:
                return False
            if programming_tool_name(receipt.call) not in {
                    "start_process", "interact_with_process", "list_sessions", "list_processes"}:
                completed = True
        elif output.get("status") == "verified":
            completed = True
        else:
            return False
    return (completed and last_home_assistant_success is not False
            and last_procedure_success is not False and not pending_automation_readbacks)

