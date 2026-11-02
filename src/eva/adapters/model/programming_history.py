"""Project recovered local programming receipts through the existing scoped archive.

The executor keeps the original receipts for replay, budget and completion checks.
This adapter only changes what an already-authorized model request receives.
"""
from __future__ import annotations

import json
import re

from eva.application.conversation import ConversationConflict
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

_PROGRAMMING = frozenset({"list_programming_tools", "use_programming_tool",
                          "write_programming_file", "start_programming_process"})


def archive_recovered_programming(turn, prior_actions, completed_action, *, recovered_call_ids, archive):
    if archive is None or completed_action is None or not recovered_call_ids:
        return prior_actions, ()
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    receipts = (*prior_actions, completed_action)
    checkpoints = [(receipt, saved) for receipt in receipts
                   if (saved := PydanticConversationModel._saved_work_progress(receipt)) is not None]
    if not checkpoints:
        return prior_actions, ()
    checkpoint, (revision, step_ids) = checkpoints[-1]
    if any(old_revision >= revision or not old_ids.issubset(step_ids)
           for _, (old_revision, old_ids) in checkpoints[:-1]):
        return prior_actions, ()
    entries, summaries, selected = [], [], []
    for receipt in prior_actions:
        output = receipt.output
        result = output.get("programming_result")
        if (receipt.call.call_id not in recovered_call_ids or receipt.call.name not in _PROGRAMMING
                or output.get("status") != "verified" or output.get("installed") is not False
                or output.get("semantic_success_verified") is not False
                or not isinstance(result, dict) or result.get("isError", False) is not False):
            selected.append(receipt)
            continue
        call = receipt.call
        entry = {"call": {"call_id": call.call_id, "name": call.name, "arguments": call.arguments},
                 "output": output}
        index = len(entries)
        entries.append(entry)
        summary = {"call_id": call.call_id, "name": call.name,
                   "pointer": "/historical_programming/" + str(index),
                   "receipt_sha256": canonical_sha256(entry),
                   "installed": False, "semantic_success_verified": False}
        arguments = call.arguments
        if call.name == "use_programming_tool":
            summary["tool_name"] = arguments.get("tool_name")
            try:
                arguments = json.loads(arguments.get("arguments_json", ""))
            except (TypeError, ValueError):
                arguments = {}
        if isinstance(arguments, dict):
            for key in ("path", "mode", "pid"):
                if type(arguments.get(key)) in (str, int):
                    summary[key] = str(arguments[key])[:240]
            if isinstance(arguments.get("command"), str):
                summary["command_prefix"] = arguments["command"][:160]
                summary["command_prefix_is_complete"] = len(arguments["command"]) <= 160
        texts = [part["text"] for part in result.get("content", ())
                 if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if texts:
            text = "\n".join(texts)
            summary["output_tail"] = text[-512:]
            summary["output_tail_is_complete"] = len(text) <= 512
        summaries.append(summary)
    if not entries:
        return prior_actions, ()
    index = {"contract": "EvaDeferredLocalReadsModelIndex/v2",
             "saved_work_progress": {"call_id": checkpoint.call.call_id, "revision": revision},
             "historical_programming": entries}
    # Tiny receipts do not justify an archive write or a larger presentation.
    if len(canonical_json_bytes(entries)) <= len(canonical_json_bytes(summaries)) + 4096:
        return prior_actions, ()
    manifest = archive(turn, index)
    digest = canonical_sha256(index)
    reader = manifest.get("reader", {}) if isinstance(manifest, dict) else {}
    if (not isinstance(manifest, dict) or manifest.get("contract") != "EvaStoredConnectorResult/v1"
            or manifest.get("stored_complete") is not True
            or manifest.get("external_state_verified") is not False
            or manifest.get("sha256") != digest
            or manifest.get("byte_length") != len(canonical_json_bytes(index))
            or reader.get("action") != "use_connector_tool"
            or not re.fullmatch(r"eva-stored-result:[0-9a-f]{64}:" + digest, reader.get("tool_id", ""))):
        raise ConversationConflict("programming history archive binding differs")
    compact = {key: manifest[key] for key in ("contract", "stored_complete", "sha256",
                                             "byte_length", "external_state_verified", "reader")}
    note = ("Historische lokale programmeerontvangstbewijzen uit dezelfde uitvoering zijn exact "
            "bewaard via de bestaande bevoegde reader. Dit is geen nieuwe uitvoering, actuele "
            "bestandstoestand, installatiebewijs of inhoudelijk succes. De huidige voortgang, "
            "nieuwste tooluitvoer, fouten en niet-programmeerhandelingen blijven zichtbaar. "
            "Gebruik bewaard werk; herhaal geen handeling om oude uitvoer terug te krijgen. "
            "Lees alleen ontbrekende originele arguments/output via use_connector_tool met "
            "de reader en pointer hieronder (arguments_json={pointer,offset,limit,query:null}). "
            "Output tails en command prefixes zijn expliciet onvolledig waar aangegeven.\n"
            + json.dumps({"archive": compact, "historical_programming": summaries},
                         ensure_ascii=False, separators=(",", ":")))
    return tuple(selected), (note,)


class ProgrammingHistoryModel:
    """Model-input decorator; preserve the underlying task identity and tool catalog."""
    def __init__(self, model, archive, recovered_call_ids):
        self.model, self.archive, self.recovered_call_ids = model, archive, recovered_call_ids

    def __getattr__(self, name):
        return getattr(self.model, name)

    def generate(self, *args, self_context=(), **kwargs):
        prior, context = archive_recovered_programming(
            args[0] if args else kwargs["turn"], kwargs.get("prior_actions", ()),
            kwargs.get("completed_action"), recovered_call_ids=self.recovered_call_ids(),
            archive=self.archive)
        kwargs["prior_actions"] = prior
        return self.model.generate(*args, self_context=(*self_context, *context), **kwargs)
