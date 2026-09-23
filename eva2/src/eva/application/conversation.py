"""Channel-neutral conversation boundary for Eva's Living Core."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
import re
from typing import Callable, Iterator, Literal, Protocol

from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.identity import IdentityPack

MEMORY_SEARCH_ACTION = "search_personal_memory"


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


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_TEXT_DELTA_SINK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "eva_conversation_text_delta_sink", default=None
)


@contextmanager
def stream_conversation_text(sink: Callable[[str], None]) -> Iterator[None]:
    token = _TEXT_DELTA_SINK.set(sink)
    try:
        yield
    finally:
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
        sink(delta)


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
    ) -> "ConversationTurn":
        """Construct an explicitly private turn for non-group connectors/tests."""

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
        )

    @property
    def grounded_text(self) -> str:
        if self.observation_binding is None:
            return self.text
        return (
            f"{self.observation_binding.model_context}\nGebruikersbericht: {self.text}"
        )


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
        if not 1 <= self.execution_timeout_seconds <= 60:
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

    def __post_init__(self) -> None:
        validate_json_value(self.output)


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


_MODEL_REQUEST_JOURNAL: ContextVar[tuple[ConversationTurn, ConversationModelJournal] | None] = ContextVar(
    "eva_model_request_journal", default=None,
)


@contextmanager
def observe_model_requests(
    turn: ConversationTurn, journal: ConversationModelJournal | None,
) -> Iterator[None]:
    token = _MODEL_REQUEST_JOURNAL.set(None if journal is None else (turn, journal))
    try:
        yield
    finally:
        _MODEL_REQUEST_JOURNAL.reset(token)


def begin_current_model_request() -> str | None:
    context = _MODEL_REQUEST_JOURNAL.get()
    return None if context is None else context[1].begin_model_request(context[0])


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
    ) -> None:
        self._memory = memory
        self._model = model
        self._identity = identity
        self._actions = actions
        self._before_step = before_step
        self._contact_model = contact_model

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
            if receipt.call.name in {"start_background_research", "start_digital_work"} or (
                contact_model is not None
                and receipt.call.name in {"start_delegated_work", "resume_delegated_work"}
            ):
                # Durable admission is the end of this foreground request, not
                # an invitation to poll the worker until its result is ready.
                return ()
            selector = getattr(self._actions, "continuation_action_names", None)
            if receipt.output.get("status") != "verified" or not callable(selector):
                return ()
            return selector(turn)

        checkpoint()
        completed = None if self._actions is None else self._actions.pending(turn)
        receipts: list[ConversationActionReceipt] = []
        if completed is not None:
            receipts.append(completed)
            pending_step = getattr(self._actions, "pending_step", None)
            # Recover admitted steps before asking the model to choose again.
            while completed.output.get("status") == "verified" and callable(pending_step):
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
        self_context = (
            () if self._actions is None else self._actions.active_context(turn)
        )
        if contact_model is not None:
            self_context = (*self_context,
                "Houd het contact natuurlijk en kort. Beantwoord eenvoudige vragen direct. "
                "Gebruik voor langere denk-, schrijf- of analysetaken start_delegated_work "
                "als die tool beschikbaar is en de taak zonder browser of externe tools kan. "
                "Deze hulpagent kan geen actuele bronnen raadplegen of handelingen uitvoeren. "
                "Neem zulk werk alleen met de daarvoor aangeboden echte tools aan. "
                "Bevestig achtergrondwerk pas na de opgeslagen toelating. Als de starttool "
                "acceptance_message aanbiedt, schrijf daarin één korte natuurlijke ontvangstzin "
                "voor deze opdracht, zonder afronding te claimen. Die zin wordt alleen na "
                "werkelijke toelating getoond. Bewaar work_id voor vervolgvragen en stoppen. "
                "Wacht daarna niet op voltooiing en poll niet: laat de gebruiker doorpraten. "
                "Een nieuwe opdracht stopt eerder werk niet. Gebruik bij een expliciete stop "
                "of onverenigbare correctie de aangeboden stoptool voor het juiste work_id; "
                "vraag bij een onduidelijk doel welke taak bedoeld wordt. Deel bij delegatie "
                "alleen context die voor deze persoon en dit publiek is toegestaan.")
        if "start_digital_work" in available_names:
            self_context = (*self_context,
                "Gebruik start_digital_work voor een digitale opdracht met bestaande toegestane "
                "connectoren die op de achtergrond moet doorgaan. Neem die opdracht rechtstreeks "
                "met start_digital_work aan: voer niet eerst connectorstatus, toolontdekking of "
                "een deel van de opdracht in dit gesprek uit. De werker zoekt en controleert "
                "zelf de benodigde tools en bereikbaarheid binnen het bestaande mandaat. "
                "Een opgeslagen toelating bevestigt alleen dat de opdracht is aangenomen, "
                "niet dat de uitvoering al lukt. "
                "Geef de concrete opdracht en de relevante toegestane gegevens mee, "
                "bevestig pas na opgeslagen toelating en wacht niet op het resultaat. "
                "Laat de gebruiker verder praten; gebruik work_id voor volgen of stoppen. "
                "Een verzoek om een digitale vaardigheid of werkwijze te leren en later te "
                "hergebruiken hoort ook bij start_digital_work, niet bij een toolvrije hulpagent "
                "of alleen een publiek onderzoeksantwoord. Geef het leerdoel, de toepassing "
                "en de wens om de werkwijze te bewaren mee. Bij later gebruik van een bewaarde "
                "werkwijze laat je de werker de actuele inhoud ophalen en toepassen; vraag "
                "de gebruiker niet om de werkwijze opnieuw uit te leggen.")
        if "start_background_research" in available_names:
            self_context = (*self_context,
                "Gebruik start_background_research voor een expliciete opdracht om publieke "
                "bronnen te onderzoeken terwijl het gesprek doorgaat. Stuur alleen de publieke "
                "onderzoeksvraag; geen privégegevens of opgehaalde herinneringen. Bevestig pas "
                "na opgeslagen toelating en wacht niet op het eindresultaat. De bestaande "
                "taakbediening kan de juiste opdracht volgen, stoppen en hervatten.")
        from eva.application.voice_context import current_voice_context, STOP_VOICE_WORK, SPEAK_AND_CONTINUE
        voice = current_voice_context()
        if voice is not None:
            self_context = (*self_context, *voice.instructions())
            available_names = (*available_names, SPEAK_AND_CONTINUE)
            if voice.active_work:
                available_names = (*available_names, STOP_VOICE_WORK)
        if initiative is not None:
            self_context = (*self_context, initiative.instructions())
        memory = recall(turn.text, fast=True)
        memory_searched = False
        seen_call_ids = {item.call.call_id for item in receipts}
        while True:
            checkpoint()
            history = {"prior_actions": tuple(receipts[:-1])} if len(receipts) > 1 else {}
            # Routing uses actual receipts, never channel names or greeting regexes.
            # Acknowledging durable work and searching memory stay on the contact
            # model. Tool execution / spoken continuation use the existing work model.
            contact_receipts = {
                MEMORY_SEARCH_ACTION, STOP_VOICE_WORK,
                "start_delegated_work", "start_background_research", "start_digital_work", "list_delegated_work", "get_delegated_work",
                "stop_delegated_work", "resume_delegated_work",
            }
            model = (contact_model
                     if contact_model is not None
                     and (completed is None or completed.call.name in contact_receipts)
                     and all(item.call.name in contact_receipts for item in receipts)
                     else self._model)
            individual_requests = getattr(model, "journals_provider_requests", False) is True
            request_id = (None if model_journal is None or individual_requests
                          else model_journal.begin_model_request(turn))
            guard_factory = getattr(self._actions, "reply_guard", None)
            reply_guard = (guard_factory(turn, tuple(
                item for item in receipts if item.call.name not in {STOP_VOICE_WORK, SPEAK_AND_CONTINUE}
            )) if callable(guard_factory) else None)
            # A durable action may change while its acknowledgement is being
            # generated. Do not publish provisional claims before its guard.
            with observe_model_requests(turn, model_journal if individual_requests else None), (
                stream_conversation_text(lambda _delta: None)
                if reply_guard is not None else nullcontext()
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
                completed = ConversationActionReceipt(
                    call=call, output={"result_count": len(expanded.confirmed_facts)},
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
            if (model is contact_model and len(receipts) == 1
                    and call.name in {"start_delegated_work", "start_background_research", "start_digital_work"}
                    and completed.output.get("status") == "verified"
                    and isinstance(acceptance, str) and acceptance.strip()
                    and len(acceptance) <= 240 and callable(guard_factory)):
                # The first model drafts a natural acceptance inside its tool
                # arguments. Publish only after durable admission and the same
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
