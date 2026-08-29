"""Channel-neutral conversation boundary for Eva's Living Core."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Iterator, Literal, Protocol

from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.contracts.base import JsonValue, validate_json_value
from eva.contracts.identity import IdentityPack
from eva.application.identity_verifier import (
    IdentityPackTrustBinding,
    load_default_identity_pack_trust_binding,
    verify_identity_pack_bytes,
)


class ConversationConflict(RuntimeError):
    """A replay reused a durable message identity for different content."""


class ConversationClarificationRequired(RuntimeError):
    """A safe action needs one ordinary follow-up answer from the user."""

    def __init__(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt or len(prompt) > 240:
            raise ValueError("conversation clarification prompt is invalid")
        self.prompt = prompt
        super().__init__(prompt)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TEXT_DELTA_SINK: ContextVar[Callable[[str], None] | None] = ContextVar("eva_conversation_text_delta_sink", default=None)


@contextmanager
def stream_conversation_text(sink: Callable[[str], None]) -> Iterator[None]:
    token = _TEXT_DELTA_SINK.set(sink)
    try:
        yield
    finally:
        _TEXT_DELTA_SINK.reset(token)


def publish_conversation_text(delta: str, *, tools_disabled: bool) -> None:
    sink = _TEXT_DELTA_SINK.get()
    if sink is not None and tools_disabled and delta:
        sink(delta)


@dataclass(frozen=True, slots=True)
class ConversationModelUsage:
    """Provider-neutral reported usage or an explicit legacy unknown value."""

    measurement: Literal["measured", "unmeasured"]
    model_attempts: int | None
    tool_calls: int | None
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        values = (self.model_attempts, self.tool_calls, self.input_tokens, self.output_tokens)
        if self.measurement == "unmeasured":
            if any(value is not None for value in values):
                raise ValueError("unmeasured model usage cannot carry values")
            return
        if self.measurement != "measured" or any(value is None for value in values):
            raise ValueError("measured model usage is incomplete")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise ValueError("measured model usage is invalid")
        attempts, tools, inputs, outputs = (int(value) for value in values)
        if attempts < 1 or min(tools, inputs, outputs) < 0:
            raise ValueError("measured model usage is invalid")

    @classmethod
    def measured(cls, *, model_attempts: int = 1, tool_calls: int,
                 input_tokens: int, output_tokens: int) -> "ConversationModelUsage":
        return cls("measured", model_attempts, tool_calls, input_tokens, output_tokens)

    @classmethod
    def unmeasured(cls) -> "ConversationModelUsage":
        return cls("unmeasured", None, None, None, None)

    @classmethod
    def aggregate(cls, usages: tuple["ConversationModelUsage", ...]) -> "ConversationModelUsage":
        if not usages or any(usage.measurement == "unmeasured" for usage in usages):
            return cls.unmeasured()
        return cls.measured(model_attempts=sum(usage.model_attempts or 0 for usage in usages),
                            tool_calls=sum(usage.tool_calls or 0 for usage in usages),
                            input_tokens=sum(usage.input_tokens or 0 for usage in usages),
                            output_tokens=sum(usage.output_tokens or 0 for usage in usages))

    def to_wire(self) -> dict[str, str | int | None]:
        return {"input_tokens": self.input_tokens, "measurement": self.measurement,
                "model_attempts": self.model_attempts, "output_tokens": self.output_tokens,
                "tool_calls": self.tool_calls}

    @classmethod
    def from_wire(cls, value: object) -> "ConversationModelUsage":
        fields = {"input_tokens", "measurement", "model_attempts", "output_tokens", "tool_calls"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("model usage payload is invalid")
        measurement = value["measurement"]
        numeric = (value["model_attempts"], value["tool_calls"], value["input_tokens"], value["output_tokens"])
        if measurement not in {"measured", "unmeasured"} or any(
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
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
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
        first = None if final_first is None else completed - (timings[-1].completed_ms or 0) + final_first
        return cls(first, completed)

    def to_wire(self) -> dict[str, int | None]:
        return {"completed_ms": self.completed_ms, "first_content_ms": self.first_content_ms}

    @classmethod
    def from_wire(cls, value: object) -> "ConversationModelTiming":
        if not isinstance(value, dict) or set(value) != {"completed_ms", "first_content_ms"}:
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


def load_current_identity_pack(
    path: Path | None = None, *, trust: IdentityPackTrustBinding | None = None
) -> IdentityPack:
    """Load the versioned procedural identity used by the Living Core."""

    selected = path or Path(__file__).with_name("default_identity_pack.json")
    if selected.is_symlink() or not selected.is_file():
        raise ValueError("IdentityPack must be a regular non-symlink file")
    if selected.stat().st_size > 65_536:
        raise ValueError("IdentityPack is too large")
    try:
        raw = selected.read_bytes()
        pack = verify_identity_pack_bytes(
            raw, trust=trust or load_default_identity_pack_trust_binding()
        )
    except (OSError, ValueError) as error:
        raise ValueError("IdentityPack is invalid") from error
    return pack


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

    def __post_init__(self) -> None:
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


@dataclass(frozen=True, slots=True)
class MemorySearchPlan:
    """Model-understood semantic lookup and bounded per-turn capability choice."""

    needs_semantic_memory: bool
    query_text: str | None
    selected_action_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.needs_semantic_memory:
            if (
                self.query_text is None
                or not self.query_text.strip()
                or len(self.query_text.encode("utf-8")) > 1_024
            ):
                raise ValueError("semantic memory query is invalid")
        elif self.query_text is not None:
            raise ValueError("unused semantic memory query must be null")
        if (
            len(self.selected_action_names) > 4
            or len(set(self.selected_action_names)) != len(self.selected_action_names)
            or any(
                not isinstance(name, str)
                or not name.strip()
                or len(name.encode("utf-8")) > 128
                for name in self.selected_action_names
            )
        ):
            raise ValueError("selected conversation actions are invalid")


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
    ) -> None:
        self._memory = memory
        self._model = model
        self._identity = identity
        self._actions = actions

    def respond(
        self,
        turn: ConversationTurn,
        recent_messages: tuple[RecentConversationMessage, ...] = (),
    ) -> GroundedReply:
        hint_loader = (
            None
            if self._actions is None
            else getattr(self._actions, "memory_query_hints", None)
        )
        memory_query_hints = hint_loader(turn) if callable(hint_loader) else ()

        def recall(query_text: str) -> PresenceMemory:
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
            recalled = self._memory.recall_for_presence(
                RecallCurrentFacts(
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
                        {
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
            )
            return self._bound_observation_memory(turn, recalled)

        completed = None if self._actions is None else self._actions.pending(turn)
        available_names: tuple[str, ...] = ()
        if self._actions is not None and completed is None:
            action_selector = getattr(self._actions, "available_action_names", None)
            available_names = (
                action_selector(turn)
                if callable(action_selector)
                else self._actions.action_names
            )
        self_context = () if self._actions is None else self._actions.active_context(turn)
        memory_query = getattr(self._model, "expand_memory_query", None)
        plan: MemorySearchPlan | None = None
        draft = None
        if callable(memory_query):
            with ThreadPoolExecutor(max_workers=3) as executor:
                memory_future = executor.submit(recall, turn.text)
                plan_future = executor.submit(memory_query, turn, recent_messages)
                draft_future = executor.submit(
                    lambda: self._model.generate(
                        turn, self._identity, memory_future.result(), recent_messages,
                        completed_action=completed, self_context=self_context))
                memory = memory_future.result()
                try:
                    plan = plan_future.result()
                except RuntimeError:
                    plan = None
                try:
                    draft = draft_future.result()
                except RuntimeError:
                    draft = None
            if (
                isinstance(plan, MemorySearchPlan)
                and plan.needs_semantic_memory
                and plan.query_text is not None
            ):
                expanded = recall(
                    f"{turn.text}\nSemantische zoekconcepten: {plan.query_text}"
                )
                if expanded.confirmed_facts:
                    memory = expanded
            available_names = tuple(
                name
                for name in available_names
                if plan is not None and name in plan.selected_action_names
            )
        else:
            memory = recall(turn.text)
        if draft is not None and (plan is None or (
            not plan.needs_semantic_memory and not plan.selected_action_names)):
            generated = draft
            publish_conversation_text(generated.text, tools_disabled=True)
        else:
            generated = self._model.generate(
                turn, self._identity, memory, recent_messages,
                available_actions=available_names, completed_action=completed,
                self_context=self_context)
        usages = ([draft.usage] if draft is not None and generated is not draft else []) + [generated.usage]
        timings = [generated.timing]
        if generated.action_call is not None:
            if self._actions is None or completed is not None:
                raise RuntimeError("conversation model requested an unavailable action")
            try:
                completed = self._actions.execute(turn, generated.action_call)
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
                    model_usage=ConversationModelUsage.aggregate(tuple(usages)),
                    model_timing=ConversationModelTiming.aggregate(tuple(timings)),
                )
            generated = self._model.generate(
                turn,
                self._identity,
                memory,
                recent_messages,
                available_actions=(),
                completed_action=completed,
                self_context=self_context,
            )
            usages.append(generated.usage)
            timings.append(generated.timing)
        if generated.action_call is not None:
            raise RuntimeError("conversation model requested more than one action")
        text = "" if generated.text is None else generated.text.strip()
        if not text:
            raise RuntimeError("conversation model returned an empty reply")
        if len(text.split()) > self._identity.maximum_reply_words:
            raise RuntimeError("conversation model exceeded the reply word limit")
        return GroundedReply(
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            text=text,
            evidence_memory_ids=tuple(item.fact_id for item in memory.recall.selected),
            generated_at=turn.occurred_at,
            provider=generated.provider,
            model_usage=ConversationModelUsage.aggregate(tuple(usages)),
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
    "MemorySearchPlan",
    "RecentConversationMessage",
    "load_current_identity_pack",
    "publish_conversation_text",
    "stream_conversation_text",
]
