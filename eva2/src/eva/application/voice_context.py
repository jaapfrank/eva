"""Ephemeral connector context; all work still uses Eva's normal runtime."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator

from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationToolDescriptor,
    RecentConversationMessage,
)

STOP_VOICE_WORK = "stop_spoken_task"
STOP_DESCRIPTOR = ConversationToolDescriptor(
    name=STOP_VOICE_WORK,
    description=("Stop een lopende opdracht in dit stemgesprek als de eigenaar dat vraagt, "
                 "of voordat je een onverenigbare correctie opnieuw uitvoert. Alleen spraak "
                 "onderbreken is geen taakstop. Kies een opgegeven message_id."),
    input_schema={"type": "object", "properties": {"message_id": {"type": "string"}},
                  "required": ["message_id"], "additionalProperties": False},
    completion_guidance=("Het stopverzoek is duurzaam vastgelegd; een reeds uitgevoerde "
                         "stap is niet teruggedraaid. Gebruik bij een correctie daarna "
                         "de gewone tools voor het aangepaste doel."),
)

@dataclass(frozen=True)
class VoiceContext:
    recent: tuple[RecentConversationMessage, ...]
    active_work: tuple[tuple[str, str], ...]
    stop: Callable[[str], None]
    event: Callable[[dict], None]
    known_results: tuple[tuple[str, str], ...] = ()

    def stop_work(self, call: ConversationActionCall) -> ConversationActionReceipt:
        if set(call.arguments) != {"message_id"}:
            raise ValueError("invalid voice control")
        target = call.arguments["message_id"]
        if target not in {key for key, _ in self.active_work}:
            raise ValueError("voice control target is not active in this session")
        self.stop(target)
        self.event({"type": "task_stop_requested", "message_id": target})
        return ConversationActionReceipt(call=call, output={
            "status": "verified", "message_id": target, "stop_requested": True,
            "already_dispatched_steps_may_finish": True,
        })

    def instructions(self) -> tuple[str, ...]:
        return (
            "Dit is een live stemgesprek in natuurlijk Nederlands. Antwoord kort en "
            "spreekbaar, zonder vaste opvulzinnen. De connectorcontext bevat alleen "
            "spraak die LiveKit als afgespeeld synchroniseerde; dit is geen bewijs van "
            "menselijk horen. Een onderbrekingsgrens kan geschat zijn. "
            "Gegenereerde maar niet afgespeelde tekst is niet gehoord. "
            "Andere opdrachten kunnen nog lopen: een nieuwe vraag annuleert die niet. "
            "Bij een expliciete taakstop of onverenigbare doelcorrectie gebruik je "
            "stop_spoken_task voor de betreffende opdracht; verzin geen stopbevestiging.",
            "Lopende opdrachten in deze sessie: " + repr(self.active_work),
            "Bekende taakresultaten (niet noodzakelijk uitgesproken of gehoord): " + repr(self.known_results),
        )

_CURRENT: ContextVar[VoiceContext | None] = ContextVar("eva_voice_context", default=None)

def current_voice_context() -> VoiceContext | None:
    return _CURRENT.get()

@contextmanager
def voice_context(context: VoiceContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)
