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

SPEAK_AND_CONTINUE = "speak_and_continue"
SPEAK_DESCRIPTOR = ConversationToolDescriptor(
    name=SPEAK_AND_CONTINUE,
    description=("Geef in een live stemgesprek een kort, inhoudelijk eerste antwoorddeel "
                 "terwijl je daarna verder antwoordt of de echte tools gebruikt. Dit is "
                 "definitieve spraak, geen concept. Gebruik alleen al bekende inhoud of "
                 "een concrete bedoeling; beweer nooit dat een actie is uitgevoerd of "
                 "een resultaat is gevonden zonder een ontvangen bewijs. Geen vaste "
                 "opvulzin. Hoogstens eenmaal per beurt, maximaal 35 woorden. Voor een "
                 "kort volledig antwoord gebruik je gewoon het eindantwoord."),
    input_schema={"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False},
    completion_guidance=("Dit antwoorddeel is aan de spraakroute aangeboden, niet bewezen "
                         "gehoord. Ga door met de benodigde echte tools of geef het "
                         "inhoudelijke vervolg; herhaal het eerste deel niet."),
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

    def speak_and_continue(self, call: ConversationActionCall) -> ConversationActionReceipt:
        if set(call.arguments) != {"text"}:
            raise ValueError("invalid spoken response")
        text = call.arguments["text"]
        if not isinstance(text, str) or not text.strip() or len(text.split()) > 35 or len(text) > 1000:
            raise ValueError("spoken response exceeds its bound")
        text = text.strip()
        if text[-1] not in ".!?":
            text += "."
        self.event({"type": "spoken_update", "text": text})
        return ConversationActionReceipt(call=call, output={
            "status": "offered_to_voice", "text": text, "heard": False,
        })

    def instructions(self) -> tuple[str, ...]:
        return (
            "Je bent Eva. Spreek kort en natuurlijk Nederlands, zonder markdown of vaste "
            "opvulzinnen. Antwoord gewoonlijk in één tot drie zinnen; geef meer details als daarom "
            "wordt gevraagd. Gebruik voor je eigen activiteiten de toegestane gespreks-, "
            "geheugen- en werkcontext. Jij hebt die activiteiten uitgevoerd of besproken; "
            "spreek in de eerste persoon, niet alsof je een andere Eva moet raadplegen. "
            "Voor een overzicht van je werkzaamheden gebruik je eerst read_recent_activity wanneer "
            "aangeboden: dat bevat directe handelingen én de taaklijst. Gebruik anders de beschikbare "
            "taaklijst en relevante opgeslagen resultaten. De laatste gespreksberichten zijn geen "
            "volledig dagoverzicht. Onderscheid bespreken, plannen, uitvoeren en afronden, "
            "met de oorspronkelijke tijdstippen. Benoem alleen een concrete ontbrekende "
            "periode of bron, niet standaard dat je niet weet wat je hebt gedaan. "
            "Een korte denkpauze mag: geen vaste zoek- of wachtmededelingen en geen "
            "wedervraag puur om het gesprek gaande te houden. "
            "Gebruik speak_and_continue als een inhoudelijke tussenreactie helpt. "
            "Afspeelstatus beschrijft alleen aflevering: onbevestigd betekent niet "
            "onderbroken of gehoord. Een nieuwe vraag stopt geen lopend werk; gebruik "
            "stop_spoken_task bij een expliciete stop of onverenigbare doelcorrectie.",
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
