from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.operations.portable_restore import (
    PortableRestoreError,
    restore_column_preserving_records,
)


def _terminal_object_reference() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    schedule = json.loads(
        (
            root / "design" / "vertical" / "noor-4d-concurrency-crash-schedules-v1.json"
        ).read_text(encoding="utf-8")
    )
    facts = schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
    return next(
        item["portable_record"]
        for item in facts
        if item["fact_id"] == "correction/object/document"
    )


class _NeverTarget:
    def exec_driver_sql(self, *_: object, **__: object) -> object:
        raise AssertionError("object restore must fail before the target is queried")


def test_object_rows_need_a_verified_cas_materializer_before_any_target_write() -> None:
    records: dict[str, list[dict[str, Any]]] = {
        str(spec["table"]): [] for spec in CANONICAL_EXPORT_SPECS
    }
    records["eva_evidence.object_refs"] = [_terminal_object_reference()]

    with pytest.raises(
        PortableRestoreError, match="verified object CAS materialization"
    ):
        restore_column_preserving_records(
            _NeverTarget(), records, table_specs=CANONICAL_EXPORT_SPECS
        )
