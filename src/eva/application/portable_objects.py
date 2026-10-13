"""Pure portable-object hand-off types shared by restore and CAS adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eva.application.objects import CasInstallStatus


@dataclass(frozen=True, slots=True)
class MaterializedPortableObject:
    """Non-secret proof of one independently authenticated target CAS write."""

    object_id: str
    ciphertext_sha256: str
    record_row_sha256: str
    status: CasInstallStatus
    target_object_root: Path
    relative_path: Path


__all__ = ["MaterializedPortableObject"]
