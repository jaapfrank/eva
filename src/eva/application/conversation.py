"""Channel-neutral conversation boundary for Eva's Living Core."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
import logging
import re
from time import monotonic
from typing import Callable, Iterator, Literal, Protocol

from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.application.conversation_media import ConversationMediaContext
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.identity import IdentityPack

MEMORY_SEARCH_ACTION = "search_personal_memory"

_INITIAL_MEMORY_TIMING_LOG = logging.getLogger(__name__ + ".initial_memory_timing")
_INITIAL_MEMORY_TIMING_LOG.setLevel(logging.INFO)
_INITIAL_MEMORY_TIMING_LOG.propagate = False
if not _INITIAL_MEMORY_TIMING_LOG.handlers:
    _INITIAL_MEMORY_TIMING_LOG.addHandler(logging.StreamHandler())


def _bounded_reply_text(text: str, maximum_words: int) -> str:
    """Keep the response contract usable without extending the model budget.

    A partial answer is explicit and ends at a paragraph or sentence boundary.
    Never splice a word, link or fenced code block. Authoritative action reply
    guards still run afterwards and retain their verified result references.
    """
    if len(text.split()) <= maximum_words:
        return text
    notice = "Dit antwoord is ingekort; de toelichting is onvolledig."
    available = maximum_words - len(notice.split())
    prefix = ""
    # Prefer complete paragraphs, retaining their original formatting and links.
    boundaries = [match.start() for match in re.finditer(r"\n\s*\n", text)]
    if not boundaries:
        boundaries = [match.start() for match in re.finditer(r"(?<=[.!?])\s+", text)]
    for end in boundaries:
        candidate = text[:end].rstrip()
        if len(candidate.split()) > available:
            break
        if candidate.count("```") % 2 or candidate.count("~~~") % 2:
            continue
        if candidate.count("[") != candidate.count("]") or candidate.count("(") != candidate.count(")"):
            continue
        prefix = candidate
    return notice if not prefix else notice + "\n\n" + prefix


class ConversationConflict(RuntimeError):
    """A replay reused a durable message identity for different content."""


class ConversationExecutionLimitReached(RuntimeError):
    """This turn exhausted its durable provider-request admission limit."""


class ConversationStopped(RuntimeError):
    """The principal durably requested no further steps for this turn."""


class ConversationClarificationRequired(RuntimeError):
    """A safe action needs one ordinary follow-up answer from the user."""

    def __init__(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt or len(prompt) > 240:
            raise ValueError("conversation clarification prompt is invalid")
        self.prompt = prompt
        super().__init__(prompt)


class ConversationActionInputRejected(ConversationClarificationRequired):
    """A tool input failed validation before admission or side effects."""

    def __init__(self, fields: tuple[str, ...], *, prompt: str =
                 "Het onderzoek is nog niet gestart: de onderzoeksopdracht kon niet correct worden verwerkt.") -> None:
        self.fields = fields
        super().__init__(prompt)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_TEXT_DELTA_SINK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "eva_conversation_text_delta_sink", default=None
)

_TEXT_DELTA_PARTS: ContextVar[list[str] | None] = ContextVar(
    "eva_conversation_text_delta_parts", default=None
)


@contextmanager
def stream_conversation_text(sink: Callable[[str], None]) -> Iterator[None]:
    token = _TEXT_DELTA_SINK.set(sink)
    parts_token = _TEXT_DELTA_PARTS.set([])
    try:
        yield
    finally:
        _TEXT_DELTA_PARTS.reset(parts_token)
        _TEXT_DELTA_SINK.reset(token)


def publish_conversation_text(
    delta: str, *, tools_disabled: bool, final_answer: bool = False,
) -> None:
    """Publish a provisional answer, never an execution/completion receipt.

    Adapters may admit provider-marked final text even with tools available.
    Unmarked text keeps the existing tool-free requirement.
    """
    sink = _TEXT_DELTA_SINK.get()
    if sink is not None and (tools_disabled or final_answer) and delta:
        parts = _TEXT_DELTA_PARTS.get()
        if parts is not None:
            parts.append(delta)
        sink(delta)


def publish_committed_conversation_text(text: str) -> None:
    """Offer a saved reply suffix without repeating provisional model text.

    The caller must validate the committed reply and current audience first.
    Diverging provisional text remains for the transport's final reconciliation.
    """
    parts = _TEXT_DELTA_PARTS.get()
    if parts is None:
        return
    prefix = "".join(parts)
    if text.startswith(prefix) and len(text) > len(prefix):
        publish_conversation_text(text[len(prefix):], tools_disabled=False, final_answer=True)


@dataclass(frozen=True, slots=True)
class ConversationModelUsage:
    """Provider-neutral usage, including honest partially measured retries."""

    measurement: Literal["measured", "partial", "unmeasured"]
    model_attempts: int | None
    tool_calls: int | None
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        values = (
            self.model_attempts,
            self.tool_calls,
            self.input_tokens,
            self.output_tokens,
        )
        if self.measurement == "unmeasured":
            if any(value is not None for value in values):
                raise ValueError("unmeasured model usage cannot carry values")
            return
        if self.measurement not in {"measured", "partial"} or any(
            value is None for value in values
        ):
            raise ValueError("measured model usage is incomplete")
        if any(
            not isinstance(value, int) or isinstance(value, bool) for value in values
        ):
            raise ValueError("measured model usage is invalid")
        attempts, tools, inputs, outputs = (int(value) for value in values)
        if attempts < 1 or min(tools, inputs, outputs) < 0:
            raise ValueError("measured model usage is invalid")

    @classmethod
    def measured(
        cls,
        *,
        model_attempts: int = 1,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> "ConversationModelUsage":
        return cls("measured", model_attempts, tool_calls, input_tokens, output_tokens)

    @classmethod
    def unmeasured(cls) -> "ConversationModelUsage":
        return cls("unmeasured", None, None, None, None)

    @classmethod
    def partial(
        cls,
        *,
        model_attempts: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> "ConversationModelUsage":
        """Keep known totals while admitting that some attempts lack usage."""

        return cls("partial", model_attempts, tool_calls, input_tokens, output_tokens)

    @classmethod
    def aggregate(
        cls, usages: tuple["ConversationModelUsage", ...]
    ) -> "ConversationModelUsage":
        if not usages or any(usage.measurement == "unmeasured" for usage in usages):
            return cls.unmeasured()
        model_attempts = sum(usage.model_attempts or 0 for usage in usages)
        tool_calls = sum(usage.tool_calls or 0 for usage in usages)
        input_tokens = sum(usage.input_tokens or 0 for usage in usages)
        output_tokens = sum(usage.output_tokens or 0 for usage in usages)
        if any(usage.measurement == "partial" for usage in usages):
            return cls.partial(
                model_attempts=model_attempts,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return cls.measured(
            model_attempts=model_attempts,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def to_wire(self) -> dict[str, str | int | None]:
        return {
            "input_tokens": self.input_tokens,
            "measurement": self.measurement,
            "model_attempts": self.model_attempts,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
        }

    @classmethod
    def from_wire(cls, value: object) -> "ConversationModelUsage":
        fields = {
            "input_tokens",
            "measurement",
            "model_attempts",
            "output_tokens",
            "tool_calls",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("model usage payload is invalid")
        measurement = value["measurement"]
        numeric = (
            value["model_attempts"],
            value["tool_calls"],
            value["input_tokens"],
            value["output_tokens"],
        )
        if measurement not in {"measured", "partial", "unmeasured"} or any(
            item is not None and (not isinstance(item, int) or isinstance(item, bool))
            for item in numeric
        ):
            raise ValueError("model usage payload is invalid")
        return cls(measurement, *numeric)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ConversationModelTiming:
    """Measured provider time to first text and completed response, in ms."""

    first_content_ms: int | None
    completed_ms: int | None

    def __post_init__(self) -> None:
        values = (self.first_content_ms, self.completed_ms)
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in values
        ):
            raise ValueError("model timing is invalid")
        if self.first_content_ms is not None and (
            self.completed_ms is None or self.first_content_ms > self.completed_ms
        ):
            raise ValueError("first content timing is invalid")

    @classmethod
    def unmeasured(cls) -> "ConversationModelTiming":
        return cls(None, None)

    @classmethod
    def aggregate(
        cls, timings: tuple["ConversationModelTiming", ...]
    ) -> "ConversationModelTiming":
        if not timings or any(item.completed_ms is None for item in timings):
            return cls.unmeasured()
        completed = sum(item.completed_ms or 0 for item in timings)
        final_first = timings[-1].first_content_ms
        first = (
            None
            if final_first is None
            else completed - (timings[-1].completed_ms or 0) + final_first
        )
        return cls(first, completed)

    def to_wire(self) -> dict[str, int | None]:
        return {
            "completed_ms": self.completed_ms,
            "first_content_ms": self.first_content_ms,
        }

    @classmethod
    def from_wire(cls, value: object) -> "ConversationModelTiming":
        if not isinstance(value, dict) or set(value) != {
            "completed_ms",
            "first_content_ms",
        }:
            raise ValueError("model timing payload is invalid")
        return cls(value["first_content_ms"], value["completed_ms"])  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ConversationObservationBinding:
    """Connector-attested media and subject region for one grounded correction."""

    observation_id: str
    media_sha256: str
    subject_region_sha256: str
    media_type: str
    source_ref: str

    def __post_init__(self) -> None:
        if (
            not self.observation_id.strip()
            or len(self.observation_id) > 240
            or any(ord(character) < 32 for character in self.observation_id)
        ):
            raise ValueError("observation binding ID is invalid")
        if not _SHA256.fullmatch(self.media_sha256) or not _SHA256.fullmatch(
            self.subject_region_sha256
        ):
            raise ValueError("observation binding hash is invalid")
        if self.media_type not in {
            "image/heic",
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise ValueError("observation binding media type is invalid")
        if (
            not self.source_ref.strip()
            or len(self.source_ref) > 500
            or any(ord(character) < 32 for character in self.source_ref)
        ):
            raise ValueError("observation binding source is invalid")

    @property
    def binding_sha256(self) -> str:
        encoded = json.dumps(
            {
                "media_sha256": self.media_sha256,
                "media_type": self.media_type,
                "observation_id": self.observation_id,
                "source_ref": self.source_ref,
                "subject_region_sha256": self.subject_region_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def model_context(self) -> str:
        return (
            "Door de connector gebonden fotowaarneming: "
            f"observation_id={self.observation_id}; media_sha256={self.media_sha256}; "
            f"subject_region_sha256={self.subject_region_sha256}; "
            f"binding_sha256={self.binding_sha256}."
        )


@dataclass(frozen=True, slots=True)
class IdentityProfile:
    name: str
    description: str
    principles: tuple[str, ...]
    maximum_reply_words: int = 160
    identity_pack: IdentityPack | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.description.strip():
            raise ValueError("identity name and description are required")
        if not self.principles or any(not item.strip() for item in self.principles):
            raise ValueError("identity requires non-empty principles")
        if not 20 <= self.maximum_reply_words <= 400:
            raise ValueError("identity reply limit must be between 20 and 400 words")
        if self.identity_pack is not None and (
            self.identity_pack.state != "current"
            or self.identity_pack.display_name != self.name
        ):
            raise ValueError("current IdentityPack does not match the active identity")


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    tenant_id: str
    installation_id: str
    principal_id: str
    conversation_id: str
    message_id: str
    text: str
    occurred_at: str
    audience: MemoryAudience
    project_id: str | None = None
    observation_binding: ConversationObservationBinding | None = None
    media_context: ConversationMediaContext | None = None
    media_context_sha256: str | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.tenant_id,
                self.installation_id,
                self.principal_id,
                self.conversation_id,
                self.message_id,
            )
        ):
            raise ValueError("conversation turn identifiers are required")
        if not self.text.strip() or len(self.text.encode("utf-8")) > 65_536:
            raise ValueError("conversation turn text is empty or too large")
        if self.audience.principal_id != self.principal_id:
            raise ValueError("conversation turn audience principal differs")
        if self.media_context is not None and (
            self.audience.kind != "private_person"
            or self.media_context.source_ref != self.message_id
        ):
            raise ValueError("media context is not bound to this private turn")
        if self.media_context_sha256 is not None and (
            self.audience.kind != "private_person"
            or not isinstance(self.media_context_sha256, str)
            or _SHA256.fullmatch(self.media_context_sha256) is None
            or (self.media_context is not None
                and self.media_context.context_sha256 != self.media_context_sha256)
        ):
            raise ValueError("media context digest is invalid")
        if self.audience.kind == "household":
            prefix = f"household:{self.audience.household_id}:conversation:"
            if (not self.conversation_id.startswith(prefix)
                    or not self.conversation_id.removeprefix(prefix)
                    or not self.message_id.startswith(self.conversation_id + ":")
                    or self.project_id is not None or self.observation_binding is not None):
                raise ValueError("household turn is not bound to its audience")
        if self.audience.kind == "telegram_group":
            assert self.audience.group_chat_id is not None
            prefix = f"telegram:{self.audience.group_chat_id}:"
            if (
                self.conversation_id != f"telegram:{self.audience.group_chat_id}"
                or not self.message_id.startswith(prefix)
                or self.project_id is not None
                or self.observation_binding is not None
            ):
                raise ValueError("Telegram group turn is not bound to its audience")
        if self.project_id is not None and (
            not self.project_id.strip() or len(self.project_id) > 200
        ):
            raise ValueError("conversation turn project ID is invalid")
        try:
            datetime.strptime(self.occurred_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                "conversation turn timestamp must be canonical UTC"
            ) from error

    @classmethod
    def private(
        cls,
        tenant_id: str,
        installation_id: str,
        principal_id: str,
        conversation_id: str,
        message_id: str,
        text: str,
        occurred_at: str,
        *,
        project_id: str | None = None,
        observation_binding: ConversationObservationBinding | None = None,
        media_context: ConversationMediaContext | None = None,
        media_context_sha256: str | None = None,
    ) -> "ConversationTurn":
        """Construct an explicitly private turn for non-group src/connectors/tests."""

        return cls(
            tenant_id=tenant_id,
            installation_id=installation_id,
            principal_id=principal_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=text,
            occurred_at=occurred_at,
            audience=MemoryAudience.private_person(principal_id=principal_id),
            project_id=project_id,
            observation_binding=observation_binding,
            media_context=media_context,
            media_context_sha256=media_context_sha256,
        )

    @property
    def grounded_text(self) -> str:
        context = []
        if self.observation_binding is not None:
            context.append(self.observation_binding.model_context)
        if self.media_context is not None:
            context.append(self.media_context.model_context)
        return ("\n".join(context) + f"\nGebruikersbericht: {self.text}") if context else self.text


@dataclass(frozen=True, slots=True)
class GroundedReply:
    conversation_id: str
    message_id: str
    text: str
    evidence_memory_ids: tuple[str, ...]
    generated_at: str
    provider: str
    model_usage: ConversationModelUsage = field(
        default_factory=ConversationModelUsage.unmeasured
    )
    model_timing: ConversationModelTiming = field(
        default_factory=ConversationModelTiming.unmeasured
    )

    def __post_init__(self) -> None:
        if not isinstance(self.model_usage, ConversationModelUsage):
            raise ValueError("grounded reply model usage is invalid")
        if not isinstance(self.model_timing, ConversationModelTiming):
            raise ValueError("grounded reply model timing is invalid")

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ConversationActionCall:
    call_id: str
    name: str
    arguments: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.call_id.strip() or len(self.call_id) > 240:
            raise ValueError("conversation action call ID is invalid")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("conversation action name is invalid")
        validate_json_value(self.arguments)


@dataclass(frozen=True, slots=True)
class ConversationToolDescriptor:
    """Provider-owned model description for one externally executable tool."""

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    completion_guidance: str = ""

    def __post_init__(self) -> None:
        if _TOOL_NAME.fullmatch(self.name) is None:
            raise ValueError("conversation tool name is invalid")
        if (
            not self.description.strip()
            or len(self.description.encode("utf-8")) > 4_096
        ):
            raise ValueError("conversation tool description is invalid")
        if len(self.completion_guidance.encode("utf-8")) > 4_096:
            raise ValueError("conversation tool completion guidance is invalid")
        validate_json_value(self.input_schema)
        if (
            self.input_schema.get("type") != "object"
            or self.input_schema.get("additionalProperties") is not False
            or not isinstance(self.input_schema.get("properties"), dict)
            or not isinstance(self.input_schema.get("required"), list)
            or any(
                not isinstance(value, str)
                for value in self.input_schema.get("required", [])
            )
            or len(json.dumps(self.input_schema, ensure_ascii=False).encode("utf-8"))
            > 16_384
        ):
            raise ValueError("conversation tool input schema is invalid")


@dataclass(frozen=True, slots=True)
class ConversationToolPreparation:
    """Provider-owned executable command plus safe receipt context."""

    tool_name: str
    arguments: dict[str, JsonValue]
    expected_proof: dict[str, JsonValue]
    receipt_output: dict[str, JsonValue]
    execution_timeout_seconds: int = 10
    verification_timeout_seconds: int = 10
    observation_output_key: str | None = None
    observation_tool_name: str | None = None
    observation_arguments: dict[str, JsonValue] | None = None
    tool_configuration_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_name.strip() or len(self.tool_name) > 240:
            raise ValueError("prepared conversation tool name is invalid")
        validate_json_value(self.arguments)
        validate_json_value(self.expected_proof)
        validate_json_value(self.receipt_output)
        if (self.observation_tool_name is None) != (self.observation_arguments is None):
            raise ValueError("prepared conversation observation command is incomplete")
        if self.observation_tool_name is not None and (
            not self.observation_tool_name.strip()
            or len(self.observation_tool_name) > 240
        ):
            raise ValueError("prepared conversation observation tool is invalid")
        if self.observation_arguments is not None:
            validate_json_value(self.observation_arguments)
        if self.tool_configuration_sha256 is not None and (
            len(self.tool_configuration_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.tool_configuration_sha256
            )
        ):
            raise ValueError("prepared conversation tool configuration is invalid")
        if set(self.receipt_output) & {"contract", "durable", "proof", "status"}:
            raise ValueError("prepared conversation receipt uses reserved fields")
        maximum_execution_seconds = 125 if self.tool_name == "research.public_sources" else 60
        if not 1 <= self.execution_timeout_seconds <= maximum_execution_seconds:
            raise ValueError("prepared conversation execution timeout is invalid")
        if not 1 <= self.verification_timeout_seconds <= 60:
            raise ValueError("prepared conversation verification timeout is invalid")
        if self.observation_output_key is not None and (
            not self.observation_output_key.isidentifier()
            or len(self.observation_output_key) > 80
            or self.observation_output_key in self.receipt_output
            or self.observation_output_key in {"contract", "durable", "proof", "status"}
        ):
            raise ValueError("prepared conversation observation output is invalid")


@dataclass(frozen=True, slots=True)
class ConversationActionReceipt:
    call: ConversationActionCall
    output: dict[str, JsonValue]
    # Host-only presentation; durable journals and completion use output.
    model_output: dict[str, JsonValue] | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        validate_json_value(self.output)
        if self.model_output is not None:
            validate_json_value(self.model_output)


@dataclass(frozen=True, slots=True)
class ConversationModelResult:
    text: str | None
    provider: str
    action_call: ConversationActionCall | None = None
    usage: ConversationModelUsage = field(
        default_factory=ConversationModelUsage.unmeasured
    )
    timing: ConversationModelTiming = field(
        default_factory=ConversationModelTiming.unmeasured
    )

    def __post_init__(self) -> None:
        if self.text is not None and not self.text.strip():
            raise ValueError("conversation model text cannot be blank")
        if not self.provider.strip():
            raise ValueError("conversation model provider is required")
        if self.text is None and self.action_call is None:
            raise ValueError("conversation model returned neither text nor action")
        if not isinstance(self.usage, ConversationModelUsage):
            raise ValueError("conversation model usage is invalid")
        if not isinstance(self.timing, ConversationModelTiming):
            raise ValueError("conversation model timing is invalid")


@dataclass(frozen=True, slots=True)
class RecentConversationMessage:
    role: str
    text: str
    occurred_at: str
    playback_status: Literal["completed", "interrupted", "unconfirmed"] | None = None
    source_turn_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.source_turn_sha256 is not None and (
                not isinstance(self.source_turn_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.source_turn_sha256) is None):
            raise ValueError("recent conversation source turn is invalid")
        if self.playback_status not in (None, "completed", "interrupted", "unconfirmed"):
            raise ValueError("recent conversation playback status is invalid")
        if not self.role.strip() or len(self.role) > 64:
            raise ValueError("recent conversation role is invalid")
        if not self.text.strip() or len(self.text.encode("utf-8")) > 65_536:
            raise ValueError("recent conversation text is invalid")
        try:
            datetime.strptime(self.occurred_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                "recent conversation timestamp must be canonical UTC"
            ) from error


class ConversationMemoryPort(Protocol):
    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory: ...


class ConversationModelPort(Protocol):
    def generate(
        self,
        turn: ConversationTurn,
        identity: IdentityProfile,
        memory: PresenceMemory,
        recent_messages: tuple[RecentConversationMessage, ...],
        *,
        available_actions: tuple[str, ...] = (),
        completed_action: ConversationActionReceipt | None = None,
        prior_actions: tuple[ConversationActionReceipt, ...] = (),
        self_context: tuple[str, ...] = (),
    ) -> ConversationModelResult: ...


class ConversationActionPort(Protocol):
    @property
    def action_names(self) -> tuple[str, ...]: ...

    def pending(self, turn: ConversationTurn) -> ConversationActionReceipt | None: ...

    def active_context(self, turn: ConversationTurn) -> tuple[str, ...]: ...

    def available_action_names(self, turn: ConversationTurn) -> tuple[str, ...]: ...

    def memory_query_hints(self, turn: ConversationTurn) -> tuple[str, ...]: ...

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt: ...


class ConversationExternalToolPort(Protocol):
    """Provider-owned conversation tools routed without provider names in core."""

    @property
    def action_names(self) -> tuple[str, ...]: ...

    def pending(self, turn: ConversationTurn) -> ConversationActionReceipt | None: ...

    def execute(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationActionReceipt: ...


class ConversationExternalToolPlanner(Protocol):
    """Provider-side validation and translation for conversation tool calls."""

    @property
    def action_names(self) -> tuple[str, ...]: ...

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation: ...


class ConversationModelJournal(Protocol):
    """Durable model admissions and reported usage, independent of provider."""

    def begin_model_request(self, turn: ConversationTurn) -> str: ...

    def complete_model_request(
        self, turn: ConversationTurn, request_id: str, usage: ConversationModelUsage,
        *, outcome: str = "succeeded", local_stream_closed: bool | None = None,
    ) -> None: ...

    def model_usage(self, turn: ConversationTurn) -> ConversationModelUsage: ...


_MODEL_REQUEST_JOURNAL: ContextVar[tuple[ConversationTurn, ConversationModelJournal, str | None] | None] = ContextVar(
    "eva_model_request_journal", default=None,
)


@contextmanager
def observe_model_requests(
    turn: ConversationTurn, journal: ConversationModelJournal | None,
    *, style_mirror_sha256: str | None = None,
) -> Iterator[None]:
    if style_mirror_sha256 is not None and (not isinstance(style_mirror_sha256, str)
                                           or not _SHA256.fullmatch(style_mirror_sha256)):
        raise ValueError('model style mirror digest is invalid')
    token = _MODEL_REQUEST_JOURNAL.set(None if journal is None else (turn, journal, style_mirror_sha256))
    try:
        yield
    finally:
        _MODEL_REQUEST_JOURNAL.reset(token)


def begin_current_model_request() -> str | None:
    context = _MODEL_REQUEST_JOURNAL.get()
    if context is None:
        return None
    request_id = context[1].begin_model_request(context[0])
    _record_model_memory_context(context[0], context[1], request_id, context[2])
    return request_id


def _record_model_memory_context(turn, journal, request_id, digest):
    record = getattr(journal, 'record_model_memory_context', None)
    if digest is not None and callable(record):
        record(turn, request_id, style_mirror_sha256=digest)


def complete_current_model_request(
    request_id: str | None, usage: ConversationModelUsage, *, outcome: str = "succeeded",
    local_stream_closed: bool | None = None,
) -> None:
    context = _MODEL_REQUEST_JOURNAL.get()
    if context is not None and request_id is not None:
        if local_stream_closed is True:
            context[1].complete_model_request(context[0], request_id, usage,
                                             outcome=outcome, local_stream_closed=True)
        elif outcome == "succeeded":
            context[1].complete_model_request(context[0], request_id, usage)
        else:
            context[1].complete_model_request(context[0], request_id, usage, outcome=outcome)


class LivingConversation:
    """Recall memory, then let Eva's own model formulate one answer.

    Hindsight is deliberately limited to retrieval on this hot path.  Its
    agentic reflection belongs to a separate idle/background worker.
    """

    def __init__(
        self,
        memory: ConversationMemoryPort,
        model: ConversationModelPort,
        identity: IdentityProfile,
        actions: ConversationActionPort | None = None,
        *,
        before_step: Callable[[ConversationTurn, ConversationModelUsage], None] | None = None,
        contact_model: ConversationModelPort | None = None,
        history_search: Callable[[ConversationTurn, str], tuple[RecentConversationMessage, ...]] | None = None,
    ) -> None:
        self._memory = memory
        self._model = model
        self._identity = identity
        self._actions = actions
        self._before_step = before_step
        self._contact_model = contact_model
        self._history_search = history_search

    def respond(
        self,
        turn: ConversationTurn,
        recent_messages: tuple[RecentConversationMessage, ...] = (),
        *,
        before_step: Callable[[ConversationTurn, ConversationModelUsage], None] | None = None,
        model_journal: ConversationModelJournal | None = None,
    ) -> GroundedReply:
        hint_loader = (
            None
            if self._actions is None
            else getattr(self._actions, "memory_query_hints", None)
        )
        memory_query_hints = hint_loader(turn) if callable(hint_loader) else ()
        from eva.application.initiative_execution import require_initiative_context
        initiative = require_initiative_context(turn)
        contact_model = self._contact_model if initiative is None else None

        def recall(query_text: str, *, fast: bool = False) -> PresenceMemory:
            grounded_query = query_text
            if turn.observation_binding is not None:
                grounded_query = (
                    f"{grounded_query}\n{turn.observation_binding.model_context}\n"
                    "Zoek binnen exact deze gebonden foto en onderwerpregio naar "
                    "eerdere correcties, identiteit en persoonsnaam."
                )
                if memory_query_hints:
                    grounded_query += "\nGebonden retrieval-ankers: " + ", ".join(
                        memory_query_hints
                    )
            query = RecallCurrentFacts(
                recall_id=f"conversation:{turn.message_id}",
                installation_id=turn.installation_id,
                tenant_id=turn.tenant_id,
                principal_id=turn.principal_id,
                audience=turn.audience,
                conversation_id=turn.conversation_id,
                query_purpose="conversation_reply",
                requested_valid_at=turn.occurred_at,
                project_id=turn.project_id,
                scope=(
                    {"kind": "household", "household_id": turn.audience.household_id}
                    if turn.audience.kind == "household"
                    else {
                        "kind": "telegram_group",
                        "group_chat_id": turn.audience.group_chat_id,
                    }
                    if turn.audience.kind == "telegram_group"
                    else {
                        "kind": "person",
                        "principal_id": turn.principal_id,
                        **(
                            {}
                            if turn.observation_binding is None
                            else {
                                "observation_binding_sha256": (
                                    turn.observation_binding.binding_sha256
                                )
                            }
                        ),
                    }
                    if turn.project_id is None
                    else {
                        "kind": "person_project",
                        "principal_id": turn.principal_id,
                        "project_id": turn.project_id,
                        **(
                            {}
                            if turn.observation_binding is None
                            else {
                                "observation_binding_sha256": (
                                    turn.observation_binding.binding_sha256
                                )
                            }
                        ),
                    }
                ),
                subject_ids=(turn.principal_id,),
                query_text=grounded_query,
                maximum_facts=12,
            )
            fast_recall = getattr(self._memory, "recall_for_presence_fast", None)
            recalled = (
                fast_recall(query)
                if fast and callable(fast_recall)
                else self._memory.recall_for_presence(query)
            )
            return self._bound_observation_memory(turn, recalled)

        usages: list[ConversationModelUsage] = []
        timings: list[ConversationModelTiming] = []

        def total_usage() -> ConversationModelUsage:
            return (model_journal.model_usage(turn) if model_journal is not None
                    else ConversationModelUsage.aggregate(tuple(usages)))

        def checkpoint() -> None:
            usage = total_usage()
            if self._before_step is not None:
                self._before_step(turn, usage)
            if before_step is not None:
                before_step(turn, usage)

        def continuation_names(receipt: ConversationActionReceipt) -> tuple[str, ...]:
            selector = getattr(self._actions, "continuation_action_names", None)
            admitted_work = any(item.call.name in {
                "start_background_research", "start_digital_work",
                "start_delegated_work", "resume_delegated_work",
            } for item in (*receipts, receipt))
            if admitted_work and (contact_model is not None or any(
                item.call.name in {"start_background_research", "start_digital_work"}
                for item in (*receipts, receipt)
            )):
                # An admitted task may need owner-requested dependent planning.
                # Keep the worker asynchronous: never offer status polling or
                # more foreground execution after admission, including after
                # a schedule receipt or replay of an interrupted planning turn.
                if receipt.output.get("status") != "verified" or not callable(selector):
                    return ()
                return tuple(name for name in selector(turn)
                             if name == "schedule_digital_work")
            if not callable(selector):
                return ()
            if receipt.output.get("status") != "verified":
                permits = getattr(self._actions, "can_continue_after", None)
                if not callable(permits) or permits(turn, receipt) is not True:
                    return ()
            return selector(turn)

        checkpoint()
        completed = None if self._actions is None else self._actions.pending(turn)
        receipts: list[ConversationActionReceipt] = []
        if completed is not None:
            receipts.append(completed)
            pending_step = getattr(self._actions, "pending_step", None)
            # Recover admitted steps before asking the model to choose again.
            while callable(pending_step):
                if completed.output.get("status") != "verified":
                    permits = getattr(self._actions, "can_continue_after", None)
                    if not callable(permits) or permits(turn, completed) is not True:
                        break
                checkpoint()
                recovered = pending_step(turn, step_index=len(receipts))
                if recovered is None:
                    break
                if recovered.call.call_id in {item.call.call_id for item in receipts}:
                    raise ConversationConflict("conversation replay has duplicate call IDs")
                receipts.append(recovered)
                completed = recovered
        available_names: tuple[str, ...] = ()
        if self._actions is not None:
            if completed is None:
                action_selector = getattr(self._actions, "available_action_names", None)
                available_names = (
                    action_selector(turn)
                    if callable(action_selector)
                    else self._actions.action_names
                )
            else:
                available_names = continuation_names(completed)
        def initial_memory():
            memory_started_at = monotonic()
            memory_outcome = "failed"
            try:
                memory = recall(turn.text, fast=True)
                memory_outcome = "succeeded"
            finally:
                _INITIAL_MEMORY_TIMING_LOG.info("conversation_initial_memory_timing %s", json.dumps({
                    "message_id_sha256": hashlib.sha256(turn.message_id.encode()).hexdigest(),
                    "elapsed_ms": max(0, round((monotonic() - memory_started_at) * 1000)),
                    "outcome": memory_outcome,
                }, separators=(",", ":")))
            return memory

        if self._actions is None:
            self_context = ()
            memory = initial_memory()
        else:
            # Both reads remain owned by this turn, including on failure. Carry
            # cancellation, voice and background authority into the read lane.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="eva-context") as preparation:
                context = preparation.submit(copy_context().run, self._actions.active_context, turn)
                memory = initial_memory()
                self_context = context.result()
        from eva.application.cancellation import check_cancellation
        check_cancellation()
        if contact_model is not None:
            self_context = (*self_context,
                "Answer only simple questions directly, warmly and briefly. Delegate research, "
                "substantial analysis, creation and Eva improvements. start_delegated_work is "
                "for tool-free thinking/writing, not current sources or external actions. "
                "Confirm only durable admission, using a short acceptance_message, never completion. "
                "Keep work_id; do not wait or poll. New work does not stop old work. Use the stop "
                "tool for an explicit stop or incompatible correction; clarify an ambiguous target. "
                "Delegate only context authorized for both this person and audience.")
        if "start_digital_work" in available_names:
            self_context = (*self_context,
                "Use start_digital_work BEFORE discovery or partial execution for multi-step "
                "connector work: reading, comparison, saving, actions, HA automation/configuration "
                "analysis, learning and skill reuse. A simple current-state read or known house "
                "action may run directly. The worker has its own authorized tools; do not infer "
                "missing capability from the foreground catalog. Let it check access within the "
                "existing mandate. Pass the full task and relevant authorized context; for learning "
                "include the goal, application and retaining the method. For reuse, have it fetch "
                "and apply the stored method rather than asking for it again. Text alone cannot "
                "fulfil an explicit request to read sources, save or act. Confirm admission only; "
                "remain available and use work_id for status/stop. If admission fails, state the "
                "blocker without claiming execution. If the request combines work now with "
                "a later dependent step, delegate only the current step and set acceptance_message to null. "
                "Then use schedule_digital_work with the returned work_id as after_work_id and "
                "the requested time. Keep scheduling in this conversation; workers cannot schedule "
                "their own continuation. Confirm a future step only from its stored schedule receipt. "
                "For ordinary work without follow-up planning, keep the immediate acceptance.")
        if "start_background_research" in available_names:
            self_context = (*self_context,
                "Use start_background_research for explicit public-source research, with only "
                "the public question, never private context or recalled memories. Confirm durable "
                "admission, remain available, and use existing task controls for status/stop/resume.")
        from eva.application.voice_context import current_voice_context, STOP_VOICE_WORK, SPEAK_AND_CONTINUE
        voice = current_voice_context()
        if voice is not None:
            self_context = (*self_context, *voice.instructions())
            available_names = (*available_names, SPEAK_AND_CONTINUE)
            if voice.active_work:
                available_names = (*available_names, STOP_VOICE_WORK)
        if initiative is not None:
            self_context = (*self_context, initiative.instructions())
        memory_searched = False
        action_input_repaired = False
        input_rejection_prompt = ""
        seen_call_ids = {item.call.call_id for item in receipts}
        while True:
            if contact_model is not None:
                # Offer admitted background work instead of new synchronous
                # execution. Reapply after lookups; retained receipts are replayed
                # above, and routes without the corresponding worker stay intact.
                deferred_actions = set()
                if completed is None and "start_background_research" in available_names:
                    deferred_actions.add("research_current_options")
                if "start_digital_work" in available_names:
                    deferred_actions.add("run_reusable_procedure")
                available_names = tuple(name for name in available_names if name not in deferred_actions)
            checkpoint()
            repairing_input = completed is not None and completed.output.get("status") == "invalid_input"
            prior_actions = receipts if repairing_input else receipts[:-1]
            history = {"prior_actions": tuple(prior_actions)} if prior_actions else {}
            # Routing uses actual receipts, never channel names or greeting regexes.
            # Acknowledging durable work and searching memory stay on the contact
            # model. Tool execution / spoken continuation use the existing work model.
            contact_receipts = {
                MEMORY_SEARCH_ACTION, STOP_VOICE_WORK,
                "list_reusable_procedures", "resolve_reusable_procedure",
                "start_delegated_work", "start_background_research", "start_digital_work", "list_delegated_work", "get_delegated_work", "get_related_work", "search_delegated_work", "revise_digital_work",
                "stop_delegated_work", "stop_related_work", "resume_delegated_work",
                "list_own_initiatives", "get_own_initiative_result",
                "stop_own_initiative", "give_initiative_feedback",
                "schedule_digital_work", "schedule_work_resume", "list_scheduled_work", "cancel_scheduled_work", "cancel_work_chain",
            }
            def contact_receipt(receipt):
                # Reading already captured bytes adds no external operation.
                # Keep the original model only while the ENTIRE history remains
                # ordinary contact/read work; a real work action still escalates.
                return receipt.call.name in contact_receipts or (
                    receipt.call.name == "use_connector_tool"
                    and isinstance(receipt.call.arguments.get("tool_id"), str)
                    and receipt.call.arguments["tool_id"].startswith("eva-stored-result:")
                    and receipt.output.get("source") == "stored_connector_result"
                    and receipt.output.get("external_state_verified") is False
                    and receipt.output.get("status") in {"verified", "selection_invalid"})

            model = (contact_model
                     if contact_model is not None
                     and (completed is None or contact_receipt(completed))
                     and all(contact_receipt(item) for item in receipts)
                     else self._model)
            individual_requests = getattr(model, "journals_provider_requests", False) is True
            request_id = (None if model_journal is None or individual_requests
                          else model_journal.begin_model_request(turn))
            style_digest = (None if memory.style_mirror is None else
                            hashlib.sha256(memory.style_mirror.encode('utf-8')).hexdigest())
            if request_id is not None:
                _record_model_memory_context(turn, model_journal, request_id, style_digest)
            guard_factory = getattr(self._actions, "reply_guard", None)
            reply_guard = (guard_factory(turn, tuple(
                item for item in receipts if item.call.name not in {STOP_VOICE_WORK, SPEAK_AND_CONTINUE}
            )) if callable(guard_factory) else None)
            # A durable action may change while its acknowledgement is being
            # generated. Do not publish provisional claims before its guard.
            with observe_model_requests(turn, model_journal if individual_requests else None,
                                        style_mirror_sha256=style_digest), (
                stream_conversation_text(lambda _delta: None)
                if reply_guard is not None or repairing_input else nullcontext()
            ):
                generated = model.generate(
                    turn,
                    self._identity,
                    memory,
                    recent_messages,
                    available_actions=available_names,
                    completed_action=completed,
                    self_context=self_context,
                    **history,
                )
            if model_journal is not None and request_id is not None:
                model_journal.complete_model_request(turn, request_id, generated.usage)
            usages.append(generated.usage)
            timings.append(generated.timing)
            call = generated.action_call
            if call is None:
                if repairing_input:
                    generated = replace(generated, text=input_rejection_prompt)
                break
            checkpoint()
            if call.call_id in seen_call_ids:
                raise ConversationConflict("conversation model reused a completed call ID")
            seen_call_ids.add(call.call_id)
            if call.name == MEMORY_SEARCH_ACTION:
                if memory_searched or receipts:
                    raise RuntimeError("conversation memory search is unavailable")
                arguments = call.arguments
                query_text = arguments.get("query_text")
                if (
                    set(arguments) != {"query_text"}
                    or not isinstance(query_text, str)
                    or not query_text.strip()
                    or len(query_text.encode("utf-8")) > 1_024
                ):
                    raise RuntimeError("conversation memory search request is invalid")
                expanded = recall(f"{turn.text}\nSemantische zoekconcepten: {query_text}")
                if expanded.confirmed_facts:
                    memory = expanded
                exchanges = (
                    self._history_search(turn, query_text)
                    if self._history_search is not None
                    and turn.audience.kind == "private_person" and turn.project_id is None
                    else ()
                )
                completed = ConversationActionReceipt(
                    call=call, output={
                        "result_count": len(expanded.confirmed_facts),
                        "conversation_exchanges": [
                            {"occurred_at": item.occurred_at, "text": item.text}
                            for item in exchanges
                        ],
                        "history_search_scope": "bounded_retained_private_source_matches",
                    },
                )
                memory_searched = True
                continue
            if voice is not None and call.name == SPEAK_AND_CONTINUE and call.name in available_names:
                completed = voice.speak_and_continue(call)
                receipts.append(completed)
                available_names = tuple(name for name in available_names if name != SPEAK_AND_CONTINUE)
                continue
            if voice is not None and call.name == STOP_VOICE_WORK and call.name in available_names:
                completed = voice.stop_work(call)
                receipts.append(completed)
                # Keep the usual tools available to implement a corrected request.
                continue
            if self._actions is None or call.name not in available_names:
                raise RuntimeError("conversation model requested an unavailable action")
            if voice is not None:
                voice.event({"type": "action_started", "name": call.name})
            try:
                if any(item.call.name not in {STOP_VOICE_WORK, SPEAK_AND_CONTINUE} for item in receipts):
                    execute_step = getattr(self._actions, "execute_step", None)
                    if not callable(execute_step):
                        raise ConversationConflict("conversation continuation is unavailable")
                    completed = execute_step(turn, call, step_index=sum(item.call.name not in {STOP_VOICE_WORK, SPEAK_AND_CONTINUE} for item in receipts))
                    if completed.call != call:
                        raise ConversationConflict("conversation step returned another call")
                else:
                    completed = self._actions.execute(turn, call)
            except ConversationClarificationRequired as clarification:
                if isinstance(clarification, ConversationActionInputRejected) and not action_input_repaired:
                    # Nothing was admitted. Let the same model correct its own
                    # arguments once, within the existing request/turn budget.
                    action_input_repaired = True
                    input_rejection_prompt = clarification.prompt
                    completed = ConversationActionReceipt(call, {
                        "status": "invalid_input", "action_admitted": False,
                        "invalid_fields": list(clarification.fields),
                        "instruction": "Correct the tool arguments using its schema. No work has started.",
                    })
                    continue
                return GroundedReply(
                    conversation_id=turn.conversation_id,
                    message_id=turn.message_id,
                    text=clarification.prompt,
                    evidence_memory_ids=tuple(
                        item.fact_id for item in memory.recall.selected
                    ),
                    generated_at=turn.occurred_at,
                    provider=generated.provider,
                    model_usage=total_usage(),
                    model_timing=ConversationModelTiming.aggregate(tuple(timings)),
                )
            receipts.append(completed)
            if voice is not None:
                voice.event({"type": "action_finished", "name": call.name,
                             "status": completed.output.get("status", "unknown")})
            acceptance = completed.output.get("acceptance_message")
            if (call.name in {"start_delegated_work", "start_background_research", "start_digital_work"}
                    and completed.output.get("status") == "verified"
                    and isinstance(acceptance, str) and acceptance.strip()
                    and len(acceptance) <= 240 and callable(guard_factory)):
                # The admitting model already drafted a natural acceptance.
                # Earlier lookup steps do not require another model reply.
                # Publish only after durable admission and the same
                # stop/authority guard used by the ordinary second-model reply.
                reply_guard = guard_factory(turn, tuple(receipts))
                if reply_guard is not None:
                    generated = replace(generated, text=acceptance, action_call=None)
                    break
            available_names = continuation_names(completed)
            if voice is not None and voice.active_work:
                available_names = (*available_names, STOP_VOICE_WORK)
        text = "" if generated.text is None else generated.text.strip()
        if not text:
            raise RuntimeError("conversation model returned an empty reply")
        text = _bounded_reply_text(text, self._identity.maximum_reply_words)
        if reply_guard is not None:
            text = reply_guard(text)
        return GroundedReply(
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            text=text,
            evidence_memory_ids=tuple(item.fact_id for item in memory.recall.selected),
            generated_at=turn.occurred_at,
            provider=generated.provider,
            model_usage=total_usage(),
            model_timing=ConversationModelTiming.aggregate(tuple(timings)),
        )

    @staticmethod
    def _bound_observation_memory(
        turn: ConversationTurn, memory: PresenceMemory
    ) -> PresenceMemory:
        expected = (
            None
            if turn.observation_binding is None
            else f"observation-binding:{turn.observation_binding.binding_sha256}"
        )
        kept_facts = tuple(
            fact
            for fact in memory.confirmed_facts
            if not fact.evidence_ref.startswith("observation-binding:")
            or fact.evidence_ref == expected
        )
        if kept_facts == memory.confirmed_facts:
            return memory
        kept_ids = {fact.fact_id for fact in kept_facts}
        kept_selected = []
        excluded = dict(memory.recall.excluded_reasons)
        for item in memory.recall.selected:
            if item.fact_id in kept_ids:
                kept_selected.append(
                    item.model_copy(update={"rank": len(kept_selected) + 1})
                )
            else:
                excluded[item.fact_id] = "observation_binding_mismatch"
        return PresenceMemory(
            memory.recall.model_copy(
                update={
                    "excluded_reasons": excluded,
                    "selected": kept_selected,
                }
            ),
            kept_facts,
            memory.style_mirror,
        )


__all__ = [
    "ConversationActionCall",
    "ConversationActionPort",
    "ConversationActionReceipt",
    "ConversationExternalToolPort",
    "ConversationExternalToolPlanner",
    "ConversationToolDescriptor",
    "ConversationToolPreparation",
    "ConversationClarificationRequired",
    "ConversationConflict",
    "ConversationObservationBinding",
    "ConversationTurn",
    "ConversationMemoryPort",
    "ConversationModelUsage",
    "ConversationModelPort",
    "ConversationModelResult",
    "GroundedReply",
    "IdentityProfile",
    "LivingConversation",
    "MEMORY_SEARCH_ACTION",
    "RecentConversationMessage",
    "publish_conversation_text",
    "stream_conversation_text",
]
