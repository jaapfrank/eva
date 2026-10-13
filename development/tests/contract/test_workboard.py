from __future__ import annotations

import pytest
from eva.contracts.workboard import WorkBoard, WorkBoardCard, WorkBoardUsage


def _usage(measurement: str = "unmeasured") -> WorkBoardUsage:
    values: dict[str, object] = {
        "contract": "EvaWorkBoardUsage/v1",
        "schema_version": 1,
        "measurement": measurement,
    }
    if measurement == "measured":
        values |= {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "cost_usd_micros": 7,
        }
    return WorkBoardUsage.model_validate(values)


def _card(**changes: object) -> WorkBoardCard:
    values: dict[str, object] = {
        "contract": "EvaWorkBoardCard/v1",
        "schema_version": 1,
        "title": "Vergelijk de beste voice-route",
        "summary": "Zet de onderhouden opties naast Eva's eisen.",
        "column": "doing",
        "worker_name": "onderzoeker",
        "worker_role": "Onderzoek",
        "worker_health": "active",
        "attempt": 1,
        "maximum_attempts": 2,
        "deadline": "2026-08-26T16:00:00Z",
        "duration_seconds": 30,
        "overdue": False,
        "usage": _usage().model_dump(mode="json"),
        "result_status": "not_received",
        "reason": "De subagent werkt aan het afgesproken onderzoek.",
        "changed": "just_now",
    }
    values |= changes
    return WorkBoardCard.model_validate(values)


def test_workboard_keeps_unknown_usage_unknown() -> None:
    card = _card()
    assert card.usage.measurement == "unmeasured"
    assert card.usage.total_tokens is None
    board = WorkBoard.model_validate(
        {
            "contract": "EvaWorkBoard/v1",
            "schema_version": 1,
            "read_only": True,
            "status": "ready",
            "cards": [card.model_dump(mode="json")],
            "truncated": False,
            "message": "Eén duurzame taak zichtbaar.",
        }
    )
    assert board.cards == [card]


@pytest.mark.parametrize(
    ("result", "wrong_column"),
    [
        ("awaiting_review", "done"),
        ("approved", "review"),
        ("failed", "done"),
        ("ambiguous", "doing"),
        ("stopped", "attention"),
        ("not_received", "done"),
    ],
)
def test_result_status_cannot_be_shown_in_a_better_column(
    result: str, wrong_column: str
) -> None:
    with pytest.raises(ValueError, match="result and column differ"):
        _card(result_status=result, column=wrong_column)


def test_unavailable_board_cannot_leak_partial_cards() -> None:
    with pytest.raises(ValueError, match="cannot expose partial cards"):
        WorkBoard.model_validate(
            {
                "contract": "EvaWorkBoard/v1",
                "schema_version": 1,
                "read_only": True,
                "status": "unavailable",
                "cards": [_card().model_dump(mode="json")],
                "truncated": False,
                "message": "Niet beschikbaar.",
            }
        )


def test_measured_usage_requires_exact_tokens() -> None:
    assert _usage("measured").total_tokens == 14
    with pytest.raises(ValueError, match="token counters differ"):
        WorkBoardUsage(
            contract="EvaWorkBoardUsage/v1",
            schema_version=1,
            measurement="measured",
            input_tokens=10,
            output_tokens=4,
            total_tokens=15,
            cost_usd_micros=7,
        )
