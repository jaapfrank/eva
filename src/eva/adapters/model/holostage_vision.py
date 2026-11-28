"""Zero-retention Visual Cortex adapter for Eva HoloStage & Eva Pocket camera co-pilot.

Strips EXIF/metadata in memory, prefers local vision when reachable, and falls back to
Eva's configured stateless Responses lease (`store=False`) strictly as a factual visual
perception organ (`V1/V4`) — never persisting raw frames and never acting as a second brain.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import struct
import time
from typing import Any
from urllib.parse import urlsplit

import httpx2

from eva.adapters.credentials.runtime_files import read_shared_runtime_file_bytes
from eva.adapters.model.openai_endpoint import responses_base_url

MAX_VISION_IMAGE_BYTES = 1_250_000
MAX_VISION_SSE_BYTES = 262_144
DEFAULT_CODEX_VISION_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_CODEX_VISION_MODEL = "gpt-6.2-astra"

_MODEL_LEASE_OWNER_UID = 1000
_MODEL_LEASE_READER_GID = 65532
_MODEL_LEASE_FILE_MODE = 0o640
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_SAFE_CHUNKS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"IDAT", b"IEND"})

_VISUAL_PERCEPTION_INSTRUCTIONS = (
    "Fungeer uitsluitend als feitelijk visueel waarnemingsorgaan voor Eva. "
    "Beschrijf beknopt, helder en feitelijk in het Nederlands (maximaal 110 woorden) "
    "wat daadwerkelijk zichtbaar is op dit beeld: belangrijke voorwerpen, producten, "
    "zichtbare tekst/etiketten/foutcodes, aantallen en toestand. "
    "Benoem onzekerheid eerlijk als iets onscherp of deels zichtbaar is. "
    "Raad geen persoonsidentiteiten en spreek de gebruiker niet zelf aan."
)


@dataclass(frozen=True, slots=True)
class VisualPerceptionReceipt:
    """Bounded factual scene observation produced from a sanitized in-memory frame."""

    summary_nl: str
    image_sha256: str
    media_type: str
    sanitized_bytes: int
    backend: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class HoloStageVisionError(RuntimeError):
    """Safe, scrubbed visual perception error suitable for API/UI handling."""

    def __init__(self, category: str, detail_nl: str) -> None:
        self.category = category
        self.detail_nl = detail_nl
        super().__init__(detail_nl)


def sanitize_visual_frame_bytes(raw_bytes: bytes, media_type: str) -> tuple[bytes, str, str]:
    """Validate JPEG/PNG structure and strip EXIF/GPS/comment metadata in memory."""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ValueError("Camerabeeld ontbreekt of is ongeldig")
    buf = bytes(raw_bytes)
    if len(buf) < 64:
        raise ValueError("Camerabeeld is te klein of leeg")
    if len(buf) > MAX_VISION_IMAGE_BYTES:
        raise ValueError("Camerabeeld overschrijdt de limiet van 1,25 MB")

    norm_mime = (media_type or "").split(";", 1)[0].strip().lower()
    if norm_mime in ("", "image/jpg"):
        norm_mime = "image/jpeg"

    if norm_mime == "image/jpeg" or (not media_type and buf.startswith(b"\xff\xd8\xff")):
        clean = _strip_jpeg_metadata(buf)
        return clean, "image/jpeg", hashlib.sha256(clean).hexdigest()

    if norm_mime == "image/png" or (not media_type and buf.startswith(_PNG_MAGIC)):
        clean = _strip_png_metadata(buf)
        return clean, "image/png", hashlib.sha256(clean).hexdigest()

    raise ValueError("Alleen JPEG- en PNG-camerabeelden worden ondersteund")


def _strip_jpeg_metadata(buf: bytes) -> bytes:
    if len(buf) < 4 or buf[:3] != b"\xff\xd8\xff":
        raise ValueError("Ongeldig JPEG-camerabeeld")
    out = bytearray(b"\xff\xd8")
    pos = 2
    n = len(buf)
    saw_image_stream = False
    saw_eoi = False

    while pos < n:
        if buf[pos] != 0xFF:
            raise ValueError("Beschadigde JPEG-segmentstructuur")
        while pos < n and buf[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = buf[pos]
        pos += 1

        if marker == 0xD9:  # Primary EOI — drop any trailing MPF/GainMap/secondary image bytes
            saw_eoi = True
            out.extend(b"\xff\xd9")
            break
        if marker == 0x00 or marker == 0x01 or 0xD0 <= marker <= 0xD8:
            out.extend((0xFF, marker))
            continue

        if pos + 2 > n:
            raise ValueError("Onvolledige JPEG-segmentkop")
        seg_len = struct.unpack(">H", buf[pos : pos + 2])[0]
        if seg_len < 2 or pos + seg_len > n:
            raise ValueError("Ongeldige JPEG-segmentlengte")

        if marker == 0xDA:  # SOS — scan entropy-coded data up to the next real JPEG marker
            saw_image_stream = True
            out.extend((0xFF, marker))
            out.extend(buf[pos : pos + seg_len])
            scan_start = pos + seg_len
            i = scan_start
            next_marker_pos = -1
            while i < n:
                ff = buf.find(b"\xff", i)
                if ff < 0:
                    raise ValueError("JPEG mist geldige eindmarkering")
                j = ff + 1
                while j < n and buf[j] == 0xFF:
                    j += 1
                if j >= n:
                    raise ValueError("JPEG mist geldige eindmarkering")
                m = buf[j]
                if m == 0x00 or 0xD0 <= m <= 0xD7:
                    # Byte-stuffed 0xFF00 or RST0..RST7 restart marker inside entropy stream
                    i = j + 1
                    continue
                next_marker_pos = ff
                break
            if next_marker_pos < 0:
                raise ValueError("JPEG mist geldige eindmarkering")
            out.extend(buf[scan_start:next_marker_pos])
            pos = next_marker_pos
            continue

        # Strip APP1..APP15 (EXIF/GPS/XMP/ICC/MakerNotes) and COM (comments) across all scans
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out.extend((0xFF, marker))
            out.extend(buf[pos : pos + seg_len])
        pos += seg_len

    if not (saw_image_stream and saw_eoi):
        raise ValueError("JPEG bevat geen geldige afbeeldingsscan")
    return bytes(out)


def _strip_png_metadata(buf: bytes) -> bytes:
    if len(buf) < 33 or not buf.startswith(_PNG_MAGIC):
        raise ValueError("Ongeldig PNG-camerabeeld")
    out = bytearray(_PNG_MAGIC)
    pos = 8
    n = len(buf)
    saw_ihdr = False
    saw_idat = False
    saw_iend = False

    while pos + 12 <= n:
        chunk_len = struct.unpack(">I", buf[pos : pos + 4])[0]
        chunk_type = buf[pos + 4 : pos + 8]
        end = pos + 12 + chunk_len
        if end > n:
            raise ValueError("Beschadigde PNG-chunklengte")
        if chunk_type == b"IHDR":
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            saw_iend = True
        if chunk_type in _PNG_SAFE_CHUNKS:
            out.extend(buf[pos:end])
        pos = end
        if saw_iend:
            break

    if not (saw_ihdr and saw_idat and saw_iend):
        raise ValueError("PNG mist verplichte afbeeldingsdata")
    return bytes(out)


def _is_local_http_endpoint(raw_url: str) -> bool:
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    host = parsed.hostname or ""
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        addr in ipaddress.ip_network(net)
        for net in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")
    )


def _load_lease_credentials(auth_file: Path) -> tuple[str, str]:
    try:
        raw = read_shared_runtime_file_bytes(
            str(auth_file),
            setting_name="HoloStage vision model access lease",
            owner_uid=_MODEL_LEASE_OWNER_UID,
            reader_gid=_MODEL_LEASE_READER_GID,
            required_mode=_MODEL_LEASE_FILE_MODE,
        )
        if len(raw) > 16_384:
            raise HoloStageVisionError(
                "credentials",
                "Visuele modeltoegang overschrijdt de veilige bestandsgrootte",
            )
        parsed = json.loads(raw.decode("utf-8"))
    except HoloStageVisionError:
        raise
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        raise HoloStageVisionError(
            "credentials",
            "Visuele modeltoegang kon niet veilig worden gelezen",
        ) from None
    if not isinstance(parsed, dict) or parsed.get("contract") != "EvaModelAccessLease/v1":
        raise HoloStageVisionError("credentials", "Visuele modeltoegang heeft een ongeldig leasecontract")
    src_hash = parsed.get("source_sha256")
    tok = parsed.get("access_token")
    acct = parsed.get("account_id")
    if (
        not isinstance(src_hash, str)
        or _SHA256_RE.fullmatch(src_hash) is None
        or not isinstance(tok, str)
        or not tok.strip()
        or not isinstance(acct, str)
        or not acct.strip()
    ):
        raise HoloStageVisionError("credentials", "Visuele modeltoegang mist geldige referenties")
    return tok.strip(), acct.strip()


class HoloStageVisionBridge:
    """Stateless, zero-retention visual scene analyzer for Eva HoloStage & Eva Pocket."""

    def __init__(
        self,
        *,
        local_vision_url: str | None = None,
        local_vision_model: str | None = None,
        codex_endpoint: str = DEFAULT_CODEX_VISION_ENDPOINT,
        codex_model: str = DEFAULT_CODEX_VISION_MODEL,
        credential_loader: Callable[[], tuple[str, str]] | None = None,
        http_transport: httpx2.BaseTransport | None = None,
    ) -> None:
        clean_local = (local_vision_url or "").strip().rstrip("/")
        self._local_url = clean_local if clean_local and _is_local_http_endpoint(clean_local) else ""
        self._local_model = (local_vision_model or "").strip()[:160]
        responses_base_url(codex_endpoint, invalid_error="Vision endpoint must end in /responses")
        self._codex_endpoint = codex_endpoint.strip()
        self._codex_model = (codex_model or DEFAULT_CODEX_VISION_MODEL).strip()[:120]
        self._credential_loader = credential_loader
        self._http_transport = http_transport

    @classmethod
    def from_runtime_settings(cls, settings: Any = None) -> "HoloStageVisionBridge":
        local_url = os.environ.get("EVA_LOCAL_VISION_URL", "").strip()
        local_model = os.environ.get("EVA_LOCAL_VISION_MODEL", "").strip()
        raw_auth = (
            getattr(settings, "conversation_auth_file", None)
            or os.environ.get("EVA_CONVERSATION_AUTH_FILE", "")
        )
        auth_path = Path(str(raw_auth)) if raw_auth else None
        codex_model = (
            getattr(settings, "conversation_model", None)
            or os.environ.get("EVA_CONVERSATION_MODEL")
            or DEFAULT_CODEX_VISION_MODEL
        )
        loader: Callable[[], tuple[str, str]] | None = None
        if auth_path is not None and auth_path.is_absolute():
            loader = lambda p=auth_path: _load_lease_credentials(p)
        return cls(
            local_vision_url=local_url,
            local_vision_model=local_model,
            codex_model=str(codex_model),
            credential_loader=loader,
        )

    @property
    def available(self) -> bool:
        return bool((self._local_url and self._local_model) or self._credential_loader is not None)

    def analyze_frame(
        self,
        raw_bytes: bytes,
        *,
        media_type: str = "image/jpeg",
        focus_hint: str = "",
    ) -> VisualPerceptionReceipt:
        """Sanitize an in-memory camera frame and return a bounded Dutch visual observation."""
        clean_bytes, clean_mime, sha256_hex = sanitize_visual_frame_bytes(raw_bytes, media_type)
        b64_data = base64.b64encode(clean_bytes).decode("ascii")
        data_uri = f"data:{clean_mime};base64,{b64_data}"

        safe_hint = re.sub(r"\s+", " ", (focus_hint or "")).strip()[:280]
        prompt_nl = (
            f"Beschrijf wat je op dit beeld ziet (let extra op: {safe_hint})."
            if safe_hint
            else "Beschrijf feitelijk wat je op dit camerabeeld ziet."
        )

        t0 = time.monotonic()
        if self._local_url and self._local_model:
            local_obs = self._try_local_vision(data_uri, prompt_nl)
            if local_obs is not None:
                return VisualPerceptionReceipt(
                    summary_nl=local_obs,
                    image_sha256=sha256_hex,
                    media_type=clean_mime,
                    sanitized_bytes=len(clean_bytes),
                    backend="local_vision",
                    model=self._local_model,
                    latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
                )

        if self._credential_loader is None:
            raise HoloStageVisionError(
                "unavailable",
                "Visuele camerawaarneming is momenteel niet beschikbaar op deze installatie.",
            )

        summary_nl, in_tok, out_tok, tot_tok = self._analyze_via_stateless_codex(data_uri, prompt_nl)
        return VisualPerceptionReceipt(
            summary_nl=summary_nl,
            image_sha256=sha256_hex,
            media_type=clean_mime,
            sanitized_bytes=len(clean_bytes),
            backend="codex_stateless_vision",
            model=self._codex_model,
            latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
        )

    def _try_local_vision(self, data_uri: str, prompt_nl: str) -> str | None:
        body = {
            "model": self._local_model,
            "messages": [
                {"role": "system", "content": _VISUAL_PERCEPTION_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_nl},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "max_tokens": 280,
            "temperature": 0,
        }
        if self._local_url.endswith("/chat/completions"):
            target_url = self._local_url
        elif self._local_url.endswith("/v1"):
            target_url = f"{self._local_url}/chat/completions"
        else:
            target_url = f"{self._local_url}/v1/chat/completions"
        try:
            with httpx2.Client(
                transport=self._http_transport,
                timeout=httpx2.Timeout(12.0, connect=1.5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream("POST", target_url, json=body) as resp:
                    if resp.status_code != 200:
                        return None
                    raw = bytearray()
                    for chunk in resp.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 32_768:
                            return None
            parsed = json.loads(raw.decode("utf-8"))
            content = str(parsed["choices"][0]["message"]["content"] or "").strip()
            clean = re.sub(r"\s+", " ", content)[:900]
            return clean if clean else None
        except Exception:
            return None

    def _analyze_via_stateless_codex(
        self,
        data_uri: str,
        prompt_nl: str,
    ) -> tuple[str, int | None, int | None, int | None]:
        assert self._credential_loader is not None
        is_gpt62 = self._codex_model.startswith("gpt-6.2")
        user_msg: dict[str, Any] = {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt_nl},
                {"type": "input_image", "image_url": data_uri},
            ],
        }
        input_msgs: list[dict[str, Any]] = (
            [
                {
                    "type": "message",
                    "role": "developer",
                    "content": _VISUAL_PERCEPTION_INSTRUCTIONS,
                },
                user_msg,
            ]
            if is_gpt62
            else [user_msg]
        )
        req_body: dict[str, Any] = {
            "model": self._codex_model,
            "instructions": (
                "Volg de leidende developer-instructies nauwkeurig."
                if is_gpt62
                else _VISUAL_PERCEPTION_INSTRUCTIONS
            ),
            "store": False,
            "stream": True,
            "parallel_tool_calls": False,
            "tool_choice": "none",
            "tools": [],
            "reasoning": {"effort": "low"},
            "prompt_cache_key": "eva-holostage-vision-v2",
            "input": input_msgs,
        }
        deltas: list[str] = []
        in_tok: int | None = None
        out_tok: int | None = None
        tot_tok: int | None = None

        try:
            access_token, account_id = self._credential_loader()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "OpenAI-Account-ID": account_id,
                "OpenAI-Beta": "responses=experimental",
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
                "Origin": "https://chatgpt.com",
                "User-Agent": "codex_cli_rs/0.172.0 (Eva)",
                "originator": "codex_cli_rs",
                "session-id": "eva-holostage-vision-v2",
                "Content-Type": "application/json",
            }
            with httpx2.Client(
                transport=self._http_transport,
                timeout=httpx2.Timeout(22.0, connect=6.0),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream("POST", self._codex_endpoint, headers=headers, json=req_body) as resp:
                    if resp.status_code == 429:
                        raise HoloStageVisionError(
                            "rate_limited",
                            "De beeldanalyse wordt tijdelijk begrensd; probeer het over enkele seconden opnieuw.",
                        )
                    if resp.status_code in (401, 403):
                        raise HoloStageVisionError(
                            "credentials",
                            "De modeltoegang voor beeldanalyse is verlopen of geweigerd.",
                        )
                    if resp.status_code != 200:
                        raise HoloStageVisionError(
                            "upstream",
                            f"Beeldanalyse mislukt (HTTP {resp.status_code}).",
                        )

                    pending = bytearray()
                    total_read = 0
                    for chunk in resp.iter_bytes():
                        total_read += len(chunk)
                        if total_read > MAX_VISION_SSE_BYTES:
                            raise HoloStageVisionError(
                                "response_too_large",
                                "Het antwoord van de beeldanalyse overschreed de veiligheidsgrens.",
                            )
                        pending.extend(chunk)
                        while True:
                            nl = pending.find(b"\n")
                            if nl < 0:
                                break
                            line = bytes(pending[:nl]).rstrip(b"\r")
                            del pending[: nl + 1]
                            if not line.startswith(b"data: "):
                                continue
                            raw_ev = line[6:]
                            if raw_ev == b"[DONE]":
                                continue
                            ev = json.loads(raw_ev.decode("utf-8"))
                            if not isinstance(ev, dict):
                                continue
                            ev_type = ev.get("type")
                            if ev_type == "response.output_text.delta":
                                delta = ev.get("delta")
                                if isinstance(delta, str) and delta:
                                    deltas.append(delta)
                            elif ev_type == "response.completed":
                                resp_obj = ev.get("response")
                                usage = resp_obj.get("usage") if isinstance(resp_obj, dict) else None
                                if isinstance(usage, dict):
                                    it = usage.get("input_tokens")
                                    ot = usage.get("output_tokens")
                                    tt = usage.get("total_tokens")
                                    if isinstance(it, int) and not isinstance(it, bool):
                                        in_tok = it
                                    if isinstance(ot, int) and not isinstance(ot, bool):
                                        out_tok = ot
                                    if isinstance(tt, int) and not isinstance(tt, bool):
                                        tot_tok = tt
                            elif ev_type in ("error", "response.failed"):
                                raise HoloStageVisionError(
                                    "upstream",
                                    "De beeldanalyse kon niet worden voltooid.",
                                )
        except HoloStageVisionError:
            raise
        except httpx2.TimeoutException:
            raise HoloStageVisionError(
                "deadline",
                "Het analyseren van het camerabeeld duurde te lang.",
            ) from None
        except Exception:
            raise HoloStageVisionError(
                "unavailable",
                "Beeldanalyse is tijdelijk niet bereikbaar.",
            ) from None

        summary = re.sub(r"\s+", " ", "".join(deltas)).strip()[:950]
        if not summary:
            raise HoloStageVisionError(
                "empty",
                "Eva kon geen duidelijk beeld afleiden uit deze opname.",
            )
        return summary, in_tok, out_tok, tot_tok


__all__ = [
    "HoloStageVisionBridge",
    "HoloStageVisionError",
    "MAX_VISION_IMAGE_BYTES",
    "VisualPerceptionReceipt",
    "sanitize_visual_frame_bytes",
]
