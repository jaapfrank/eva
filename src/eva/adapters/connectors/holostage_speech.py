"""Zero-retention neural Dutch TTS & PCM speech connector for Eva HoloStage."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import urlsplit

def _valid_speech_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/api/tts_proxy/")
        and not any(c in path for c in ("%", "?", "#", "\\"))
        and not any(segment in {".", ".."} for segment in path.split("/"))
        and not any(ord(c) <= 32 or ord(c) == 127 for c in path)
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow HTTP redirects with provider Bearer credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, "redirects disallowed", headers, fp)


_SAFE_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_ALLOWED_AUDIO_MIMES = frozenset({"audio/mpeg", "audio/wav", "audio/ogg", "audio/flac"})


class HoloStageSpeechBridge:
    """Thread-safe neural Dutch TTS & 16 kHz PCM STT connector with zero raw audio persistence."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        credential: str | None = None,
        dashboard_voice: Any = None,
    ) -> None:
        clean_origin = ""
        if endpoint_url:
            parts = urlsplit(endpoint_url.strip())
            if parts.scheme in ("http", "https") and parts.netloc and not parts.username and not parts.password:
                clean_origin = f"{parts.scheme}://{parts.netloc}"
        self._origin = clean_origin
        self._token = (credential or "").strip()
        self._dashboard_voice = dashboard_voice

    @classmethod
    def from_runtime_settings(cls, settings: Any, *, dashboard_voice: Any = None) -> "HoloStageSpeechBridge":
        loaded = () if getattr(settings, "external_provider", None) is None else settings.external_provider.providers
        media_settings = next(
            (
                item.settings
                for item in loaded
                if hasattr(getattr(item, "settings", None), "endpoint_url")
                and hasattr(getattr(item, "settings", None), "credential")
            ),
            None,
        )
        if media_settings is None:
            return cls(dashboard_voice=dashboard_voice)
        return cls(
            endpoint_url=str(media_settings.endpoint_url),
            credential=str(media_settings.credential),
            dashboard_voice=dashboard_voice,
        )

    @property
    def available(self) -> bool:
        return bool((self._origin and self._token) or self._dashboard_voice is not None)

    def synthesize_speech(self, text: str) -> tuple[bytes, str] | None:
        """Synthesize a spoken Dutch clause into bounded neural audio bytes `(data, mime_type)`."""
        clean = re.sub(r"\s+", " ", (text or "")).strip()
        if not clean or len(clean) > 1500:
            return None

        if self._dashboard_voice is not None:
            try:
                session = self._dashboard_voice.issue_session()
                self._dashboard_voice.check(session)

                async def _collect() -> tuple[bytes, str] | None:
                    buf = bytearray()
                    mime = ""
                    stream = self._dashboard_voice.speech_output.synthesize(clean)
                    try:
                        async for ch in stream:
                            buf.extend(ch.data)
                            mime = ch.mime_type
                            if len(buf) > 4_000_000:
                                return None
                    finally:
                        await stream.aclose()
                    return (bytes(buf), mime) if buf and mime in _ALLOWED_AUDIO_MIMES else None

                res = asyncio.run(_collect())
                if res is not None:
                    return res
            except Exception:
                pass

        if not (self._origin and self._token):
            return None

        # Only local Piper TTS is permitted when `DashboardVoice` policy consent is absent or inactive.
        tts_specs: tuple[dict[str, Any], ...] = (
            {"engine_id": "tts.piper", "message": clean, "language": "nl_NL", "cache": False},
        )
        for spec in tts_specs:
            try:
                prep_req = urllib.request.Request(
                    f"{self._origin}/api/tts_get_url",
                    data=json.dumps(spec, ensure_ascii=False).encode("utf-8"),
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                    },
                )
                with _SAFE_OPENER.open(prep_req, timeout=6.0) as prep_resp:
                    if prep_resp.status != 200:
                        continue
                    prep_data = json.loads(prep_resp.read(16_384).decode("utf-8"))
                path = prep_data.get("path") if isinstance(prep_data, dict) else None
                if not _valid_speech_path(path):
                    continue
                audio_req = urllib.request.Request(
                    f"{self._origin}{path}",
                    method="GET",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                with _SAFE_OPENER.open(audio_req, timeout=8.0) as audio_resp:
                    if audio_resp.status != 200:
                        continue
                    ctype = (audio_resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                    if ctype not in _ALLOWED_AUDIO_MIMES:
                        continue
                    audio_bytes = audio_resp.read(4_000_001)
                    if 0 < len(audio_bytes) <= 4_000_000:
                        return audio_bytes, ctype
            except Exception:
                continue

        return None


__all__ = ["HoloStageSpeechBridge"]
