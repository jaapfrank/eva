"""Native research through the existing indexed tool journal and model adapter."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib

from eva.application.conversation import ConversationConflict, ConversationToolPreparation
from eva.application.initiative_execution import require_initiative_context
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.contracts.canonical_json import canonical_sha256


class NativeResearchTool:
    action_names = ("research_current_options",)
    tool_name = "research.public_sources"

    def __init__(self, research, validate_arguments):
        self.research = research
        self.validate_arguments = validate_arguments

    async def prepare(self, turn, call):
        if require_initiative_context(turn) is None or call.name not in self.action_names:
            raise ConversationConflict("native research requires its initiative context")
        arguments = self.validate_arguments(call.arguments)
        if "options" not in arguments:
            raise ConversationConflict("native research requires admitted public sources")
        return ConversationToolPreparation(
            tool_name=self.tool_name, arguments=arguments, expected_proof={},
            observation_tool_name=self.tool_name, observation_arguments=arguments,
            tool_configuration_sha256=self.research.research_configuration_sha256(),
            execution_timeout_seconds=60, verification_timeout_seconds=1,
            receipt_output={"verification_scope": "research_artifact_only",
                "external_state_verified": False,
                "completion_guidance": "Gebruik alleen de opgeslagen bronnen en conclusies. "
                    "Een onzekere onderzoeksaanvraag niet herhalen; benoem de beperking."},
            observation_output_key="result")

    def _check(self, request):
        if (request.tool_name != self.tool_name
                or request.observation_tool_name != self.tool_name
                or request.arguments != request.observation_arguments
                or request.expected_proof != {}
                or request.tool_configuration_sha256 != self.research.research_configuration_sha256()):
            raise ConversationConflict("native research configuration or request binding differs")

    @staticmethod
    def _request_hash(request):
        return canonical_sha256({"operation_id": request.operation_id,
            "configuration_sha256": request.tool_configuration_sha256,
            "arguments": request.arguments})

    async def execute(self, request):
        self._check(request)
        artifact = await asyncio.to_thread(self.research.research_current_options,
            question=request.arguments["question"], title=request.arguments["title"],
            options=tuple(request.arguments["options"]),
            researched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        output = {"title": artifact.title, "question": artifact.question,
            "content": artifact.content, "content_sha256": artifact.content_sha256,
            "provider": artifact.provider, "researched_at": artifact.researched_at,
            "sources": [{"title": s.title, "url": s.url} for s in artifact.sources],
            "model_usage": artifact.usage.to_wire(),
            "verification_scope": "research_artifact_only"}
        if (artifact.question != request.arguments["question"]
                or artifact.title != request.arguments["title"]
                or hashlib.sha256(artifact.content.encode()).hexdigest() != artifact.content_sha256):
            raise ConversationConflict("research artifact differs from its admitted request")
        digest = canonical_sha256(output)
        return ToolDispatchResult(True, digest, {"research_artifact": output,
            "request_sha256": self._request_hash(request), "artifact_sha256": digest})

    async def observe(self, request, dispatch):
        # No research/model/network call here, including after interrupted dispatch.
        self._check(request)
        if dispatch is None:
            return ToolProofObservation("conflict", {}, {"outcome": "unknown",
                "model_usage": {"measurement": "unknown"},
                "reason": "Research may have started; automatic replay is disabled."})
        data = dispatch.details
        output = data.get("research_artifact")
        if (not dispatch.accepted or not isinstance(output, dict)
                or data.get("request_sha256") != self._request_hash(request)
                or data.get("artifact_sha256") != canonical_sha256(output)
                or dispatch.provider_ref != canonical_sha256(output)):
            raise ConversationConflict("durable research receipt binding differs")
        return ToolProofObservation("verified", {}, {"observation": output,
            "artifact_sha256": dispatch.provider_ref,
            "verification_scope": "research_artifact_only", "external_state_verified": False})
