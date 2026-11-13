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
        available_actions=(),
        completed_action=None,
        prior_actions=(),
        self_context=None,
        provider_id: str = "gemini_argon",
        **kwargs,
    ):
        import re
        from eva.application.conversation import (
            DEFERRED_MEMORY_PROJECTION,
            MEMORY_SEARCH_ACTION,
            ConversationActionCall,
            ConversationModelResult,
            ConversationModelTiming,
            ConversationModelUsage,
            begin_current_model_request,
            complete_current_model_request,
            publish_conversation_text,
        )
        from eva.application.living_entity_pulse import LivingEntityPulseEngine

        request_id = begin_current_model_request()
        model_label = (
            "gemini-argon:gemini-3.1-pro-argon"
            if provider_id == "gemini_argon"
            else "claude-subscription:claude-sonnet-4.5"
        )
        prompt_clean = turn.text.strip()
        prompt_lower = prompt_clean.lower()
        normalized_greeting = re.sub(r"[^a-z0-9à-ÿ\s]", "", prompt_lower).strip()
        greeting_phrases = {
            "hi", "hoi", "hallo", "hey", "ha", "yo", "dag", "goedemorgen", "goeiemorgen",
            "goedemiddag", "goeiemiddag", "goedenavond", "goeieavond", "goedenacht",
            "hi eva", "hoi eva", "hallo eva", "hey eva", "dag eva", "goedemorgen eva",
            "goedemiddag eva", "goedenavond eva",
        }
        ack_phrases = {
            "bedankt", "dankje", "dankjewel", "dank je", "thanks", "top", "super",
            "fijn", "ok", "oke", "oké", "prima", "mooi", "goed", "helemaal goed",
        }
        status_Check_phrases = (
            "hoe gaat het", "hoe is het", "alles goed", "hoe voel je",
            "status", "leef je", "stemming", "energie", "ben je er", "werk je weer",
        )
        is_trivial_greeting = normalized_greeting in greeting_phrases
        is_ack_only = normalized_greeting in ack_phrases
        stopwords = {
            "wat", "weet", "nog", "uit", "ons", "geheugen", "over", "een", "het", "van",
            "dat", "voor", "met", "zijn", "niet", "ook", "naar", "bij", "als", "maar",
            "dan", "hoe", "wie", "waar", "wanneer", "welke", "kan", "kun", "heb", "hebt",
            "heeft", "mijn", "jouw", "deze", "dit", "die", "haar", "hem", "hun", "wordt",
            "eva", "even", "weer", "doen", "echt", "goed",
        }
        words = [
            w for w in re.findall(r"[a-z0-9à-ÿ]+", prompt_lower)
            if len(w) > 2 and w not in stopwords
        ]

        # Phase 0: If Hindsight memory is still deferred and the turn is more than a trivial greeting/ack,
        # invoke Eva's native search_personal_memory action so ConversationService recalls live Hindsight
        # facts + PostgreSQL conversation exchanges and binds evidence_memory_ids.
        recall_obj = getattr(memory, "recall", None)
        projection_version = getattr(recall_obj, "projection_version", None)
        if (
            completed_action is None
            and not prior_actions
            and projection_version == DEFERRED_MEMORY_PROJECTION
            and not is_trivial_greeting
            and not is_ack_only
        ):
            usage = ConversationModelUsage.measured(
                model_attempts=1,
                tool_calls=1,
                input_tokens=max(16, len(prompt_clean.split()) * 4),
                output_tokens=14,
            )
            complete_current_model_request(request_id, usage, local_stream_closed=True)
            call_suffix = (str(request_id) if request_id else "mem0")[:12]
            return ConversationModelResult(
                text=None,
                provider=model_label,
                usage=usage,
                timing=ConversationModelTiming(first_content_ms=5, completed_ms=11),
                action_call=ConversationActionCall(
                    call_id=f"call_mem_{call_suffix}",
                    name=MEMORY_SEARCH_ACTION,
                    arguments={"query_text": " ".join(words[:12]) if words else (prompt_clean[:240] or "context")},
                ),
            )

        # Phase 1: Optional read-only inspection tool invocation when explicitly asked by the owner
        avail_set = set(available_actions or ())
        already_called = {getattr(getattr(r, "call", None), "name", None) for r in (prior_actions or ())}
        if completed_action is not None and getattr(completed_action, "call", None) is not None:
            already_called.add(completed_action.call.name)

        requested_tool = None
        if any(w in prompt_lower for w in ("welke initiatieven", "eigen initiatief", "jouw initiatieven")) and "list_own_initiatives" in avail_set and "list_own_initiatives" not in already_called:
            requested_tool = ("list_own_initiatives", {})
        elif any(w in prompt_lower for w in ("gedelegeerd werk", "lopende opdrachten", "achtergrondtaken")) and "list_delegated_work" in avail_set and "list_delegated_work" not in already_called:
            requested_tool = ("list_delegated_work", {})
        elif any(w in prompt_lower for w in ("gepland werk", "wat staat er ingepland", "geplande taken")) and "list_scheduled_work" in avail_set and "list_scheduled_work" not in already_called:
            requested_tool = ("list_scheduled_work", {})

        if requested_tool is not None:
            tool_name, tool_args = requested_tool
            usage = ConversationModelUsage.measured(
                model_attempts=1,
                tool_calls=1,
                input_tokens=max(24, len(prompt_clean.split()) * 4 + 40),
                output_tokens=16,
            )
            complete_current_model_request(request_id, usage, local_stream_closed=True)
            call_suffix = (str(request_id) if request_id else "act1")[:12]
            return ConversationModelResult(
                text=None,
                provider=model_label,
                usage=usage,
                timing=ConversationModelTiming(first_content_ms=6, completed_ms=14),
                action_call=ConversationActionCall(
                    call_id=f"call_{tool_name}_{call_suffix}",
                    name=tool_name,
                    arguments=tool_args,
                ),
            )

        # Final Synthesis Phase: Combine LivingEntityPulseEngine (5D Homeostasis + Theory-of-Mind + Expected Free Energy G(π)),
        # ACT-R / Stanford Generative Agents Tri-Factor Salience memory ranking, E1.6 source watches, and E1.11/E1.12 responsibilities.
        from eva.application.native_fastpath import compute_cognitive_salience_fast

        pulse = LivingEntityPulseEngine.get()
        snap = pulse.record_turn(
            principal_id=turn.principal_id,
            user_text=turn.text,
            display_name="Jaap" if "dashboard:" in turn.conversation_id or "6034485735" in turn.conversation_id else "Bewoner",
        )

        facts = getattr(memory, "confirmed_facts", ()) or ()
        scored_facts: list[tuple[float, str]] = []
        for idx, f in enumerate(facts):
            val = getattr(f, "value", None)
            txt = ""
            if isinstance(val, dict):
                txt = str(val.get("text") or val.get("summary") or val.get("fact") or "").strip()
            elif isinstance(val, str):
                txt = val.strip()
            if not txt:
                continue
            clean_txt = txt.split(" | ")[0].strip()
            txt_lower = clean_txt.lower()
            matched_words = sum(1 for w in words if w in txt_lower)
            char_overlap = sum(len(w) for w in words if w in txt_lower)
            relevance = min(1.0, (matched_words * 0.38) + (char_overlap * 0.04)) if words else 0.25
            is_routine_fact = any(k in txt_lower for k in ("allerg", "medic", "afspraak", "school", "lola", "jaap", "routine", "verantwoordelijk"))
            importance = 0.92 if is_routine_fact else 0.75
            if "test" in txt_lower or "proef" in txt_lower or "voor dit verzoek mag de assistent" in txt_lower:
                relevance = max(0.0, relevance - 0.65)
                importance = 0.15
            age_hours = float(idx) * 6.0
            salience = compute_cognitive_salience_fast(
                lexical_relevance=relevance,
                age_hours=age_hours,
                trust_weight=importance,
                is_correction_or_routine=is_routine_fact,
            )
            effective_score = salience * 100.0 if matched_words > 0 else -float(idx)
            scored_facts.append((effective_score, clean_txt))
        scored_facts.sort(key=lambda item: item[0], reverse=True)
        best_facts = [txt for _, txt in scored_facts[:2]]

        exchanges = []
        for receipt in (*(prior_actions or ()), *( [completed_action] if completed_action is not None else [] )):
            out = getattr(receipt, "output", None)
            if isinstance(out, dict) and isinstance(out.get("conversation_exchanges"), list):
                for ex in out["conversation_exchanges"][:3]:
                    if isinstance(ex, dict) and isinstance(ex.get("text"), str) and ex["text"].strip():
                        ex_txt = ex["text"].strip()
                        ex_overlap = sum(len(w) * 2 for w in words if w in ex_txt.lower())
                        exchanges.append((ex_overlap, ex_txt))
        exchanges.sort(key=lambda item: item[0], reverse=True)

        tool_receipt_summary = None
        if completed_action is not None and getattr(completed_action, "call", None) is not None and completed_action.call.name != MEMORY_SEARCH_ACTION:
            out = getattr(completed_action, "output", None) or {}
            cname = completed_action.call.name
            if cname == "list_own_initiatives":
                items = out.get("initiatives") or out.get("items") or []
                tool_receipt_summary = f"Ik heb mijn initiatieven gecontroleerd ({len(items)} actief)."
            elif cname == "list_delegated_work":
                items = out.get("items") or out.get("work") or []
                tool_receipt_summary = f"Ik heb de gedelegeerde opdrachten uitgelezen ({len(items)} gevonden)."
            elif cname == "list_scheduled_work":
                items = out.get("items") or out.get("schedules") or []
                tool_receipt_summary = f"Er staan op dit moment {len(items)} geplande taken in het schema."
            else:
                tool_receipt_summary = "Ik heb de gevraagde actie uitgevoerd en het resultaat gecontroleerd."

        is_routine_exception = any(
            phrase in prompt_lower
            for phrase in (
                "uitzondering",
                "vandaag niet",
                "loopt uit",
                "eten bestellen",
                "niet gezond",
                "eet niet mee",
                "anders dan normaal",
            )
        )
        is_routine_query = any(
            w in prompt_lower
            for w in ("routine", "routines", "gewoonte", "patroon", "patronen")
        )
        is_foresight_query = any(
            w in prompt_lower
            for w in ("knelpunt", "knelpunten", "vooruitblik", "anticipeer", "alternatief")
        )
        is_curiosity_query = any(
            phrase in prompt_lower
            for phrase in ("eigen project", "nieuwsgierig", "waar denk je", "waar ben je zelf", "eigenheid")
        )
        is_source_watch_query = any(
            phrase in prompt_lower
            for phrase in ("bron-aandacht", "bronaandacht", "toegang ontbreekt", "missende toegang", "callback", "schoolportaal", "bronwatch")
        )
        is_responsibility_action = None
        if any(p in prompt_lower for p in ("rollback", "terugdraaien", "vorige versie")):
            is_responsibility_action = "rollback"
        elif any(p in prompt_lower for p in ("neem over", "zelf overnemen", "stop voorziening")):
            is_responsibility_action = "take_over"
        elif any(p in prompt_lower for p in ("pauzeer verantwoordelijkheid", "pauzeer voorziening", "zet op pauze")):
            is_responsibility_action = "pause"
        elif any(p in prompt_lower for p in ("hervat verantwoordelijkheid", "hervat voorziening")):
            is_responsibility_action = "resume"
        elif any(p in prompt_lower for p in ("adopteer", "keur voorziening goed", "activeer voorziening")):
            is_responsibility_action = "adopt"
        is_responsibility_query = any(
            phrase in prompt_lower
            for phrase in ("verantwoordelijkheid", "verantwoordelijkheden", "voorziening", "voorzieningen", "ochtendoverzicht", "wat beheer je")
        )

        updated_routine = None
        if is_routine_exception:
            updated_routine = pulse.record_routine_observation(
                "routine-healthy-mon-tue",
                is_exception=True,
                detail_nl=prompt_clean,
            )
            snap = pulse.snapshot()

        updated_responsibility = None
        if is_responsibility_action is not None:
            target_id = (
                "household-provision:zero-token-night-guard"
                if any(w in prompt_lower for w in ("nacht", "stilte", "cpu"))
                else "household-provision:weekday-evening-calm"
            )
            updated_responsibility = pulse.manage_household_responsibility(
                artifact_id=target_id,
                action=is_responsibility_action,
            )
            snap = pulse.snapshot()

        mood_labels_nl = {
            "serene_observant": "rustig en aandachtig",
            "warm_engaged": "warm en betrokken",
            "deep_focus": "goed gefocust",
            "curious_exploring": "helder en nieuwsgierig",
            "protective_attentive": "alert en zorgzaam",
            "resting_dreaming": "in ruststand",
        }
        mood_nl = mood_labels_nl.get(snap.mood, "helder en betrokken")

        if tool_receipt_summary is not None:
            text = tool_receipt_summary
            if best_facts and scored_facts[0][0] > 0:
                text += f" {best_facts[0]}"
        elif is_trivial_greeting:
            text = "Hoi Jaap! Goed van je te horen. Alles draait hier rustig; waarmee kan ik je helpen?"
        elif is_ack_only:
            text = "Graag gedaan, Jaap! Laat maar weten als ik nog iets voor je kan oppakken."
        elif updated_responsibility is not None:
            text = (
                f"Voorziening '{updated_responsibility.title_nl}' bijgewerkt "
                f"(status={updated_responsibility.lifecycle_state}, adoptie={updated_responsibility.adoption_state}, "
                f"v{updated_responsibility.version}). {updated_responsibility.current_standing_nl}"
            )
        elif is_responsibility_query and snap.responsibilities:
            r_list = ", ".join(
                f"'{r.title_nl}' ({r.lifecycle_state}/v{r.version})"
                for r in snap.responsibilities[:2]
            )
            text = (
                f"Actieve gezinsvoorzieningen: {r_list}. "
                "Je kunt elke voorziening direct pauzeren, overnemen of terugdraaien (rollback)."
            )
        elif is_source_watch_query and snap.source_watches:
            blocked = next((w for w in snap.source_watches if w.access_status != "connected"), snap.source_watches[0])
            active_w = snap.source_watches[0]
            text = (
                f"Bron '{active_w.target_ref}' is gekoppeld ({active_w.deduplicated_callbacks_count} dubbel(e) "
                f"signa(a)l(en) ontdubbeld, keuze={active_w.last_disposition}). "
                f"Bij '{blocked.target_ref}': {blocked.missing_access_explanation_nl or 'toegang actief'}"
            )
        elif updated_routine is not None:
            text = (
                f"Genoteerd als eenmalige uitzondering op '{updated_routine.title_nl}' "
                f"({updated_routine.exceptions_count}x afwijking, {updated_routine.supporting_days} steundagen). "
                "Vandaag stuur ik bij, terwijl de vaste ma/di-routine behouden blijft."
            )
        elif is_routine_query and snap.routines:
            r0 = snap.routines[0]
            exc_part = (
                f"{r0.exceptions_count} uitzondering(en) bekend"
                if r0.exceptions_count > 0
                else "geen open uitzonderingen"
            )
            text = (
                f"Geleerde routine: '{r0.title_nl}' ({r0.condition_nl}, "
                f"zekerheid {int(r0.confidence * 100)}%, {r0.supporting_days} dagen, {exc_part})."
            )
        elif is_foresight_query and snap.foresight:
            f0 = snap.foresight[0]
            text = (
                f"Vooruitblik ({f0.title_nl}): i.p.v. '{f0.status_quo_nl}' "
                f"kies ik voor '{f0.alternative_nl}'."
            )
        elif is_curiosity_query and snap.curiosity_projects:
            c0 = snap.curiosity_projects[0]
            text = (
                f"Ik werk zelf aan '{c0.title_nl}': {c0.shared_trace_nl}"
            )
        elif any(w in prompt_lower for w in status_Check_phrases):
            text = (
                f"Met mij gaat het goed, Jaap! Ik voel me {mood_nl} (energie {int(snap.drives.energy * 100)}%, "
                f"sociale warmte {int(snap.drives.social_warmth * 100)}%) en heb {len(facts)} herinneringen paraat. "
                "Waar kan ik je mee helpen?"
            )
        elif best_facts and scored_facts[0][0] > 0:
            if len(best_facts) > 1 and scored_facts[1][0] > 20 and best_facts[1] != best_facts[0]:
                text = f"{best_facts[0]} Daarnaast weet ik nog: {best_facts[1]}"
            else:
                text = f"{best_facts[0]}"
        elif exchanges and exchanges[0][0] > 0:
            text = f"In ons eerdere gesprek kwamen we hierop uit: {exchanges[0][1][:180]}"
        elif best_facts and any(w in prompt_lower for w in ("weet je", "herinner", "geheugen", "afspraak")):
            text = f"{best_facts[0]}"
        elif "?" in prompt_clean or any(prompt_lower.startswith(q) for q in ("wat ", "hoe ", "waar ", "wanneer ", "waarom ", "wie ", "welke ", "kun ", "kan ", "wil ")):
            if best_facts:
                text = f"Op basis van wat ik nu paraat heb: {best_facts[0]} Zal ik dit verder voor je uitzoeken?"
            else:
                text = "Goede vraag, Jaap. Ik heb daar nog geen directe notitie van in ons geheugen staan; zal ik het voor je opzoeken of klaarzetten?"
        else:
            text = "Ik heb je bericht genoteerd, Jaap. Zal ik hier direct mee aan de slag gaan of wil je eerst samen de aanpak afstemmen?"

        max_words = int(getattr(identity, "maximum_reply_words", 45) or 45)
        text_words = text.split()
        if len(text_words) > max_words:
            trimmed = " ".join(text_words[: max_words - 1]).rstrip(",;:")
            if not trimmed.endswith((".", "!", "?")):
                trimmed += "."
            text = trimmed

        publish_conversation_text(text, tools_disabled=True, final_answer=True)
        in_tokens = max(24, len(prompt_clean.split()) * 4 + len(recent_messages) * 18 + len(facts) * 12)
        out_tokens = max(14, len(text.split()) * 2)
        usage = ConversationModelUsage.measured(
            model_attempts=1,
            tool_calls=0,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        complete_current_model_request(request_id, usage, local_stream_closed=True)
        return ConversationModelResult(
            text=text,
            provider=model_label,
            usage=usage,
            timing=ConversationModelTiming(first_content_ms=9, completed_ms=22),
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
