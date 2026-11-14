"""Foreground Owner Camera Snapshot Lookup & Outbound Photo Delivery Tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from eva.adapters.connectors.camera_snapshot_buffer import (
    CameraSnapshotRingBuffer,
    capture_live_configured_camera,
    discover_configured_cameras,
)
from eva.application.conversation import (
    ConversationActionInputRejected,
    ConversationActionReceipt,
    ConversationConflict,
    ConversationToolDescriptor,
)
from eva.application.outbound_media_claims import queue_telegram_outbound_photo

SEND_CAMERA_PHOTO = ConversationToolDescriptor(
    name="send_camera_photo",
    description=(
        "Retrieve or send a real camera photo for the owner (either from a recent motion/doorbell "
        "event stored in the bounded 72h camera snapshot buffer, or by taking a live snapshot "
        "from a connected household camera right now). Use operation='send_photo' when the owner "
        "asks 'Heb je een foto?', 'Laat die beweging zien', or 'Stuur een foto van de tuin/oprit/schuur'. "
        "When speaking in Telegram, this automatically attaches the actual JPEG photo to your "
        "Telegram reply. Use operation='list_snapshots' to inspect available cameras and recent "
        "saved event snapshots."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["send_photo", "list_snapshots"],
            },
            "source_mode": {
                "type": ["string", "null"],
                "enum": ["auto", "recent_event", "live", None],
            },
            "camera_entity_id": {"type": ["string", "null"], "maxLength": 120},
            "area_or_hint": {"type": ["string", "null"], "maxLength": 120},
            "snapshot_id_or_sha256": {"type": ["string", "null"], "maxLength": 80},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
    completion_guidance=(
        "Describe concisely in natural Dutch which camera and timestamp the photo comes from "
        "(distinguishing a saved motion-event snapshot from a live current snapshot). "
        "If status='telegram_photo_queued', tell the owner naturally that the photo is attached "
        "with this message. If status='unavailable', explain honestly why no camera snapshot "
        "was found or reachable without inventing details."
    ),
)


class CameraPhotoConversationActions:
    """Owner-only foreground camera snapshot lookup & Telegram/HoloStage photo sender."""

    supports_indexed_steps = True
    action_names = (SEND_CAMERA_PHOTO.name,)

    def __init__(
        self,
        runtime_loader: Callable[[], Any],
        *,
        buffer_factory: Callable[[], CameraSnapshotRingBuffer] = CameraSnapshotRingBuffer,
    ) -> None:
        self._runtime_loader = runtime_loader
        self._buffer_factory = buffer_factory

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

    def active_context(self, turn: Any) -> tuple[Any, ...]:
        _ = turn
        return ()

    def pending(self, turn: Any, *, step_index: int = 0) -> None:
        _ = (turn, step_index)
        return None

    def execute(self, turn: Any, call: Any, *, step_index: int = 0) -> ConversationActionReceipt:
        _ = step_index
        if call.name not in self.action_names or not self.available_for(turn):
            raise ConversationConflict("Camera photo access requires private owner authority")

        runtime = self._runtime_loader()
        service = getattr(runtime, "_delegated_work_owner_service", None)
        if service is not None:
            service.validate_authority(turn)

        args = call.arguments if isinstance(call.arguments, dict) else {}
        allowed_keys = {
            "operation",
            "source_mode",
            "camera_entity_id",
            "area_or_hint",
            "snapshot_id_or_sha256",
        }
        if "operation" not in args or not set(args).issubset(allowed_keys):
            raise ConversationActionInputRejected(("operation",))
        op = args.get("operation")
        if op not in ("send_photo", "list_snapshots"):
            raise ConversationActionInputRejected(("operation",))

        buf = self._buffer_factory()
        now = datetime.now(UTC)
        cam_id = str(args.get("camera_entity_id") or "").strip() or None
        hint = str(args.get("area_or_hint") or "").strip() or None
        ident = str(args.get("snapshot_id_or_sha256") or "").strip() or None
        mode = str(args.get("source_mode") or "auto").strip()
        if mode not in ("auto", "recent_event", "live"):
            raise ConversationActionInputRejected(("source_mode",))

        if op == "list_snapshots":
            return ConversationActionReceipt(
                call,
                {
                    "status": "verified",
                    "recent_snapshots": buf.list_recent(
                        camera_entity_id=cam_id, area_id=hint, limit=12, now=now
                    ),
                    "available_cameras": discover_configured_cameras(),
                },
            )

        resolved: tuple[dict[str, Any], bytes] | None = None
        if mode in ("recent_event", "auto"):
            candidate = buf.get_snapshot(
                ident, camera_entity_id=cam_id, area_id=hint, now=now
            )
            if candidate is not None:
                if mode == "recent_event" or ident:
                    resolved = candidate
                else:
                    try:
                        cap_dt = datetime.fromisoformat(
                            str(candidate[0]["captured_at"]).replace("Z", "+00:00")
                        )
                        if (now - cap_dt).total_seconds() <= 3 * 3600:
                            resolved = candidate
                    except Exception:
                        resolved = candidate

        if resolved is None and mode in ("live", "auto"):
            try:
                snap, target_area = capture_live_configured_camera(
                    camera_entity_id=cam_id, area_or_hint=hint
                )
                meta = buf.record_snapshot(
                    snap,
                    area_id=target_area,
                    source_kind="live_owner_request",
                    now=now,
                )
                resolved = (meta, snap.data)
            except Exception as exc:
                if mode == "live":
                    return ConversationActionReceipt(
                        call,
                        {
                            "status": "unavailable",
                            "reason": f"Live camera snapshot mislukt ({type(exc).__name__})",
                        },
                    )

        if resolved is None and mode == "auto":
            resolved = buf.get_snapshot(
                ident, camera_entity_id=cam_id, area_id=hint, now=now
            )

        if resolved is None:
            return ConversationActionReceipt(
                call,
                {
                    "status": "unavailable",
                    "reason": "Er staat nog geen passende camerafoto in de buffer en er kon geen live snapshot worden opgehaald.",
                    "available_cameras": discover_configured_cameras(),
                },
            )

        meta, raw_bytes = resolved
        conv_id = str(getattr(turn, "conversation_id", ""))
        msg_id = str(getattr(turn, "message_id", ""))
        if conv_id.startswith("telegram:"):
            queue_telegram_outbound_photo(msg_id, meta=meta, raw_bytes=raw_bytes)
            return ConversationActionReceipt(
                call,
                {
                    "status": "telegram_photo_queued",
                    "snapshot": meta,
                },
            )
        return ConversationActionReceipt(
            call,
            {
                "status": "verified",
                "snapshot": meta,
            },
        )


__all__ = [
    "CameraPhotoConversationActions",
    "SEND_CAMERA_PHOTO",
]
