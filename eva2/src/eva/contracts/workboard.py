"""Field-minimized, read-only wire contract for Eva's durable workboard."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from eva.contracts.base import ShortText, Timestamp, Version1Contract


class WorkBoardUsage(Version1Contract):
    contract: Literal["EvaWorkBoardUsage/v1"]
    measurement: Literal["measured", "partial", "unmeasured"]
    input_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=2_000_000)
    cost_usd_micros: int | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def _unknown_is_never_zero(self) -> WorkBoardUsage:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cost_usd_micros,
        )
        if self.measurement == "unmeasured" and any(
            value is not None for value in values
        ):
            raise ValueError("unmeasured workboard usage cannot carry values")
        if self.measurement == "measured" and any(value is None for value in values):
            raise ValueError("measured workboard usage must carry every value")
        if self.measurement == "partial" and not any(
            value is not None for value in values
        ):
            raise ValueError("partial workboard usage must carry a value")
        if all(value is not None for value in values[:3]):
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("workboard token counters differ")
        elif any(value is not None for value in values[:3]):
            raise ValueError("workboard token counters must stay together")
        return self


class WorkBoardCard(Version1Contract):
    contract: Literal["EvaWorkBoardCard/v1"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=240)
    column: Literal["todo", "doing", "attention", "review", "done"]
    worker_name: str = Field(min_length=1, max_length=32)
    worker_role: str = Field(min_length=1, max_length=80)
    worker_health: Literal[
        "waiting", "active", "contact_lost", "finished", "stopped"
    ]
    attempt: int = Field(ge=0, le=3)
    maximum_attempts: int = Field(ge=1, le=3)
    deadline: Timestamp
    duration_seconds: int = Field(ge=0, le=31_536_000)
    overdue: bool
    usage: WorkBoardUsage
    result_status: Literal[
        "not_received",
        "awaiting_review",
        "approved",
        "failed",
        "ambiguous",
        "stopped",
    ]
    reason: ShortText
    changed: Literal[
        "just_now", "minutes_ago", "hours_ago", "days_ago", "unknown"
    ]

    @model_validator(mode="after")
    def _column_and_result_are_honest(self) -> WorkBoardCard:
        allowed = {
            "not_received": {"todo", "doing"},
            "awaiting_review": {"review"},
            "approved": {"done"},
            "failed": {"attention"},
            "ambiguous": {"attention"},
            "stopped": {"done"},
        }
        if self.column not in allowed[self.result_status]:
            raise ValueError("workboard result and column differ")
        if self.attempt > self.maximum_attempts:
            raise ValueError("workboard attempt exceeds its maximum")
        return self


class WorkBoard(Version1Contract):
    contract: Literal["EvaWorkBoard/v1"]
    read_only: Literal[True]
    status: Literal["ready", "unavailable"]
    cards: list[WorkBoardCard] = Field(max_length=64)
    truncated: bool
    message: ShortText

    @model_validator(mode="after")
    def _unavailable_board_has_no_partial_cards(self) -> WorkBoard:
        if self.status == "unavailable" and self.cards:
            raise ValueError("unavailable workboard cannot expose partial cards")
        if self.status == "unavailable" and self.truncated:
            raise ValueError("unavailable workboard cannot claim hidden cards")
        return self


__all__ = ["WorkBoard", "WorkBoardCard", "WorkBoardUsage"]
