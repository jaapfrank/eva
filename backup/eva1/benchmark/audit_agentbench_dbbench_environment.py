#!/usr/bin/env python3
"""Audit DBBench environment structure without emitting questions or answers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


MAX_DATASET_BYTES = 128 * 1024 * 1024
MAX_CASE_BYTES = 8 * 1024 * 1024


class DbBenchEnvironmentAuditError(ValueError):
    """The dataset cannot be projected into a safe environment audit."""


def _schema_digest(tables: list[object]) -> str:
    schema: list[dict[str, object]] = []
    for table in tables:
        if not isinstance(table, dict):
            schema.append({"invalid": True})
            continue
        info = table.get("table_info")
        columns = info.get("columns") if isinstance(info, dict) else None
        names = (
            [column.get("name") if isinstance(column, dict) else None for column in columns]
            if isinstance(columns, list)
            else None
        )
        schema.append({"columnNames": names, "tableName": table.get("table_name")})
    return hashlib.sha256(
        json.dumps(
            schema,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def audit_case(entry: object, *, case_index: int) -> dict[str, object]:
    """Return a content-free structural audit for one already isolated row."""

    if not isinstance(entry, dict):
        raise DbBenchEnvironmentAuditError("case is not an object")
    user_sqlite = entry.get("user_sqlite", False) is True
    raw_tables = entry.get("table")
    tables = raw_tables if isinstance(raw_tables, list) else [raw_tables]
    issues: set[str] = set()
    total_rows = 0
    if not 1 <= len(tables) <= 64:
        issues.add("invalid_table_count")
    for table in tables:
        if not isinstance(table, dict):
            issues.add("invalid_table")
            continue
        name = table.get("table_name")
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 64:
            issues.add("invalid_table_name")
        info = table.get("table_info")
        if not isinstance(info, dict):
            issues.add("invalid_table_info")
            continue
        columns = info.get("columns")
        rows = info.get("rows")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 1024:
            issues.add("invalid_column_count")
            continue
        names: list[str] = []
        for column in columns:
            column_name = column.get("name") if isinstance(column, dict) else None
            if not isinstance(column_name, str):
                issues.add("invalid_column_name")
                continue
            if not column_name:
                issues.add("empty_column_name")
            if "\x00" in column_name or len(column_name.encode("utf-8")) > 64:
                issues.add("invalid_column_name")
            names.append(column_name.casefold())
        if len(set(names)) != len(names):
            issues.add("duplicate_column_name")
        if not isinstance(rows, list):
            issues.add("invalid_rows")
            continue
        total_rows += len(rows)
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            issues.add("row_width_mismatch")
    if user_sqlite:
        create = entry.get("create")
        if not isinstance(create, dict) or not all(
            isinstance(create.get(field), str) and create[field]
            for field in ("database", "init")
        ):
            issues.add("invalid_sqlite_source")
    return {
        "caseIndex": case_index,
        "contract": "EvaAgentBenchDbBenchEnvironmentAudit/v1",
        "issues": sorted(issues),
        "schemaSha256": _schema_digest(tables),
        "tableCount": len(tables),
        "totalRows": total_rows,
        "userSqlite": user_sqlite,
        "valid": not issues,
    }


def audit_dataset(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size > MAX_DATASET_BYTES:
        raise DbBenchEnvironmentAuditError("dataset file is invalid")
    results: list[dict[str, object]] = []
    with path.open("rb") as handle:
        for index, raw in enumerate(handle):
            if not raw.strip() or len(raw) > MAX_CASE_BYTES:
                raise DbBenchEnvironmentAuditError("dataset row is invalid")
            try:
                entry: Any = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DbBenchEnvironmentAuditError("dataset row is not JSON") from error
            results.append(audit_case(entry, case_index=index))
    if not results:
        raise DbBenchEnvironmentAuditError("dataset is empty")
    invalid = [result for result in results if result["valid"] is False]
    return {
        "caseCount": len(results),
        "contract": "EvaAgentBenchDbBenchEnvironmentAuditSet/v1",
        "invalidCaseCount": len(invalid),
        "invalidCases": [
            {"caseIndex": result["caseIndex"], "issues": result["issues"]}
            for result in invalid
        ],
        "validCaseCount": len(results) - len(invalid),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = audit_dataset(args.dataset)
    except DbBenchEnvironmentAuditError as error:
        print(json.dumps({"error": str(error), "status": "failed"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["invalidCaseCount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
