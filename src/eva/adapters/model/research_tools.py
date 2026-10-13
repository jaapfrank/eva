"""Native research through the existing indexed tool journal and model adapter."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import ipaddress
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from eva.application.conversation import ConversationConflict, ConversationToolPreparation
from eva.application.cancellation import run_cancellable, run_in_cancellation_thread
from eva.application.initiative_execution import require_initiative_context
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.contracts.canonical_json import canonical_sha256


def validate_public_projection(arguments):
    """Structural disclosure checks plus the admitted model's suitability judgment.

    This reduces disclosure risk; it is not a proof that arbitrary prose is anonymous.
    Model review never widens the owner's cost, destination or operation authority.
    """
    if arguments.get("public_review") != "public_unlinked":
        raise ConversationConflict("public research input is uncertain or not reviewed")
    values = [arguments["question"], arguments["title"], *arguments["options"]]
    text = "\n".join(values)
    if len(text.encode()) > 4000 or any(unicodedata.category(c).startswith("C") for c in text if c != "\n"):
        raise ConversationConflict("public research projection is not bounded plain text")
    text = unicodedata.normalize("NFKC", unquote(text))
    if any(unicodedata.category(c).startswith("C") for c in text if c != "\n"):
        raise ConversationConflict("public research decoded input contains control characters")
    for token in re.findall(r"[0-9A-Fa-f:]+(?:%[A-Za-z0-9_.-]+)?", text):
        if token.count(":") < 2:
            continue
        try:
            ipaddress.IPv6Address(token)
        except ValueError:
            continue
        raise ConversationConflict("public research direct IP inputs are not admitted")
    blocked = (
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?i)\b(?:bearer|password|passwd|api[_ -]?key|access[_ -]?token|secret)\s*[:= ]",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\beyJ[A-Za-z0-9_-]{12,}\.",
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AIza[A-Za-z0-9_-]{20,})",
        r"\b[A-Fa-f0-9]{32,}\b|\bsk-[A-Za-z0-9_-]{12,}",
        r"(?:\+?\d[ ()-]*){8,}|\b[1-9]\d{3}\s?[A-Z]{2}\b",
        r"(?i)(?:\b[A-Z]:[\\/]|\\\\|/(?:home|users|run|var|etc|mnt|private|tmp|proof-state)/)",
        r"(?i)\b(?:mijn|m'n|onze|ons|my|our)\b",
        r"(?i)\b[A-Za-zÀ-ÿ]+(?:straat|laan|weg|dreef|plein)\s+\d+",
        r"(?i)\b(?:localhost|[\w.-]+\.(?:local|lan|internal|home))\b",
        r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)",
        r"(?i)\b(?:adres|telefoonnummer|contactgegevens|woonadres)\s+van\b",
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2})\b",
    )
    if any(re.search(pattern, text) for pattern in blocked):
        raise ConversationConflict("public research input contains possible private details")
    for match in re.finditer(r"(?i)\b(?:https?|ftp|file)://[^\s<>()]+", text):
        raw = match.group().rstrip(".,;")
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as error:
            raise ConversationConflict("public research URL is invalid") from error
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment or port not in (None, 443)
                or "." not in host or host.endswith((".local", ".lan", ".internal", ".home", ".arpa"))
                or host == "localhost"):
            raise ConversationConflict("public research URL has a private or uncontrolled destination")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host):
                raise ConversationConflict("public research numeric destination is not admitted")
        else:
            raise ConversationConflict("public research direct IP destinations are not admitted")
    return {"model_assessment": "public_unlinked", "host_checks": "passed",
        "policy": "abstract-projection-host-checks-v1", "guarantee": "risk_reduction_not_anonymity_proof"}


class NativeResearchTool:
    action_names = ("research_current_options",)
    tool_name = "research.public_sources"

    def __init__(self, research, validate_arguments):
        self.research = research
        self.validate_arguments = validate_arguments

    @staticmethod
    def _mode(arguments):
        return {"research_mode": arguments["research_mode"]} if "research_mode" in arguments else {}

    async def prepare(self, turn, call):
        if require_initiative_context(turn) is None or call.name not in self.action_names:
            raise ConversationConflict("native research requires its initiative context")
        arguments = self.validate_arguments(call.arguments)
        if "options" not in arguments:
            raise ConversationConflict("native research requires admitted public sources")
        review = (validate_public_projection(arguments) if "public_review" in arguments
                  else {"policy": "host_constant_public_sources"})
        return self._preparation(arguments, review)

    def _configuration_sha256(self):
        return self.research.research_configuration_sha256()

    def _validate_execution_input(self, arguments):
        if "public_review" in arguments:
            validate_public_projection(arguments)

    def _preparation(self, arguments, review):
        envelope = self.research.research_request_payload(
            question=arguments["question"], options=tuple(arguments["options"]), **self._mode(arguments))
        envelope_hash = canonical_sha256(envelope)
        return ConversationToolPreparation(
            tool_name=self.tool_name, arguments=arguments,
            expected_proof={"provider_envelope_sha256": envelope_hash},
            observation_tool_name=self.tool_name, observation_arguments=arguments,
            tool_configuration_sha256=self._configuration_sha256(),
            execution_timeout_seconds=int(getattr(self.research, "native_research_timeout_seconds", lambda: 55.0)()) + 5,
            verification_timeout_seconds=1,
            receipt_output={"verification_scope": "research_artifact_only",
                "provider_request_body": envelope, "provider_request_body_sha256": envelope_hash,
                "public_input_checks": review,
                "search_control_scope": "submitted_provider_body_only; internal searches are provider-managed",
                "external_state_verified": False,
                "completion_guidance": "Gebruik alleen de opgeslagen bronnen en conclusies. "
                    "Een onzekere onderzoeksaanvraag niet herhalen; benoem de beperking."},
            observation_output_key="result")

    def _check(self, request):
        if (request.tool_name != self.tool_name
                or request.observation_tool_name != self.tool_name
                or request.arguments != request.observation_arguments
                or request.expected_proof != {"provider_envelope_sha256": canonical_sha256(
                    self.research.research_request_payload(question=request.arguments["question"],
                        options=tuple(request.arguments["options"]), **self._mode(request.arguments)))}
                or request.tool_configuration_sha256 != self._configuration_sha256()):
            raise ConversationConflict("native research configuration or request binding differs")

    @staticmethod
    def _request_hash(request):
        return canonical_sha256({"operation_id": request.operation_id,
            "configuration_sha256": request.tool_configuration_sha256,
            "arguments": request.arguments})

    async def execute(self, request):
        self._check(request)
        self._validate_execution_input(request.arguments)
        artifact = await run_cancellable(lambda: run_in_cancellation_thread(
            lambda: self.research.research_current_options(
                question=request.arguments["question"], title=request.arguments["title"],
                options=tuple(request.arguments["options"]),
                researched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **self._mode(request.arguments))))
        output = {"title": artifact.title, "question": artifact.question,
            "content": artifact.content, "content_sha256": artifact.content_sha256,
            "provider": artifact.provider, "researched_at": artifact.researched_at,
            "sources": [{"title": s.title, "url": s.url} for s in artifact.sources],
            "model_usage": artifact.usage.to_wire(),
            "verification_scope": "research_artifact_only",
            "source_coverage": "cited_topic_sources" if request.arguments.get("research_mode") == "topic" else "named_options",
            "semantic_claims_independently_verified": False}
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
        return ToolProofObservation("verified", dict(request.expected_proof), {"observation": output,
            "artifact_sha256": dispatch.provider_ref,
            "verification_scope": "research_artifact_only", "external_state_verified": False})


class OwnerResearchContinuationTool(NativeResearchTool):
    """Ordinary requested research in an existing indexed conversation step.

    Initiative disclosure checks stay on NativeResearchTool. The owner route
    retains the ordinary research argument policy and does not invent a public
    projection or grant initiative authority.
    """

    async def prepare(self, turn, call):
        if require_initiative_context(turn) is not None or call.name not in self.action_names:
            raise ConversationConflict("owner research continuation requires an ordinary turn")
        arguments = self.validate_arguments(call.arguments)
        self._validate_execution_input(arguments)
        return self._preparation(arguments, {"policy": "ordinary_requested_research"})

    def _configuration_sha256(self):
        return canonical_sha256({"research": super()._configuration_sha256(),
            "input_policy": "ordinary_requested_research", "contract": "owner-research-continuation-v1"})

    def _validate_execution_input(self, arguments):
        if arguments.get("public_review", "not_applicable") != "not_applicable":
            raise ConversationConflict("owner research continuation cannot claim initiative review")
