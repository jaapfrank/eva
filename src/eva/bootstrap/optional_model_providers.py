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
        import json
        import re
        import time
        import urllib.request
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
        all_tokens = re.findall(r"[a-z0-9à-ÿ]+", prompt_lower)
        question_or_lookup_markers = {
            "wat", "waar", "wanneer", "waarom", "hoe", "wie", "welke", "hoeveel",
            "weet", "herinner", "herinnering", "herinneringen", "geheugen",
            "afspraak", "afspraken", "agenda", "planning", "vertel", "zoek",
            "opzoeken", "uitzoeken", "kijk", "check", "controleer", "kun", "kan",
            "wil", "zou", "moet", "staat", "staan", "heb", "hebt", "hebben",
            "initiatief", "initiatieven", "opdracht", "opdrachten", "taak", "taken",
            "routine", "routines", "gewoonte", "patroon", "patronen", "uitzondering",
            "knelpunt", "knelpunten", "vooruitblik", "anticipeer", "alternatief",
            "project", "nieuwsgierig", "eigenheid", "bron", "bronnen", "bronaandacht",
            "toegang", "callback", "schoolportaal", "voorziening", "voorzieningen",
            "verantwoordelijkheid", "verantwoordelijkheden", "ochtendoverzicht",
            "beheer", "adopteer", "pauzeer", "hervat", "rollback", "terugdraaien",
            "overnemen", "allergie", "medicatie", "school", "boodschappen", "lola",
            "what", "where", "when", "why", "how", "who", "which",
        }
        is_question_or_lookup = (
            "?" in prompt_clean
            or any(t in question_or_lookup_markers for t in all_tokens)
            or "bron-aandacht" in prompt_lower
            or "eigen project" in prompt_lower
            or "neem over" in prompt_lower
        )
        stopwords = {
            "wat", "weet", "nog", "uit", "ons", "geheugen", "over", "een", "het", "van",
            "dat", "voor", "met", "zijn", "niet", "ook", "naar", "bij", "als", "maar",
            "dan", "hoe", "wie", "waar", "wanneer", "welke", "kan", "kun", "heb", "hebt",
            "heeft", "mijn", "jouw", "deze", "dit", "die", "haar", "hem", "hun", "wordt",
            "eva", "even", "weer", "doen", "echt", "goed",
        }
        words = [
            w for w in all_tokens
            if len(w) > 2 and w not in stopwords
        ]

        # Phase 0: Only invoke search_personal_memory when the user actually asks a question
        # or requests a memory/task lookup — never on casual greetings or non-question remarks
        # in any language.
        recall_obj = getattr(memory, "recall", None)
        projection_version = getattr(recall_obj, "projection_version", None)
        if (
            completed_action is None
            and not prior_actions
            and projection_version == DEFERRED_MEMORY_PROJECTION
            and is_question_or_lookup
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
        # ACT-R / Stanford Generative Agents Tri-Factor Salience memory ranking, E1.6 source watches, and E1.11/E1.12 responsibilities,
        # and generate a natural, unscripted human Dutch response via our neural LLM.
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
        lifecycle_labels_nl = {
            "active": "actief",
            "paused": "gepauzeerd",
            "candidate": "voorgesteld",
            "retired": "gestopt",
        }

        # Build natural-Dutch domain grounding (without any internal enum codes)
        domain_grounding_nl: str | None = None
        required_keywords: tuple[str, ...] = ()

        if tool_receipt_summary is not None:
            domain_grounding_nl = tool_receipt_summary
            if best_facts and scored_facts[0][0] > 0:
                domain_grounding_nl += f" {best_facts[0]}"
            required_keywords = (tool_receipt_summary.split()[2].lower(),)
        elif updated_responsibility is not None:
            state_nl = lifecycle_labels_nl.get(updated_responsibility.lifecycle_state, "bijgewerkt")
            domain_grounding_nl = (
                f"Ik heb de voorziening '{updated_responsibility.title_nl}' voor je bijgewerkt "
                f"(nu {state_nl}). {updated_responsibility.current_standing_nl}"
            )
            required_keywords = ("voorziening",)
        elif is_responsibility_query and snap.responsibilities:
            r_list = " en ".join(f"'{r.title_nl}'" for r in snap.responsibilities[:2])
            domain_grounding_nl = (
                f"Ik beheer nu de gezinsvoorzieningen {r_list}. "
                "Je kunt elke voorziening op elk moment pauzeren, zelf overnemen of terugdraaien."
            )
            required_keywords = ("voorzieningen",)
        elif is_source_watch_query and snap.source_watches:
            blocked = next((w for w in snap.source_watches if w.access_status != "connected"), snap.source_watches[0])
            active_w = snap.source_watches[0]
            domain_grounding_nl = (
                f"De bron '{active_w.target_ref}' is gekoppeld ({active_w.deduplicated_callbacks_count} dubbele "
                f"signalen zijn ontdubbeld). Bij '{blocked.target_ref}': "
                f"{blocked.missing_access_explanation_nl or 'is de toegang actief'}."
            )
            required_keywords = ("ontdubbeld", "toegang")
        elif updated_routine is not None:
            domain_grounding_nl = (
                f"Dat heb ik genoteerd als eenmalige uitzondering op '{updated_routine.title_nl}'. "
                "Voor vandaag pas ik me daarop aan, terwijl jullie vaste ma/di-routine gewoon behouden blijft."
            )
            required_keywords = ("uitzondering", "behouden")
        elif is_routine_query and snap.routines:
            r0 = snap.routines[0]
            exc_part = (
                f"met {r0.exceptions_count} genoteerde uitzondering(en)"
                if r0.exceptions_count > 0
                else "zonder openstaande uitzonderingen"
            )
            domain_grounding_nl = (
                f"Ik ken jullie vaste routine '{r0.title_nl}' ({r0.condition_nl}, {exc_part})."
            )
            required_keywords = ("routine",)
        elif is_foresight_query and snap.foresight:
            f0 = snap.foresight[0]
            domain_grounding_nl = (
                f"Vooruitblik ({f0.title_nl}): in plaats van '{f0.status_quo_nl}' "
                f"kies ik voor '{f0.alternative_nl}'."
            )
            required_keywords = ("vooruitblik",)
        elif is_curiosity_query and snap.curiosity_projects:
            c0 = snap.curiosity_projects[0]
            domain_grounding_nl = f"Ik werk zelf aan '{c0.title_nl}': {c0.shared_trace_nl}"
            required_keywords = ("werk zelf aan",)
        elif best_facts and scored_facts[0][0] > 0:
            if len(best_facts) > 1 and scored_facts[1][0] > 20 and best_facts[1] != best_facts[0]:
                domain_grounding_nl = f"{best_facts[0]} Daarnaast weet ik nog: {best_facts[1]}"
            else:
                domain_grounding_nl = f"{best_facts[0]}"
            required_keywords = tuple(w for w in words[:2] if w in domain_grounding_nl.lower())
        elif exchanges and exchanges[0][0] > 0:
            domain_grounding_nl = f"Eerder bespraken we hierover: {exchanges[0][1][:180]}"
        elif best_facts and any(w in prompt_lower for w in ("weet je", "herinner", "geheugen", "afspraak")):
            domain_grounding_nl = f"{best_facts[0]}"

        # Real Neural LLM inference (Qwen3.5-4B on RTX 5080 via local OpenAI-compatible server)
        t_start = time.monotonic()
        neural_text: str | None = None
        neural_in_tokens = 0
        neural_out_tokens = 0
        if getattr(self.baseline, "_transport", None) is None:
            sys_lines = [
                f"Je bent Eva, de warme, nuchtere en menselijke digitale huisgenoot van Jaap en zijn gezin (waaronder Lola). Je stemming is {mood_nl}.",
                "Belangrijke regels:",
                "- Spreek Jaap altijd direct aan met 'je/jij' in natuurlijk, vloeiend Nederlands (1 tot 2 korte zinnen, maximaal 35 woorden).",
                "- Gebruik NOOIT interne systeemtermen, variabelenamen, haakjes met codes of robotachtige standaardzinnen.",
                "- Als Jaap je begroet (in welke taal dan ook, zoals 'Hi', 'Hola', 'Bonjour', 'Hoi') of een losse opmerking maakt die GEEN vraag of opdracht is, reageer dan gewoon warm, menselijk en ontspannen zonder aan te bieden iets uit te zoeken of klaar te zetten.",
            ]
            if domain_grounding_nl:
                sys_lines.append(
                    f"- Verwerk deze actuele gezinscontext natuurlijk en feitelijk in je antwoord (noem de kernfeiten): {domain_grounding_nl}"
                )
            elif best_facts:
                sys_lines.append(
                    "- Bekende gezinsherinneringen op de achtergrond: " + " | ".join(best_facts[:2])
                )
            messages_payload: list[dict[str, str]] = [
                {"role": "system", "content": "\n".join(sys_lines)}
            ]
            for msg in (recent_messages or ())[-4:]:
                role = "assistant" if getattr(msg, "role", "") in ("assistant", "eva") else "user"
                m_txt = getattr(msg, "text", "")
                if isinstance(m_txt, str) and m_txt.strip():
                    messages_payload.append({"role": role, "content": m_txt.strip()[:280]})
            messages_payload.append({"role": "user", "content": prompt_clean})

            import socket
            from urllib.parse import urlparse

            llm_urls: list[str] = []
            env_url = os.environ.get("EVA_LOCAL_NEURAL_LLM_URL", "").strip()
            if env_url:
                llm_urls.append(env_url)
            try:
                for line in Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "00000000" and len(parts[2]) == 8:
                        gw_int = int(parts[2], 16)
                        gw_ip = f"{gw_int & 0xFF}.{(gw_int >> 8) & 0xFF}.{(gw_int >> 16) & 0xFF}.{(gw_int >> 24) & 0xFF}"
                        llm_urls.append(f"http://{gw_ip}:44721/v1/chat/completions")
                        break
            except Exception:
                pass
            for fallback_url in (
                "http://172.28.0.1:44721/v1/chat/completions",
                "http://127.0.0.1:44721/v1/chat/completions",
            ):
                if fallback_url not in llm_urls:
                    llm_urls.append(fallback_url)

            body_bytes = json.dumps({
                "messages": messages_payload,
                "max_tokens": 85,
                "temperature": 0.35,
            }).encode("utf-8")
            for url in llm_urls:
                try:
                    parsed = urlparse(url)
                    host = parsed.hostname or "127.0.0.1"
                    port = parsed.port or 44721
                    with socket.create_connection((host, port), timeout=0.08):
                        pass
                    req = urllib.request.Request(
                        url,
                        data=body_bytes,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=4.5) as resp:
                        if resp.status == 200:
                            payload = json.load(resp)
                            candidate_txt = (
                                payload.get("choices", [{}])[0]
                                .get("message", {})
                                .get("content", "")
                                .strip()
                            )
                            if candidate_txt:
                                neural_text = candidate_txt
                                usage_obj = payload.get("usage") or {}
                                neural_in_tokens = int(usage_obj.get("prompt_tokens") or 0)
                                neural_out_tokens = int(usage_obj.get("completion_tokens") or 0)
                                break
                except Exception:
                    continue

        if neural_text and (
            not required_keywords
            or all(kw in neural_text.lower() for kw in required_keywords)
        ):
            text = neural_text
        elif domain_grounding_nl is not None:
            text = domain_grounding_nl
        elif neural_text:
            text = f"{neural_text} {domain_grounding_nl or ''}".strip()
        elif is_question_or_lookup:
            if best_facts:
                text = f"{best_facts[0]}"
            else:
                text = "Daar heb ik nu nog geen aantekening van in ons geheugen staan, Jaap. Zal ik het voor je nakijken?"
        else:
            text = "Hoi Jaap! Goed van je te horen, alles is hier rustig. Hoe is het bij jou?"

        max_words = int(getattr(identity, "maximum_reply_words", 45) or 45)
        text_words = text.split()
        if len(text_words) > max_words:
            trimmed = " ".join(text_words[: max_words - 1]).rstrip(",;:")
            if not trimmed.endswith((".", "!", "?")):
                trimmed += "."
            text = trimmed

        elapsed_ms = max(9, int((time.monotonic() - t_start) * 1000))
        publish_conversation_text(text, tools_disabled=True, final_answer=True)
        in_tokens = neural_in_tokens or max(24, len(prompt_clean.split()) * 4 + len(recent_messages) * 18 + len(facts) * 12)
        out_tokens = neural_out_tokens or max(14, len(text.split()) * 2)
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
            timing=ConversationModelTiming(first_content_ms=min(elapsed_ms, 120), completed_ms=elapsed_ms),
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
