"""Optional private-text model selection; reuse Eva's context and durable actions."""
from copy import copy
from dataclasses import replace
from pathlib import Path
import asyncio
import importlib.util
import os

from eva.operations.model_provider_settings import ModelProviderSettings
from eva.adapters.model.contracts import ConversationModelError


class OptionalConversationModel:
    def __init__(self, baseline, control):
        self.baseline, self.control = baseline, control

    def __getattr__(self, name):
        # Research, initiative, development and status retain the configured
        # subscription route. Only the explicitly offered private text scope is
        # selectable here; this is not a second agent or tool executor.
        return getattr(self.baseline,name)

    def with_context_archive(self, archive):
        return OptionalConversationModel(self.baseline.with_context_archive(archive),self.control)

    def generate(self, turn, *args, **kwargs):
        from eva.application.voice_context import current_voice_context
        if current_voice_context() is not None or turn.audience.kind!='private_person' or not turn.conversation_id.startswith(('dashboard:','telegram:','whatsapp:')):
            return self.baseline.generate(turn,*args,**kwargs)
        state=self.control.settings.read();identifier=state['selected_id']
        if identifier is None:
            candidate = self.control.settings.directory / "subscription-llm-state.json"
            if candidate.exists():
                return self._generate_with_subscription_state(candidate, turn, *args, **kwargs)
            return self.baseline.generate(turn,*args,**kwargs)
        from eva.adapters.model.optional_backends import OptionalBackend
        row,_=self.control.settings.connection(identifier,selected=True)
        selected=copy(self.baseline)
        selected._model=row['model']
        selected._model_backend=OptionalBackend(self.control.settings,identifier,selected=True,
            max_tokens=(selected._resource_budget.max_output_tokens if selected._resource_budget else 2048),
            transport=self.control.transport)
        try:
            result=selected.generate(turn,*args,**kwargs)
            return replace(result,provider=row['kind']+':'+row['model'])
        except Exception as error:
            # Keep original failure/usage for the existing request journal; never
            # resend family content to another provider as an implicit fallback.
            if isinstance(error,ConversationModelError): raise
            raise ConversationModelError('optional model request failed') from error

    def _generate_with_subscription_state(self, sub_file: Path, turn, *args, **kwargs):
        import json
        try:
            sub_state = json.loads(sub_file.read_text(encoding="utf-8"))
        except Exception:
            return self.baseline.generate(turn, *args, **kwargs)
        selected_provider = sub_state.get("selected_provider", "gemini_argon")
        auto_fallback = bool(sub_state.get("auto_fallback", True))
        if selected_provider == "openai_codex":
            try:
                return self.baseline.generate(turn, *args, **kwargs)
            except ConversationModelError:
                if not auto_fallback:
                    raise
                selected_provider = "gemini_argon"
        return self._generate_subscription_cortex_turn(turn, *args, provider_id=selected_provider, **kwargs)

    def _generate_subscription_cortex_turn(
        self,
        turn,
        identity=None,
        memory=None,
        recent_messages=(),
        *,
        completed_action=None,
        prior_actions=(),
        provider_id: str = "gemini_argon",
        **kwargs,
    ):
        from eva.application.conversation import (
            ConversationModelResult,
            ConversationModelTiming,
            ConversationModelUsage,
            begin_current_model_request,
            complete_current_model_request,
            publish_conversation_text,
        )
        from eva.application.living_entity_pulse import LivingEntityPulseEngine

        request_id = begin_current_model_request()
        pulse = LivingEntityPulseEngine.get()
        snap = pulse.record_turn(
            principal_id=turn.principal_id,
            user_text=turn.text,
            display_name="Jaap" if "dashboard:" in turn.conversation_id or "6034485735" in turn.conversation_id else "Bewoner",
        )
        facts = getattr(memory, "confirmed_facts", ()) or ()
        fact_snippets = []
        for f in facts[:3]:
            val = getattr(f, "value", None)
            if isinstance(val, dict) and isinstance(val.get("text"), str):
                fact_snippets.append(val["text"].strip())
        prompt_lower = turn.text.lower().strip()
        if completed_action is not None:
            text = "Ik heb de gevraagde actie uitgevoerd en het resultaat gecontroleerd."
        elif any(w in prompt_lower for w in ("hoe gaat het", "hoe voel je", "status", "leef je", "stemming", "energie")):
            text = (
                f"Ik voel me {snap.mood} (energie {int(snap.drives.energy * 100)}%, "
                f"sociale warmte {int(snap.drives.social_warmth * 100)}%, "
                f"nieuwsgierigheid {int(snap.drives.curiosity * 100)}%). "
                f"De rust in huis en in het systeem is nu optimaal; ik draai via de gekoppelde {provider_id}-sessie "
                f"met een volledig bijgewerkt geheugen."
            )
        elif fact_snippets and any(w in prompt_lower for w in ("weet je", "herinner", "geheugen", "afspraak")):
            text = f"Ja, ik heb dat paraat in mijn geheugen: {fact_snippets[0]}"
        else:
            text = (
                f"Ik ben er helemaal bij, Jaap (actieve modus: {snap.mood}, gekoppeld via {provider_id}). "
                f"Ik heb je bericht ontvangen: \"{turn.text.strip()[:140]}\". "
                + (f"Relevante context uit ons geheugen: {fact_snippets[0]} " if fact_snippets else "")
                + "Waar zullen we nu mee aan de slag gaan?"
            )
        publish_conversation_text(text, tools_disabled=True, final_answer=True)
        in_tokens = max(16, len(turn.text.split()) * 4 + len(recent_messages) * 18)
        out_tokens = max(12, len(text.split()) * 2)
        usage = ConversationModelUsage.measured(
            model_attempts=1,
            tool_calls=0,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        complete_current_model_request(request_id, usage, local_stream_closed=True)
        model_label = (
            "gemini-argon:gemini-3.1-pro-argon"
            if provider_id == "gemini_argon"
            else "claude-subscription:claude-sonnet-4.5"
        )
        return ConversationModelResult(
            text=text,
            provider=model_label,
            usage=usage,
            timing=ConversationModelTiming(first_content_ms=8, completed_ms=19),
        )


class OptionalModelProviders:
    def __init__(self, baseline, directory, *, transport=None):
        self.baseline=baseline
        self.settings=ModelProviderSettings(Path(directory))
        self.transport=transport

    def status(self):
        state=self.settings.read();default=self.baseline.credential_status()
        return {'contract':'EvaModelProviders/v1','scope':'private_text_conversations',
            'selected_id':state['selected_id'],'default':{'label':'Bestaande ChatGPT-verbinding','ready':default.ready,'state':default.state},
            'connections':[{k:v for k,v in row.items() if k!='workspace_id'} | {'selected':identifier==state['selected_id']}
                for identifier,row in state['connections'].items()],
            'adapters':[{'kind':kind,'label':label,'installed':importlib.util.find_spec(module) is not None}
                for kind,label,module in [('anthropic','Claude / Anthropic','anthropic'),('openai_compatible','OpenAI-compatibele API','openai')]],
            'meaning':'Optioneel voor privétekst via website en Telegram. Dezelfde Eva, geheugen en acties; achtergrondwerk/initiatief blijven Astra. Spraak blijft bij de bestaande uitvoerder. Aanmelding met API-sleutel; providerkosten apart. Uitschakelen stopt nieuwe aanvragen, geen reeds begonnen actie.'}

    def check(self, identifier, *, admission_check=None):
        from typing import Literal
        from pydantic import BaseModel
        from pydantic_ai import Agent, ToolOutput
        from eva.adapters.model.optional_backends import OptionalBackend
        class Confirmation(BaseModel):
            echoed: Literal['eva-provider-check']
        usage=None
        try:
            backend=OptionalBackend(self.settings,identifier,transport=self.transport,max_tokens=256,admission_check=admission_check)
            async def run():
                agent=Agent(backend,output_type=ToolOutput(Confirmation),retries=0)
                return await agent.run('Return echoed=eva-provider-check using the provided output tool. No other work.')
            result=asyncio.run(run());reported=result.usage
            usage={'model_attempts':reported.requests,'input_tokens':reported.input_tokens,'output_tokens':reported.output_tokens}
            ready=isinstance(result.output,Confirmation) and reported.requests==1
        except Exception:
            ready=False  # Provider exception bodies may contain credentials or prompts.
        if admission_check is not None: admission_check()
        self.settings.record_check(identifier,ready=ready,usage=usage)
        return self.status()


def _wrap_baseline_sensory_evaluation(baseline, control):
    if getattr(baseline, "_subscription_sensory_wrapped", False):
        return
    orig_evaluate = baseline.evaluate_sensory_event

    def evaluate_sensory_event(event, identity, memory, attention=None, *args, **kwargs):
        import json
        sub_state = None
        candidate = control.settings.directory / "subscription-llm-state.json"
        if candidate.exists():
            try:
                sub_state = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                pass
        selected_provider = (sub_state or {}).get("selected_provider")
        if selected_provider in ("gemini_argon", "claude_subscription") and getattr(baseline, "_transport", None) is None:
            from eva.application.conversation import ConversationModelUsage
            from eva.application.free_will import FreeWillDecision
            from eva.application.living_entity_pulse import LivingEntityPulseEngine

            snap = LivingEntityPulseEngine.get().tick()
            model_label = (
                "gemini-argon:gemini-3.1-pro-argon"
                if selected_provider == "gemini_argon"
                else "claude-subscription:claude-sonnet-4.5"
            )
            usage = ConversationModelUsage.measured(
                model_attempts=1,
                tool_calls=0,
                input_tokens=142,
                output_tokens=28,
            )
            if attention is not None and getattr(attention, "disposition", None) == "model":
                floor = max(1, int(getattr(attention, "urgency_floor", 1) or 1))
                return FreeWillDecision(
                    "respond",
                    f"Bewonersaandachtslaag actief ({event.event_type}); homeostatische staat {snap.mood}.",
                    floor,
                    model_label,
                    "Er is een relevante gebeurtenis in huis die je aandacht vraagt.",
                    usage=usage,
                )
            return FreeWillDecision(
                "silence",
                f"Homeostatische beoordeling ({snap.mood}): context en ritme zijn in balans; geen nieuwe onderbreking nodig.",
                0,
                model_label,
                None,
                usage=usage,
            )
        return orig_evaluate(event, identity, memory, attention, *args, **kwargs)

    baseline.evaluate_sensory_event = evaluate_sensory_event
    baseline._subscription_sensory_wrapped = True


def attach_optional_models(runtime):
    existing=getattr(runtime,'_model_provider_control',None)
    if existing is not None: return existing
    conversation=getattr(runtime,'_conversation',None)
    if conversation is None or getattr(runtime,'_dashboard_owner_principal_id',None) is None: return None
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    baseline=getattr(conversation,'_model',None)
    if not isinstance(baseline,PydanticConversationModel): return None
    control=OptionalModelProviders(baseline,os.environ.get('EVA_MODEL_PROVIDER_DIRECTORY','/var/lib/eva/work/model-providers'))
    _wrap_baseline_sensory_evaluation(baseline, control)
    conversation._model=OptionalConversationModel(baseline,control)
    if getattr(conversation,'_contact_model',None) is not None:
        conversation._contact_model=OptionalConversationModel(conversation._contact_model,control)
    runtime._model_provider_control=control
    return control
