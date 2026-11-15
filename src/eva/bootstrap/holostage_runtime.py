"""Foreground Owner Stage & Cast V2 Conversation Tools for Eva HoloStage."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import re
from pathlib import Path
from typing import Any, Callable

from eva.adapters.connectors.holostage_speech import HoloStageSpeechBridge
from eva.adapters.model.holostage_vision import HoloStageVisionBridge
from eva.application.cast_v2_protocol import query_or_launch_approved_cast_device
from eva.application.conversation import (
    ConversationActionInputRejected,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationToolDescriptor,
)
from eva.application.holostage import (
    PRIVATE_STAGE_CARD_KINDS,
    VALID_STAGE_CARD_KINDS,
    HoloStageManager,
)

MANAGE_HOLOSTAGE = ConversationToolDescriptor(
    name="manage_holostage",
    description=(
        "Inspect, animate, or cast my 3D Eva HoloStage visual screen and Chromecast / Google TVs "
        "(such as woonkamer, DIW7022, overkapping, or schuur). ALWAYS use this foreground tool "
        "(NOT start_digital_work) whenever the owner asks to: "
        "(1) show or cast me on a Chromecast/TV ('cast_stage' — you can also pass title/body_nl/items "
        "and animation_effect in the SAME call to launch on TV AND show an animated card/leaderboard at once!), "
        "(2) conjure/reveal an animated holographic card or standings leaderboard on screen ('conjure_card': "
        "shopping_list, household_today, home_overview, visitors_review, or custom_spotlight with title/body_nl/items), "
        "(3) trigger a 3D stage animation & Eva gesture ('stage_show' with animation_effect), "
        "(4) hide/clear cards so only full-screen 3D Eva is shown ('dismiss_cards'), "
        "(5) add items to the HoloStage shopping list ('shopping_add'), or "
        "(6) speak a live message out loud on connected HoloStage TVs/screens ('speak_on_stage'). "
        "When asked to show live data (e.g. F1 standings or Eredivisie table) on Chromecast, first call "
        "search_public_info(answer_directly=false) and then immediately call manage_holostage(operation='cast_stage', "
        "animation_effect='cinema_spotlight', title=..., body_nl=..., items=[...]) in the same turn!"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "status",
                    "conjure_card",
                    "stage_show",
                    "dismiss_cards",
                    "shopping_add",
                    "cast_stage",
                    "speak_on_stage",
                ],
            },
            "card_kind": {
                "type": ["string", "null"],
                "enum": [
                    "shopping_list",
                    "household_today",
                    "home_overview",
                    "visitors_review",
                    "custom_spotlight",
                    "all",
                    None,
                ],
            },
            "animation_effect": {
                "type": ["string", "null"],
                "enum": [
                    "aurora_reveal",
                    "celebration",
                    "cinema_spotlight",
                    "pulse_energy",
                    "wave_greeting",
                    None,
                ],
            },
            "card_theme": {
                "type": ["string", "null"],
                "enum": ["spotlight", "leaderboard", "climate", "celebration", "aurora", None],
            },
            "target_device": {"type": ["string", "null"], "maxLength": 120},
            "approve_device": {"type": ["boolean", "null"]},
            "title": {"type": ["string", "null"], "maxLength": 96},
            "body_nl": {"type": ["string", "null"], "maxLength": 800},
            "items": {
                "type": ["array", "null"],
                "items": {"type": "string", "maxLength": 140},
                "maxItems": 16,
            },
            "spoken_greeting": {"type": ["string", "null"], "maxLength": 600},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
    completion_guidance=(
        "Report the verified HoloStage or Chromecast outcome concisely in one natural Dutch sentence. "
        "If 'cast_stage' returns status='device_offline', state honestly that the Cast screen/TV "
        "is currently powered off or asleep on the network and will show full-screen 3D Eva as soon "
        "as it is turned on."
    ),
)


class HoloStageConversationActions:
    """Owner-authorized foreground HoloStage & Cast V2 actions for messaging and voice."""

    supports_indexed_steps = True
    action_names = (MANAGE_HOLOSTAGE.name,)

    def __init__(self, runtime_loader: Callable[[], Any]) -> None:
        self._runtime_loader = runtime_loader

    def _resolve_manager(self, runtime: Any) -> HoloStageManager:
        existing = getattr(runtime, "_holostage_manager", None)
        if isinstance(existing, HoloStageManager):
            return existing
        ctrl = getattr(runtime, "_model_provider_control", None) or getattr(
            runtime, "_optional_model_providers", None
        )
        settings = getattr(ctrl, "settings", None)
        directory = getattr(settings, "directory", None) or Path(
            os.environ.get("EVA_MODEL_PROVIDER_DIRECTORY", "/var/lib/eva/work/model-providers")
        )
        mgr = HoloStageManager(directory)
        try:
            setattr(runtime, "_holostage_manager", mgr)
        except Exception:
            pass
        return mgr

    def available_for(self, turn: Any) -> bool:
        runtime = self._runtime_loader()
        if runtime is None or getattr(turn, "project_id", None) is not None:
            return False
        if getattr(turn.audience, "kind", None) != "private_person":
            return False
        conv_id = str(getattr(turn, "conversation_id", ""))
        if conv_id.startswith("household:") or ":-" in conv_id:
            return False
        service = getattr(runtime, "_delegated_work_owner_service", None)
        if service is not None:
            return bool(service.available_for(turn))
        owner_id = getattr(runtime, "_dashboard_owner_principal_id", None)
        return bool(owner_id and getattr(turn, "principal_id", None) == owner_id)

    def active_context(self, turn: Any) -> tuple[str, ...]:
        if not self.available_for(turn):
            return ()
        runtime = self._runtime_loader()
        if runtime is None:
            return ()
        try:
            mgr = self._resolve_manager(runtime)
            raw_ts = getattr(turn, "occurred_at", None) or getattr(turn, "received_at", None)
            if isinstance(raw_ts, str) and raw_ts.strip():
                now = datetime.fromisoformat(raw_ts.strip().replace("Z", "+00:00")).astimezone(UTC).replace(microsecond=0)
            elif isinstance(raw_ts, datetime):
                now = raw_ts.astimezone(UTC).replace(microsecond=0)
            else:
                now = datetime.now(UTC).replace(microsecond=0)
            if mgr.biometrics.is_privacy_shield_active(now=now)[0]:
                mgr.clear_ephemeral_visual_context()
                return ()
            items: list[str] = []
            vis_ctx = mgr.get_ephemeral_visual_context(now=now)
            if vis_ctx is not None:
                items.append(
                    f"Actief live camerabeeld op HoloStage ({vis_ctx['source_label']}, "
                    f"{vis_ctx['age_seconds']}s geleden getoond, niet opgeslagen): {vis_ctx['summary_nl']}"
                )
            conv_id = str(getattr(turn, "conversation_id", ""))
            if conv_id.startswith(("voice:", "dashboard:")):
                st = mgr.build_stage_state(viewer_kind="owner", form_factor="cockpit", now=now)
                conjured = st.get("cards", {}).get("conjured_cards") or []
                if conjured:
                    items.append(f"Zichtbare hologramkaarten op Eva HoloStage: {', '.join(str(c) for c in conjured)}.")
            return tuple(items)
        except Exception:
            return ()

    def pending(self, turn: Any, *, step_index: int = 0) -> None:
        _ = (turn, step_index)
        return None

    def contact_reply_allowed(self, turn: Any, receipt: ConversationActionReceipt) -> bool:
        return (
            receipt.call.name in self.action_names
            and receipt.output.get("status") == "verified"
            and self.available_for(turn)
        )

    def conversation_instructions(self, turn: Any, available_names: tuple[str, ...]) -> tuple[str, ...]:
        if MANAGE_HOLOSTAGE.name not in available_names or not self.available_for(turn):
            return ()
        return (
            "Wanneer gevraagd wordt om jezelf op de Chromecast/TV te laten zien of iets visueels met een "
            "animatie op HoloStage/Chromecast te tonen, gebruik je direct `manage_holostage` (`operation='cast_stage'` "
            "met `animation_effect` zoals `'aurora_reveal'` of `'cinema_spotlight'`, en optioneel `title`, `body_nl` "
            "en `items` voor standen/kaarten). Vraagt de gebruiker om actuele informatie (zoals F1- of Eredivisie-stand) "
            "én weergave op de Chromecast, roep dan eerst `search_public_info(answer_directly=false)` aan en direct "
            "daarna `manage_holostage` in dezelfde beurt.",
        )

    def execute(self, turn: Any, call: Any, *, step_index: int = 0) -> ConversationActionReceipt:
        _ = step_index
        if call.name not in self.action_names or not self.available_for(turn):
            raise ConversationConflict("HoloStage control requires private owner authority")

        runtime = self._runtime_loader()
        service = getattr(runtime, "_delegated_work_owner_service", None)
        if service is not None:
            service.validate_authority(turn)

        args = call.arguments if isinstance(call.arguments, dict) else {}
        allowed_keys = {
            "operation",
            "card_kind",
            "animation_effect",
            "card_theme",
            "target_device",
            "approve_device",
            "title",
            "body_nl",
            "items",
            "spoken_greeting",
        }
        if "operation" not in args or not set(args).issubset(allowed_keys):
            raise ConversationActionInputRejected(("operation",))

        op = args.get("operation")
        if op not in (
            "status",
            "conjure_card",
            "stage_show",
            "dismiss_cards",
            "shopping_add",
            "cast_stage",
            "speak_on_stage",
        ):
            raise ConversationActionInputRejected(("operation",))

        mgr = self._resolve_manager(runtime)
        now = datetime.now(UTC).replace(microsecond=0)
        try:
            return self._execute_operation(mgr, call, args, op=op, now=now)
        except (ValueError, PermissionError) as exc:
            return ConversationActionReceipt(
                call,
                {"status": "rejected", "reason": str(exc)},
            )

    def _execute_operation(
        self,
        mgr: HoloStageManager,
        call: Any,
        args: dict[str, Any],
        *,
        op: str,
        now: datetime,
    ) -> ConversationActionReceipt:
        lan_origin = mgr.get_lan_stage_origin(now=now)
        shield_active = bool(mgr.biometrics.is_privacy_shield_active(now=now)[0])

        if op == "status":
            st = mgr.build_stage_state(viewer_kind="owner", form_factor="cockpit", now=now)
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "lan_stage_origin": lan_origin or None,
                    "conjured_cards": st["cards"]["conjured_cards"],
                    "shopping_list": st["cards"]["shopping_list"],
                    "cast_targets": st["cards"]["home_overview"]["cast_targets"],
                },
            )

        if op == "shopping_add":
            label = str(args.get("title") or args.get("body_nl") or "").strip()
            if not label:
                raise ConversationActionInputRejected(("title",))
            item = mgr.add_shopping_item(label=label, added_by="Jaap", now=now)
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "added_item": item.model_dump(mode="json"),
                    "conjured_cards": mgr.build_stage_state(viewer_kind="owner", now=now)["cards"]["conjured_cards"],
                },
            )

        def _infer_card_theme(title_s: str, items_l: list[str], explicit: str | None) -> str:
            if explicit in {"spotlight", "leaderboard", "climate", "celebration", "aurora"}:
                return str(explicit)
            low_t = title_s.casefold()
            if any(k in low_t for k in ("stand", "f1", "formule", "eredivisie", "klassement", "top ", "score")):
                return "leaderboard"
            if len(items_l) >= 2 and sum(1 for x in items_l if re.match(r"^\s*(?:#?\d+[.):\s-])", x)) >= 2:
                return "leaderboard"
            if any(k in low_t for k in ("temperatuur", "klimaat", "thermostaat", "weer", "graden")):
                return "climate"
            return "spotlight"

        if op == "stage_show":
            eff = str(args.get("animation_effect") or "aurora_reveal").strip()
            utt_priv = "private_household" if shield_active else "shared"
            card_out = None
            if args.get("title") or args.get("body_nl") or args.get("items"):
                title = str(args.get("title") or "Eva HoloStage").strip()
                body_nl = str(args.get("body_nl") or title).strip()
                raw_items = args.get("items")
                items_list = [str(x) for x in raw_items[:16]] if isinstance(raw_items, list) else []
                theme = _infer_card_theme(title, items_list, args.get("card_theme"))
                card_out = mgr.summon_spotlight_card(
                    title=title,
                    body_nl=body_nl,
                    items=items_list,
                    theme=theme,
                    privacy_level=utt_priv,
                    now=now,
                ).model_dump(mode="json")
            anim = mgr.trigger_stage_animation(
                effect=eff,
                label_nl=str(args.get("title") or "Eva HoloStage"),
                privacy_level=utt_priv,
                ttl_seconds=30,
                now=now,
            )
            if args.get("spoken_greeting"):
                mgr.record_stage_utterance(
                    eva_text=str(args["spoken_greeting"]), privacy_level=utt_priv, now=now
                )
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "stage_animation": anim,
                    "spotlight": card_out,
                },
            )

        if op == "conjure_card":
            raw_kind = str(args.get("card_kind") or "custom_spotlight").strip()
            eff = str(args.get("animation_effect") or "").strip()
            if raw_kind == "custom_spotlight" or (args.get("title") and args.get("body_nl")) or args.get("items"):
                title = str(args.get("title") or "Eva Overzicht").strip()
                body_nl = str(args.get("body_nl") or title).strip()
                raw_items = args.get("items")
                items_list = [str(x) for x in raw_items[:16]] if isinstance(raw_items, list) else []
                utt_priv = "private_household" if shield_active else "shared"
                theme = _infer_card_theme(title, items_list, args.get("card_theme"))
                card = mgr.summon_spotlight_card(
                    title=title,
                    body_nl=body_nl,
                    items=items_list,
                    theme=theme,
                    privacy_level=utt_priv,
                    now=now,
                )
                anim = mgr.trigger_stage_animation(
                    effect=eff or ("cinema_spotlight" if theme == "leaderboard" else "aurora_reveal"),
                    label_nl=title,
                    privacy_level=utt_priv,
                    ttl_seconds=28,
                    now=now,
                )
                if args.get("spoken_greeting"):
                    mgr.record_stage_utterance(
                        eva_text=str(args["spoken_greeting"]), privacy_level=utt_priv, now=now
                    )
                return ConversationActionReceipt(
                    call,
                    {
                        "status": "verified",
                        "conjured_card": "custom_spotlight",
                        "spotlight": card.model_dump(mode="json"),
                        "stage_animation": anim,
                    },
                )
            if raw_kind not in VALID_STAGE_CARD_KINDS:
                raise ConversationActionInputRejected(("card_kind",))
            conjured = mgr.conjure_card(raw_kind, allow_private=not shield_active, now=now)  # type: ignore[arg-type]
            if eff:
                mgr.trigger_stage_animation(
                    effect=eff,
                    label_nl=raw_kind,
                    privacy_level="private_household" if (raw_kind in PRIVATE_STAGE_CARD_KINDS or shield_active) else "shared",
                    ttl_seconds=24,
                    now=now,
                )
            if args.get("spoken_greeting"):
                utt_priv = (
                    "private_household"
                    if (raw_kind in PRIVATE_STAGE_CARD_KINDS or shield_active)
                    else "shared"
                )
                mgr.record_stage_utterance(
                    eva_text=str(args["spoken_greeting"]), privacy_level=utt_priv, now=now
                )
            return ConversationActionReceipt(
                call,
                {"status": "verified", "conjured_cards": conjured},
            )

        if op == "dismiss_cards":
            raw_kind = str(args.get("card_kind") or "all").strip()
            remaining = mgr.dismiss_conjured_card(raw_kind, allow_private=not shield_active, now=now)
            return ConversationActionReceipt(
                call,
                {"status": "verified", "conjured_cards": remaining},
            )

        if op == "speak_on_stage":
            spoken = str(args.get("spoken_greeting") or args.get("body_nl") or "").strip()
            if not spoken:
                raise ConversationActionInputRejected(("spoken_greeting",))
            utt = mgr.record_stage_utterance(
                eva_text=spoken,
                privacy_level="private_household" if shield_active else "shared",
                now=now,
            )
            return ConversationActionReceipt(
                call,
                {"status": "verified", "utterance": utt},
            )

        # op == "cast_stage"
        needle = str(args.get("target_device") or "").strip().casefold()
        hp_status = mgr.proprioception.status(now=now)
        cast_devs: list[dict[str, Any]] = []
        for dev in hp_status.get("devices") or []:
            if not isinstance(dev, dict):
                continue
            ports = list(dev.get("open_ports") or [])
            if isinstance(dev.get("port"), int):
                ports.append(int(dev["port"]))
            blob = f"{dev.get('kind', '')} {' '.join(str(x) for x in (dev.get('protocols') or []))}".lower()
            if 8009 in ports or "cast" in blob:
                cast_devs.append(dev)

        generic_words = {
            "cast",
            "chromecast",
            "google",
            "tv",
            "televisie",
            "scherm",
            "beeldscherm",
            "grote",
            "het",
            "de",
            "een",
            "in",
            "op",
            "naar",
            "mijn",
        }
        specific_tokens = [w for w in re.split(r"\W+", needle) if len(w) >= 2 and w not in generic_words]
        wants_woonkamer = any(t in {"woonkamer", "huiskamer", "beneden", "living"} for t in specific_tokens)
        non_room_tokens = [t for t in specific_tokens if t not in {"woonkamer", "huiskamer", "beneden", "living"}]

        candidate_devs: list[dict[str, Any]] = []
        if non_room_tokens:
            scored_devs: list[tuple[int, dict[str, Any]]] = []
            for d in cast_devs:
                disp = f"{d.get('device_id', '')} {d.get('display_name', '')}".casefold()
                room = str(d.get("room_id") or "").casefold()
                sc = 0
                if needle and needle in f"{disp} {room}":
                    sc += 100
                for tok in non_room_tokens:
                    if tok in disp:
                        sc += 50
                    elif tok in room:
                        sc += 20
                if sc > 0:
                    scored_devs.append((sc, d))
            scored_devs.sort(key=lambda x: -x[0])
            candidate_devs = [d for _, d in scored_devs]
        else:
            # Generic Chromecast / TV / Woonkamer request: rank living-room & primary non-schuur Cast screens first,
            # while keeping other active Cast screens as automatic fallback if the first one is powered off!
            def _default_cast_rank(d: dict[str, Any]) -> tuple[int, int, int, str]:
                disp = f"{d.get('device_id', '')} {d.get('display_name', '')}".casefold()
                room = str(d.get("room_id") or "").casefold()
                ignored_penalty = 1 if d.get("consent_status") == "ignored" else 0
                is_schuur_or_outdoor = 1 if any(k in disp for k in ("schuur", "buiten", "overkapping")) else 0
                is_woonkamer = -1 if ("woonkamer" in disp or (room in {"woonkamer", "huiskamer", "living"} and not is_schuur_or_outdoor)) else 0
                return (ignored_penalty, is_woonkamer, is_schuur_or_outdoor, disp)

            candidate_devs = sorted(cast_devs, key=_default_cast_rank)

        if not candidate_devs:
            return ConversationActionReceipt(
                call,
                {
                    "status": "device_not_found",
                    "available_cast_devices": [
                        {
                            "device_id": d.get("device_id"),
                            "label": d.get("display_name"),
                            "room_id": d.get("room_id"),
                            "consent_status": d.get("consent_status"),
                        }
                        for d in cast_devs
                    ],
                },
            )

        matched_dev = candidate_devs[0]
        dev_id = str(matched_dev["device_id"])
        dev_label = str(matched_dev.get("display_name") or dev_id)
        consent = str(matched_dev.get("consent_status") or "")
        if consent == "ignored":
            return ConversationActionReceipt(
                call,
                {
                    "status": "device_ignored",
                    "device_id": dev_id,
                    "label": dev_label,
                    "reason_nl": f"'{dev_label}' staat ingesteld op negeren en wordt niet automatisch geactiveerd.",
                },
            )
        if consent != "approved":
            if args.get("approve_device") is False:
                return ConversationActionReceipt(
                    call,
                    {
                        "status": "consent_required",
                        "device_id": dev_id,
                        "label": dev_label,
                        "hint": "Pass approve_device=true when the owner explicitly asked to cast to this screen.",
                    },
                )
            # Authenticated owner explicitly requested cast_stage -> approve & pin LAN IP immediately in 1 step!
            mgr.proprioception.set_device_consent(
                device_id=dev_id, consent_status="approved", now=now
            )

        if not lan_origin:
            return ConversationActionReceipt(
                call,
                {
                    "status": "lan_gateway_unconfigured",
                    "device_id": dev_id,
                    "label": dev_label,
                },
            )

        # Prepare shared visual card & 3D stage choreography BEFORE opening the TV stream so it renders immediately!
        spotlight_out = None
        if args.get("title") or args.get("body_nl") or args.get("items"):
            title = str(args.get("title") or "Eva op TV").strip()
            body_nl = str(args.get("body_nl") or title).strip()
            raw_items = args.get("items")
            items_list = [str(x) for x in raw_items[:16]] if isinstance(raw_items, list) else []
            theme = _infer_card_theme(title, items_list, args.get("card_theme"))
            spotlight_out = mgr.summon_spotlight_card(
                title=title,
                body_nl=body_nl,
                items=items_list,
                theme=theme,
                privacy_level="shared",
                now=now,
            ).model_dump(mode="json")
        elif args.get("card_kind") in VALID_STAGE_CARD_KINDS and args.get("card_kind") not in PRIVATE_STAGE_CARD_KINDS:
            mgr.conjure_card(str(args["card_kind"]), allow_private=False, now=now)  # type: ignore[arg-type]

        eff = str(args.get("animation_effect") or ("cinema_spotlight" if spotlight_out else "aurora_reveal")).strip()
        stage_anim = mgr.trigger_stage_animation(
            effect=eff,
            label_nl=str(args.get("title") or "Eva HoloStage"),
            privacy_level="shared",
            ttl_seconds=35,
            now=now,
        )

        if non_room_tokens:
            try_pool = [matched_dev]
        else:
            non_ignored = [d for d in candidate_devs if d.get("consent_status") != "ignored"]
            if wants_woonkamer:
                wk_only = [
                    d
                    for d in non_ignored
                    if "woonkamer" in f"{d.get('device_id', '')} {d.get('display_name', '')}".casefold()
                    or (
                        str(d.get("room_id") or "").casefold() in {"woonkamer", "huiskamer", "living"}
                        and not any(k in f"{d.get('device_id', '')} {d.get('display_name', '')}".casefold() for k in ("schuur", "buiten", "overkapping"))
                    )
                ]
                try_pool = (wk_only or non_ignored)[:3]
            else:
                try_pool = non_ignored[:3]
        last_offline_id = dev_id
        last_offline_label = dev_label

        for cand in try_pool:
            c_id = str(cand["device_id"])
            c_label = str(cand.get("display_name") or c_id)
            if cand.get("consent_status") != "approved":
                if args.get("approve_device") is False:
                    continue
                mgr.proprioception.set_device_consent(device_id=c_id, consent_status="approved", now=now)

            tv_grant = mgr.issue_device_token(
                label=f"Cast {c_label}"[:78],
                form_factor="tv_cast",
                room_id=str(cand.get("room_id") or "woonkamer"),
                allow_private_cards_when_alone=False,
                ttl_hours=720,
                now=now,
            )
            tv_stage_url = f"{lan_origin}{tv_grant['pair_path']}"
            try:
                cast_res = query_or_launch_approved_cast_device(
                    mgr.proprioception,
                    device_id=c_id,
                    stage_url=tv_stage_url,
                )
                if args.get("spoken_greeting"):
                    mgr.record_stage_utterance(
                        eva_text=str(args["spoken_greeting"]),
                        privacy_level="shared",
                        ttl_seconds=90,
                        now=now,
                    )
                return ConversationActionReceipt(
                    call,
                    {
                        "status": "verified",
                        "cast": cast_res,
                        "stage_animation": stage_anim,
                        "spotlight": spotlight_out,
                        "full_screen_3d_eva": True,
                        "voice_note": (
                            "3D Eva staat nu met animatie volledig in beeld op het scherm en spreekt via de tv-speakers."
                        ),
                    },
                )
            except (TimeoutError, OSError, ConnectionError, RuntimeError):
                last_offline_id = c_id
                last_offline_label = c_label
                try:
                    mgr.revoke_device_grant(str(tv_grant["grant"]["grant_id"]), now=now)
                except Exception:
                    pass

        return ConversationActionReceipt(
            call,
            {
                "status": "device_offline",
                "device_id": last_offline_id,
                "label": last_offline_label,
                "reason_nl": (
                    f"'{last_offline_label}' staat nu uit of in diepe slaapstand (geen reactie op Cast-poort 8009). "
                    "De toestemming staat klaar: zodra het scherm aan staat zet ik mezelf er direct volledig in 3D op."
                ),
            },
        )


__all__ = [
    "HoloStageConversationActions",
    "HoloStageSpeechBridge",
    "HoloStageVisionBridge",
    "MANAGE_HOLOSTAGE",
]
