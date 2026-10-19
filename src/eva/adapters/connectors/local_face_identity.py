"""Optional local enrolled-face similarity; never authentication or enrollment.

The legacy thresholds are reused for compatibility, not calibrated probabilities.
Only a confirmed human-enrolled profile may supply a candidate label. Image bytes
and embeddings stay inside this transport and never appear in observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import struct
from urllib.parse import urlsplit

import httpx2 as httpx

from eva.adapters.connectors.home_assistant_camera import CameraSnapshot


CONTRACT = "EvaLocalFaceObservation/v1"
EVIDENCE_KIND = "face_similarity_candidate_not_authentication"
MAX_FACES = 8
MAX_RESPONSE_BYTES = 524_288


def local_http_endpoint(endpoint: str, *, loopback: bool = False) -> str:
    parsed = urlsplit(endpoint)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        local = address.is_loopback if loopback else (
            address.is_loopback or any(address in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")))
    except ValueError:
        local = parsed.hostname == "localhost"
    if (parsed.scheme != "http" or not local or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("face endpoint must be an explicit local HTTP origin")
    return endpoint.rstrip("/")


def _label(value: object) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 120
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid enrolled face label")
    return value


def _vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != 512:
        raise ValueError("face vector must contain 512 finite values")
    if any(type(part) not in (int, float) or not math.isfinite(part) for part in value):
        raise ValueError("face vector must contain 512 finite values")
    result = tuple(float(part) for part in value)
    norm = math.sqrt(sum(part * part for part in result))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("invalid face vector norm")
    return tuple(part / norm for part in result)


@dataclass(frozen=True, slots=True)
class EnrolledFaceProfile:
    person_label: str = field(repr=False)
    vector: tuple[float, ...] = field(repr=False)
    quality: float | None

    def __post_init__(self) -> None:
        _label(self.person_label)
        object.__setattr__(self, "vector", _vector(self.vector))
        if self.quality is not None and (type(self.quality) not in (int, float)
                                        or not math.isfinite(self.quality)):
            raise ValueError("invalid enrolled face quality")


def read_enrolled_profiles(path: str | Path) -> tuple[tuple[EnrolledFaceProfile, ...], str]:
    """Read the existing ArcFace rows, including WAL, without changing its schema."""
    # The HTTP-only container client has no SQLite dependency. Profiles are
    # opened exclusively by the optional host bridge.
    import sqlite3
    resolved = Path(path).resolve(strict=True)
    try:
        with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                "SELECT id,person,vec,quality FROM faces "
                "WHERE backend='arcface' AND coalesce(active,1)!=0 ORDER BY id LIMIT 4097"
            ).fetchall()
    except sqlite3.Error:
        raise RuntimeError("enrolled face profiles are unavailable") from None
    if len(rows) > 4096:
        raise ValueError("enrolled profile set exceeds the reader bound")
    profiles = []
    digest = hashlib.sha256()
    for identifier, person, blob, quality in rows:
        if not isinstance(blob, bytes) or len(blob) != 2048:
            raise ValueError("enrolled profile encoding differs from ArcFace float32")
        profile = EnrolledFaceProfile(person, struct.unpack("<512f", blob), quality)
        profiles.append(profile)
        digest.update(json.dumps([identifier, person, quality], ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode())
        digest.update(blob)
    return tuple(profiles), digest.hexdigest()


def match_face(vector: object, profiles: tuple[EnrolledFaceProfile, ...], *, area: int) -> dict:
    normalized = _vector(vector)
    if type(area) is not int or area < 0:
        raise ValueError("invalid detected face area")
    if area < 700 or not profiles:
        return {"status": "unknown"}
    ranked = []
    for profile in profiles:
        cosine = max(-1.0, min(1.0, sum(a*b for a, b in zip(normalized, profile.vector))))
        quality = 0.45 if profile.quality is None else max(0.25, min(1.0, profile.quality))
        ranked.append((cosine * (0.85 + 0.3 * quality), cosine, profile))
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, cosine, candidate = ranked[0]
    runner = next((row for row in ranked[1:]
                   if row[2].person_label != candidate.person_label), None)
    margin = 1.0 if runner is None else cosine - runner[1]
    threshold = 0.38 if candidate.quality is None else (0.30 if candidate.quality >= 0.5 else 0.33)
    if cosine < threshold or (cosine < 0.45 and margin < 0.065):
        return {"status": "unknown"}
    # Integers preserve the sensory canonical-JSON contract. These are scaled
    # cosine scores, never probabilities or authority to access private data.
    return {"status": "known_candidate", "person_label": candidate.person_label,
            "similarity_ppm": round(cosine * 1_000_000),
            "margin_ppm": round(margin * 1_000_000)}


def face_observation(snapshot: CameraSnapshot, result: dict,
                     profiles: tuple[EnrolledFaceProfile, ...], *,
                     profile_revision: str, backend_revision: str) -> dict:
    detections = result.get("gezichten") if isinstance(result, dict) else None
    if not isinstance(detections, list) or len(detections) > MAX_FACES:
        raise ValueError("local face detections exceed the supported bound")
    faces = []
    for detection in detections:
        if not isinstance(detection, dict):
            raise ValueError("invalid local face detection")
        bbox = detection.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(value) is not int or not 0 <= value <= 100_000 for value in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
            raise ValueError("invalid detected face region")
        face = match_face(detection.get("embedding"), profiles, area=detection.get("oppervlak"))
        faces.append({"region": bbox, **face})
    payload = {"contract": CONTRACT, "image_sha256": snapshot.sha256,
               "analyzed_at": datetime.now(UTC).isoformat(), "backend": "arcface",
               "backend_revision": backend_revision, "profile_revision": profile_revision,
               "evidence_kind": EVIDENCE_KIND, "faces": faces}
    return validate_face_observation(payload, snapshot.sha256, backend_revision)


def validate_face_observation(payload: object, image_sha256: str, revision: str) -> dict:
    expected = {"contract", "image_sha256", "analyzed_at", "backend", "backend_revision",
                "profile_revision", "evidence_kind", "faces"}
    if (not isinstance(payload, dict) or set(payload) != expected
            or payload.get("contract") != CONTRACT or payload.get("image_sha256") != image_sha256
            or payload.get("backend") != "arcface" or payload.get("backend_revision") != revision
            or payload.get("evidence_kind") != EVIDENCE_KIND
            or not isinstance(payload.get("profile_revision"), str)
            or re.fullmatch(r"[a-f0-9]{64}", payload["profile_revision"]) is None):
        raise ValueError("face observation provenance differs from the configured source")
    try:
        timestamp = datetime.fromisoformat(payload["analyzed_at"])
        if timestamp.tzinfo is None:
            raise ValueError("missing timezone")
    except (ValueError, TypeError):
        raise ValueError("invalid face observation time") from None
    faces = payload.get("faces")
    if not isinstance(faces, list) or len(faces) > MAX_FACES:
        raise ValueError("invalid face observation count")
    for face in faces:
        if not isinstance(face, dict):
            raise ValueError("invalid face observation")
        if face.get("status") == "unknown":
            if set(face) != {"region", "status"}:
                raise ValueError("unknown face must not disclose a rejected candidate")
        elif face.get("status") == "known_candidate":
            if set(face) != {"region", "status", "person_label", "similarity_ppm", "margin_ppm"}:
                raise ValueError("invalid face candidate fields")
            _label(face["person_label"])
            for key, lower, upper in (("similarity_ppm", -1_000_000, 1_000_000),
                                      ("margin_ppm", -2_000_000, 2_000_000)):
                value = face[key]
                if type(value) is not int or not lower <= value <= upper:
                    raise ValueError("invalid face candidate similarity")
        else:
            raise ValueError("invalid face observation status")
        region = face.get("region")
        if (not isinstance(region, list) or len(region) != 4
                or any(type(value) is not int or not 0 <= value <= 100_000 for value in region)
                or region[2] <= region[0] or region[3] <= region[1]):
            raise ValueError("invalid face observation region")
    return payload


class LocalFaceIdentityHttpAnalyzer:
    def __init__(self, *, endpoint: str, token: str, revision: str):
        self.endpoint = local_http_endpoint(endpoint)
        if not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("face bridge bearer token is required")
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise ValueError("face backend revision is required")
        self._token = token
        self.revision = revision

    def analyze(self, snapshot: CameraSnapshot) -> dict:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", self.endpoint + "/analyze", content=snapshot.data,
                    headers={"Authorization": "Bearer " + self._token,
                             "Content-Type": snapshot.content_type,
                             "X-Image-SHA256": snapshot.sha256}) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"local face bridge returned HTTP {response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 16_384:
                        raise RuntimeError("local face observation exceeds limit")
        return validate_face_observation(json.loads(body), snapshot.sha256, self.revision)
