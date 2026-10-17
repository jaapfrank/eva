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

class VoicePreparationUnavailable(RuntimeError):
    """An optional preparation reaction produced no publishable speech."""


SPEAK_AND_CONTINUE = "speak_and_continue"
SPEAK_DESCRIPTOR = ConversationToolDescriptor(
    name=SPEAK_AND_CONTINUE,
    description=("Geef in een live stemgesprek een kort eerste antwoorddeel of een natuurlijke "
                 "denkreactie wanneer je nog iets moet afwegen of opzoeken. Formuleer die zelf "
                 "vanuit dit gesprek; wissel niet mechanisch een lijst wachtzinnen af. Dit is "
                 "definitieve spraak, geen concept. Gebruik alleen al bekende inhoud of "
                 "een concrete bedoeling; beweer nooit dat een actie is uitgevoerd of "
                 "een resultaat is gevonden zonder een ontvangen bewijs. Geen vaste "
                 "opvulzin of onnodige aankondiging bij een direct antwoord. Hoogstens eenmaal "
                 "per beurt, maximaal 35 woorden. Voor een "
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
            "Beantwoord een vervolgvraag direct als de al beschikbare broninhoud die dekt. "
            "Voor ontbrekende activiteitgegevens gebruik je read_recent_activity wanneer "
            "aangeboden: dat bevat directe handelingen én de taaklijst. Gebruik anders de beschikbare "
            "taaklijst en relevante opgeslagen resultaten. De laatste gespreksberichten zijn geen "
            "volledig dagoverzicht. Onderscheid bespreken, plannen, uitvoeren en afronden, "
            "met de oorspronkelijke tijdstippen. Lees ontbrekende daggegevens eerst; "
            "vertel daarna wat je weet en wat je eventueel niet meer weet. Leg het ophalen "
            "van herinneringen niet uit alsof je gegevens van iemand anders raadpleegt. "
            "Vertel wat je deed, ontdekte en wat nog loopt; vertaal technische bronlabels naar "
            "gewone taal zonder onzekerheid weg te laten. Verzin geen belevenissen of gevoelens. "
            "Sluit aan op wat de ander net vertelde; stel alleen een vervolgvraag als het antwoord "
            "je helpt die persoon of diens bedoeling te begrijpen. Als je iets niet weet of "
            "niet meer terugvindt, zeg dat eenvoudig en concreet; gebruik geen formele "
            "betrouwbaarheidsverklaring. Een korte denkpauze mag. Voorkom een lange stilte: "
            "als je antwoord nog een opzoekstap of verdere afweging nodig heeft, geef via "
            "speak_and_continue eerst een kort eerste antwoorddeel of een kleine natuurlijke "
            "denkreactie. Formuleer die vanuit deze vraag en de "
            "recente reacties; verval niet in dezelfde openingszin, een vaste afwisseling of "
            "een technische voortgangsmelding. Soms is één aarzelend woord genoeg, soms een "
            "korte gedachte. Bij een direct antwoord is geen tussenreactie nodig. "
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
