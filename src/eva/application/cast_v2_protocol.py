"""Pure-stdlib Google Cast V2 (`CASTV2_1_0` TLS :8009) stage receiver launcher.

Zero external dependencies (`pychromecast` not required): encodes and decodes
`extensions.api.cast_channel.CastMessage` binary protobuf frames directly over
pinned RFC1918 LAN sockets, strictly gated on `HomeProprioceptionManager`
`consent_status == "approved"`.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import struct
import time
from typing import Any
from urllib.parse import urlsplit

from eva.application.home_proprioception import HomeProprioceptionManager, is_safe_lan_ipv4

NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
NS_DASHCAST = "urn:x-cast:es.offd.dashcast"

DEFAULT_MEDIA_RECEIVER_APP_ID = "CC1AD845"
DASHCAST_WEB_RECEIVER_APP_ID = "84912283"
MAX_CAST_FRAME_BYTES = 65_536
_APP_ID_RE = re.compile(r"^[A-Za-z0-9]{6,12}$")


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _decode_varint(buf: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    pos = offset
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint overflow")
    raise ValueError("truncated varint")


def _encode_len_delimited(field_number: int, data: bytes) -> bytes:
    tag = (field_number << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def encode_cast_v2_frame(
    *,
    source_id: str,
    destination_id: str,
    namespace: str,
    payload: dict[str, Any],
) -> bytes:
    """Serialize a `CastMessage` (`CASTV2_1_0`, UTF-8 payload) with 4-byte big-endian length prefix."""
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = b"".join(
        [
            b"\x08\x00",  # field 1: protocol_version = CASTV2_1_0 (0)
            _encode_len_delimited(2, source_id.encode("utf-8")),
            _encode_len_delimited(3, destination_id.encode("utf-8")),
            _encode_len_delimited(4, namespace.encode("utf-8")),
            b"\x28\x00",  # field 5: payload_type = STRING (0)
            _encode_len_delimited(6, payload_bytes),
        ]
    )
    if len(body) > MAX_CAST_FRAME_BYTES:
        raise ValueError("Cast V2 frame exceeds maximum allowed size")
    return struct.pack(">I", len(body)) + body


def decode_cast_v2_body(body: bytes) -> dict[str, Any]:
    """Decode a `CastMessage` protobuf payload into `{source_id, destination_id, namespace, payload}`."""
    if len(body) > MAX_CAST_FRAME_BYTES:
        raise ValueError("Cast V2 body exceeds maximum allowed size")
    pos = 0
    out: dict[str, Any] = {
        "protocol_version": 0,
        "source_id": "",
        "destination_id": "",
        "namespace": "",
        "payload_type": 0,
        "payload": {},
    }
    while pos < len(body):
        tag, pos = _decode_varint(body, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, pos = _decode_varint(body, pos)
            if field_num == 1:
                out["protocol_version"] = val
            elif field_num == 5:
                out["payload_type"] = val
        elif wire_type == 2:
            length, pos = _decode_varint(body, pos)
            if length < 0 or pos + length > len(body):
                raise ValueError("truncated Cast V2 length-delimited field")
            chunk = body[pos : pos + length]
            pos += length
            if field_num == 2:
                out["source_id"] = chunk.decode("utf-8", errors="replace")
            elif field_num == 3:
                out["destination_id"] = chunk.decode("utf-8", errors="replace")
            elif field_num == 4:
                out["namespace"] = chunk.decode("utf-8", errors="replace")
            elif field_num == 6:
                txt = chunk.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(txt)
                    out["payload"] = parsed if isinstance(parsed, dict) else {"raw": txt[:256]}
                except Exception:
                    out["payload"] = {"raw": txt[:256]}
        else:
            raise ValueError(f"unsupported Cast V2 protobuf wire type {wire_type}")
    return out


def validate_stage_cast_url(url: str) -> str:
    """Validate that a stage URL sent to a Cast receiver is clean HoloStage HTTP(S) without query credentials."""
    if not isinstance(url, str) or not 10 <= len(url) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ValueError("Ongeldige HoloStage Cast-URL")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("HoloStage Cast-URL moet http:// of https:// zijn zonder inloggegevens in de URL")
    if parts.path.rstrip("/") != "/internal/v1/dashboard/holostage":
        raise ValueError("HoloStage Cast-URL mag uitsluitend naar /internal/v1/dashboard/holostage verwijzen")
    lowered_query = (parts.query or "").lower()
    if any(
        secret_key in lowered_query
        for secret_key in ("token=", "secret=", "password=", "authorization=", "key=", "stg_")
    ):
        raise ValueError("Geheime tokens mogen nooit in een Cast-queryparameter staan (gebruik #fragment pairing)")
    if parts.fragment and not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", parts.fragment):
        raise ValueError("Ongeldig HoloStage Cast-fragment")
    return url


def _recv_exact(sock: ssl.SSLSocket, count: int, deadline: float) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("Cast V2 sessie overschreed totale tijdslimiet")
        sock.settimeout(max(0.05, remaining))
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError("Cast V2 verbinding voortijdig gesloten")
        buf.extend(chunk)
    return bytes(buf)


def _read_cast_frame(sock: ssl.SSLSocket, deadline: float) -> dict[str, Any]:
    hdr = _recv_exact(sock, 4, deadline)
    (length,) = struct.unpack(">I", hdr)
    if length <= 0 or length > MAX_CAST_FRAME_BYTES:
        raise ValueError("Ongeldige Cast V2 framelengte")
    return decode_cast_v2_body(_recv_exact(sock, length, deadline))


def query_or_launch_approved_cast_device(
    proprioception: HomeProprioceptionManager,
    *,
    device_id: str,
    stage_url: str | None = None,
    app_id: str | None = None,
    timeout_seconds: float = 4.5,
) -> dict[str, Any]:
    """Connect to an approved LAN Google Cast device on :8009, read receiver status, and optionally launch stage."""
    effective_app_id = app_id or (
        DASHCAST_WEB_RECEIVER_APP_ID if stage_url else DEFAULT_MEDIA_RECEIVER_APP_ID
    )
    st = proprioception.status()
    dev = next(
        (d for d in (st.get("devices") or []) if isinstance(d, dict) and d.get("device_id") == device_id),
        None,
    )
    if dev is None:
        raise ValueError("Cast-apparaat niet gevonden in ontdekte thuisapparaten")
    if dev.get("consent_status") != "approved":
        raise PermissionError("Keur dit Cast-/Google TV-apparaat eerst goed op het dashboard voordat Eva ernaar cast")

    approved_ip = str(dev.get("approved_ip") or "").strip()
    if not approved_ip:
        raise PermissionError("Cast-apparaat mist een vastgepind goedgekeurd IP-adres; keur het apparaat opnieuw goed")
    live_ip = str(dev.get("ip") or dev.get("ip_address") or "").strip()
    if live_ip and live_ip != approved_ip:
        raise PermissionError("IP-adres van Cast-apparaat wijkt af van goedgekeurd adres; herbevestig goedkeuring")
    host = approved_ip
    if not is_safe_lan_ipv4(host, allow_docker_bridge=False):
        raise ValueError("Cast-apparaat heeft geen veilig lokaal RFC1918 IPv4-adres")

    dev_port = dev.get("approved_port") or dev.get("port")
    ports = [int(p) for p in (dev.get("open_ports") or []) if isinstance(p, int)]
    if isinstance(dev_port, int):
        ports.append(dev_port)
    proto_blob = " ".join(
        [
            str(dev.get("kind") or ""),
            str(dev.get("protocol_family") or ""),
            " ".join(str(x) for x in (dev.get("protocols") or [])),
            " ".join(str(x) for x in (dev.get("capabilities") or [])),
        ]
    ).lower()
    if 8009 not in ports and "cast" not in proto_blob:
        raise ValueError("Gekozen apparaat ondersteunt geen Google Cast (:8009)")

    if not _APP_ID_RE.match(effective_app_id):
        raise ValueError("Ongeldige Cast Receiver App-ID")
    clean_url = validate_stage_cast_url(stage_url) if stage_url else None

    deadline = time.monotonic() + max(0.2, float(timeout_seconds))

    # Google Cast devices use self-signed device TLS certificates on local LAN port 8009
    tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_ctx.check_hostname = False
    tls_ctx.verify_mode = ssl.CERT_NONE

    conn_timeout = max(0.05, deadline - time.monotonic())
    with socket.create_connection((host, 8009), timeout=conn_timeout) as raw_sock:
        raw_sock.settimeout(max(0.05, deadline - time.monotonic()))
        with tls_ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            tls_sock.settimeout(max(0.05, deadline - time.monotonic()))
            tls_sock.sendall(
                encode_cast_v2_frame(
                    source_id="sender-0",
                    destination_id="receiver-0",
                    namespace=NS_CONNECTION,
                    payload={"type": "CONNECT", "origin": {}},
                )
            )
            tls_sock.sendall(
                encode_cast_v2_frame(
                    source_id="sender-0",
                    destination_id="receiver-0",
                    namespace=NS_RECEIVER,
                    payload={"type": "GET_STATUS", "requestId": 1},
                )
            )

            receiver_status: dict[str, Any] = {}
            for _ in range(6):
                msg = _read_cast_frame(tls_sock, deadline)
                ns = msg.get("namespace")
                pl = msg.get("payload") or {}
                if ns == NS_HEARTBEAT and pl.get("type") == "PING":
                    tls_sock.sendall(
                        encode_cast_v2_frame(
                            source_id="sender-0",
                            destination_id="receiver-0",
                            namespace=NS_HEARTBEAT,
                            payload={"type": "PONG"},
                        )
                    )
                    continue
                if ns == NS_RECEIVER and pl.get("type") == "RECEIVER_STATUS":
                    receiver_status = pl.get("status") if isinstance(pl.get("status"), dict) else {}
                    break

            launched_transport_id: str | None = None
            if clean_url is not None:
                tls_sock.sendall(
                    encode_cast_v2_frame(
                        source_id="sender-0",
                        destination_id="receiver-0",
                        namespace=NS_RECEIVER,
                        payload={"type": "LAUNCH", "appId": effective_app_id, "requestId": 2},
                    )
                )
                for _ in range(10):
                    msg = _read_cast_frame(tls_sock, deadline)
                    ns = msg.get("namespace")
                    pl = msg.get("payload") or {}
                    if ns == NS_HEARTBEAT and pl.get("type") == "PING":
                        tls_sock.sendall(
                            encode_cast_v2_frame(
                                source_id="sender-0",
                                destination_id="receiver-0",
                                namespace=NS_HEARTBEAT,
                                payload={"type": "PONG"},
                            )
                        )
                        continue
                    if ns == NS_RECEIVER and pl.get("type") == "RECEIVER_STATUS":
                        st_obj = pl.get("status") if isinstance(pl.get("status"), dict) else {}
                        receiver_status = st_obj
                        apps = st_obj.get("applications") if isinstance(st_obj.get("applications"), list) else []
                        for app_row in apps:
                            if (
                                isinstance(app_row, dict)
                                and app_row.get("transportId")
                                and str(app_row.get("appId") or "") == effective_app_id
                            ):
                                launched_transport_id = str(app_row["transportId"])
                                break
                        if launched_transport_id:
                            break

                if launched_transport_id:
                    tls_sock.sendall(
                        encode_cast_v2_frame(
                            source_id="sender-0",
                            destination_id=launched_transport_id,
                            namespace=NS_CONNECTION,
                            payload={"type": "CONNECT", "origin": {}},
                        )
                    )
                    if effective_app_id == DASHCAST_WEB_RECEIVER_APP_ID:
                        tls_sock.sendall(
                            encode_cast_v2_frame(
                                source_id="sender-0",
                                destination_id=launched_transport_id,
                                namespace=NS_DASHCAST,
                                payload={"url": clean_url, "force": True},
                            )
                        )
                    else:
                        tls_sock.sendall(
                            encode_cast_v2_frame(
                                source_id="sender-0",
                                destination_id=launched_transport_id,
                                namespace=NS_MEDIA,
                                payload={
                                    "type": "LOAD",
                                    "requestId": 3,
                                    "autoplay": True,
                                    "media": {
                                        "contentId": clean_url,
                                        "streamType": "LIVE",
                                        "contentType": "text/html",
                                        "metadata": {
                                            "metadataType": 0,
                                            "title": "Eva HoloStage",
                                            "subtitle": "Gedeeld 3D huiskamerpodium",
                                        },
                                    },
                                },
                            )
                        )

    apps_summary = []
    for app_row in (receiver_status.get("applications") or [])[:4]:
        if isinstance(app_row, dict):
            apps_summary.append(
                {
                    "app_id": str(app_row.get("appId") or ""),
                    "display_name": str(app_row.get("displayName") or "")[:64],
                }
            )
    vol_obj = receiver_status.get("volume") if isinstance(receiver_status.get("volume"), dict) else {}
    return {
        "contract": "EvaCastStageResult/v1",
        "device_id": device_id,
        "label": str(dev.get("display_name") or dev.get("label") or device_id),
        "room_id": str(dev.get("room_id") or "woonkamer"),
        "app_id": effective_app_id,
        "launched": bool(clean_url is not None and launched_transport_id),
        "active_applications": apps_summary,
        "volume_level": round(float(vol_obj.get("level", 0.5)), 2) if isinstance(vol_obj.get("level"), (int, float)) else None,
        "muted": bool(vol_obj.get("muted", False)),
    }


__all__ = [
    "DASHCAST_WEB_RECEIVER_APP_ID",
    "DEFAULT_MEDIA_RECEIVER_APP_ID",
    "NS_DASHCAST",
    "decode_cast_v2_body",
    "encode_cast_v2_frame",
    "query_or_launch_approved_cast_device",
    "validate_stage_cast_url",
]
