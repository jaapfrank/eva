"""Bounded rolling ring-buffer for recent camera motion-event and owner snapshots.

Stores at most `max_items` validated still frames with strict `0700`/`0600`
filesystem permissions and automatic TTL + count eviction. Never exposes public
paths or unauthenticated access.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Iterator

from eva.adapters.connectors.camera_observer import HomeAssistantCameraDiscovery
from eva.adapters.connectors.home_assistant_camera import CameraSnapshot, HomeAssistantCameraSource


def _read_secret_file(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()

_SNAPSHOT_ID_RE = re.compile(r"^snap_[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class CameraSnapshotRingBuffer:
    """Thread-safe, TTL-bounded local ring-buffer of recent camera snapshots."""

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        max_items: int = 48,
        ttl_seconds: int = 72 * 3600,
    ) -> None:
        if max_items < 1 or max_items > 256 or ttl_seconds < 60:
            raise ValueError("camera snapshot buffer bounds are invalid")
        if directory is not None:
            raw_dir: str | Path = directory
        elif os.environ.get("EVA_CAMERA_SNAPSHOT_DIR", "").strip():
            raw_dir = os.environ["EVA_CAMERA_SNAPSHOT_DIR"].strip()
        elif os.access("/var/lib/eva/work", os.W_OK) or os.path.isdir("/var/lib/eva/work/camera-snapshots"):
            raw_dir = "/var/lib/eva/work/camera-snapshots"
        else:
            raw_dir = "/var/lib/eva/work/model-providers/camera-snapshots"
        self.directory = Path(raw_dir)
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._index_path = self.directory / "index.json"
        self._lock_path = self.directory / ".buffer.lock"
        self._lock = threading.RLock()

    def _ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock:
            self._ensure_dir()
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _write_private_bytes(self, path: Path, payload: bytes) -> None:
        self._ensure_dir()
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(tmp), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        else:
            os.close(fd)
        os.replace(tmp, path)

    def _load_entries_unlocked(self, *, now: datetime) -> list[dict[str, Any]]:
        if not self._index_path.exists():
            return []
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        kept: list[dict[str, Any]] = []
        changed = False
        for row in items:
            if not isinstance(row, dict):
                changed = True
                continue
            snap_id = row.get("snapshot_id")
            filename = row.get("filename")
            captured_raw = row.get("captured_at")
            if (
                not isinstance(snap_id, str)
                or not _SNAPSHOT_ID_RE.match(snap_id)
                or not isinstance(filename, str)
                or "/" in filename
                or "\\" in filename
                or not isinstance(captured_raw, str)
            ):
                changed = True
                continue
            try:
                captured_dt = datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
                if captured_dt.tzinfo is None:
                    captured_dt = captured_dt.replace(tzinfo=UTC)
            except ValueError:
                changed = True
                continue
            file_path = self.directory / filename
            if (now - captured_dt).total_seconds() > self.ttl_seconds or not file_path.is_file():
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
                changed = True
                continue
            kept.append(row)
        if len(kept) > self.max_items:
            evicted = kept[self.max_items :]
            kept = kept[: self.max_items]
            for old in evicted:
                fname = old.get("filename")
                if isinstance(fname, str):
                    try:
                        (self.directory / fname).unlink(missing_ok=True)
                    except OSError:
                        pass
            changed = True
        if changed:
            self._save_entries_unlocked(kept)
        return kept

    def _save_entries_unlocked(self, items: list[dict[str, Any]]) -> None:
        doc = {"contract": "EvaCameraSnapshotBuffer/v1", "items": items}
        self._write_private_bytes(
            self._index_path,
            (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def record_snapshot(
        self,
        snapshot: CameraSnapshot,
        *,
        area_id: str = "",
        event_occurred_at: str | None = None,
        scene_description: str | None = None,
        source_kind: str = "motion_event",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ts = now or datetime.now(UTC)
        digest = snapshot.sha256
        snap_id = f"snap_{digest[:24]}"
        ext = _EXT_BY_CONTENT_TYPE.get(snapshot.content_type, "jpg")
        filename = f"{snap_id}.{ext}"
        entry = {
            "snapshot_id": snap_id,
            "image_sha256": digest,
            "camera_entity_id": snapshot.entity_id,
            "area_id": str(area_id or "").strip()[:80],
            "content_type": snapshot.content_type,
            "filename": filename,
            "byte_length": len(snapshot.data),
            "captured_at": snapshot.retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "event_occurred_at": (event_occurred_at or snapshot.retrieved_at.astimezone(UTC).isoformat()).replace(
                "+00:00", "Z"
            ),
            "source_kind": source_kind,
            "scene_description": (str(scene_description).strip()[:400] if scene_description else None),
        }
        with self._locked():
            existing = self._load_entries_unlocked(now=ts)
            self._write_private_bytes(self.directory / filename, snapshot.data)
            filtered = [row for row in existing if row.get("snapshot_id") != snap_id]
            updated = [entry, *filtered]
            if len(updated) > self.max_items:
                for old in updated[self.max_items :]:
                    fname = old.get("filename")
                    if isinstance(fname, str) and fname != filename:
                        try:
                            (self.directory / fname).unlink(missing_ok=True)
                        except OSError:
                            pass
                updated = updated[: self.max_items]
            self._save_entries_unlocked(updated)
        return dict(entry)

    def list_recent(
        self,
        *,
        camera_entity_id: str | None = None,
        area_id: str | None = None,
        limit: int = 12,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ts = now or datetime.now(UTC)
        cam_needle = (camera_entity_id or "").strip().casefold()
        area_needle = (area_id or "").strip().casefold()
        with self._locked():
            items = self._load_entries_unlocked(now=ts)
        matched: list[dict[str, Any]] = []
        for row in items:
            if cam_needle and cam_needle not in str(row.get("camera_entity_id", "")).casefold():
                continue
            if area_needle and area_needle not in str(row.get("area_id", "")).casefold() and area_needle not in str(
                row.get("camera_entity_id", "")
            ).casefold():
                continue
            matched.append({k: v for k, v in row.items() if k != "filename"})
            if len(matched) >= max(1, min(limit, 48)):
                break
        return matched

    def get_snapshot(
        self,
        identifier: str | None = None,
        *,
        camera_entity_id: str | None = None,
        area_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], bytes] | None:
        ts = now or datetime.now(UTC)
        ident = (identifier or "").strip().lower()
        cam_needle = (camera_entity_id or "").strip().casefold()
        area_needle = (area_id or "").strip().casefold()
        with self._locked():
            items = self._load_entries_unlocked(now=ts)
            selected: dict[str, Any] | None = None
            for row in items:
                if ident:
                    if row.get("snapshot_id") == ident or row.get("image_sha256") == ident:
                        selected = row
                        break
                    continue
                if cam_needle and cam_needle not in str(row.get("camera_entity_id", "")).casefold():
                    continue
                if (
                    area_needle
                    and area_needle not in str(row.get("area_id", "")).casefold()
                    and area_needle not in str(row.get("camera_entity_id", "")).casefold()
                ):
                    continue
                selected = row
                break
            if selected is None:
                return None
            file_path = self.directory / str(selected["filename"])
            try:
                raw_bytes = file_path.read_bytes()
            except OSError:
                return None
        expected_sha = selected.get("image_sha256")
        if not isinstance(expected_sha, str) or not _SHA256_RE.match(expected_sha):
            return None
        if hashlib.sha256(raw_bytes).hexdigest() != expected_sha:
            return None
        meta = {k: v for k, v in selected.items() if k != "filename"}
        return meta, raw_bytes


def discover_configured_cameras() -> list[dict[str, Any]]:
    base_url = os.environ.get("EVA_HOME_ASSISTANT_URL", "").strip()
    token_file = os.environ.get("EVA_HOME_ASSISTANT_TOKEN_FILE", "").strip()
    if not base_url or not token_file:
        return []
    try:
        token = _read_secret_file(token_file)
        if not token:
            return []
        inv = HomeAssistantCameraDiscovery(base_url=base_url, token=token).discover()
        return [
            {
                "camera_entity_id": d.camera_entity_id,
                "area_id": d.area_id,
                "integration": d.integration,
            }
            for d in inv.devices
        ]
    except Exception:
        return []


def capture_live_configured_camera(
    *, camera_entity_id: str | None = None, area_or_hint: str | None = None
) -> tuple[CameraSnapshot, str]:
    base_url = os.environ.get("EVA_HOME_ASSISTANT_URL", "").strip()
    token_file = os.environ.get("EVA_HOME_ASSISTANT_TOKEN_FILE", "").strip()
    if not base_url or not token_file:
        raise RuntimeError("camera connector is not configured")
    token = _read_secret_file(token_file)
    if not token:
        raise RuntimeError("camera credential is empty")
    cams = discover_configured_cameras()
    if not cams:
        raise RuntimeError("no configured cameras discovered")
    target_cam = (camera_entity_id or "").strip()
    target_area = (area_or_hint or "").strip()
    if target_cam.startswith("camera."):
        matched = next(
            (c for c in cams if str(c.get("camera_entity_id", "")).casefold() == target_cam.casefold()),
            None,
        )
    else:
        needle = (target_cam or target_area).casefold()
        matched = next(
            (
                c
                for c in cams
                if not needle
                or needle in str(c.get("camera_entity_id", "")).casefold()
                or needle in str(c.get("area_id", "")).casefold()
            ),
            cams[0] if not needle else None,
        )
    if matched is None:
        raise RuntimeError("no matching configured camera discovered")
    resolved_cam = str(matched["camera_entity_id"])
    resolved_area = str(matched.get("area_id") or target_area)
    snap = HomeAssistantCameraSource(base_url=base_url, token=token).snapshot(resolved_cam)
    return snap, resolved_area


__all__ = [
    "CameraSnapshotRingBuffer",
    "capture_live_configured_camera",
    "discover_configured_cameras",
]
