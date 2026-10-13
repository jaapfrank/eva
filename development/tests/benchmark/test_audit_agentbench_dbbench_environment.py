from __future__ import annotations

import json
from pathlib import Path
import sys


BENCHMARK = Path(__file__).resolve().parents[3] / "development/benchmark"
sys.path.insert(0, str(BENCHMARK))
import audit_agentbench_dbbench_environment as audit  # noqa: E402


def _entry(column_name: str = "id") -> dict[str, object]:
    return {
        "description": "private question",
        "label": "private hidden answer",
        "sql": {"query": "private answer SQL"},
        "table": {
            "table_name": "items",
            "table_info": {
                "columns": [{"name": column_name}],
                "rows": [["private row value"]],
            },
        },
        "type": ["counting"],
    }


def test_audit_reports_only_structure_and_hashes() -> None:
    result = audit.audit_case(_entry(), case_index=4)
    rendered = json.dumps(result)

    assert result["valid"] is True
    assert result["totalRows"] == 1
    assert "private" not in rendered
    assert "label" not in rendered
    assert "private answer SQL" not in rendered


def test_audit_rejects_the_upstream_empty_mysql_column() -> None:
    result = audit.audit_case(_entry(""), case_index=5)

    assert result["valid"] is False
    assert result["issues"] == ["empty_column_name"]


def test_dataset_summary_keeps_invalid_case_content_private(tmp_path: Path) -> None:
    dataset = tmp_path / "dev.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(item) for item in (_entry(), _entry(""))) + "\n",
        encoding="utf-8",
    )

    result = audit.audit_dataset(dataset)
    rendered = json.dumps(result)

    assert result == {
        "caseCount": 2,
        "contract": "EvaAgentBenchDbBenchEnvironmentAuditSet/v1",
        "invalidCaseCount": 1,
        "invalidCases": [{"caseIndex": 1, "issues": ["empty_column_name"]}],
        "validCaseCount": 1,
    }
    assert "private" not in rendered
