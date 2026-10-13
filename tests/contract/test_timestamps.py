from datetime import UTC, datetime, timedelta, timezone

import pytest

from eva.contracts.timestamps import (
    format_normalized_utc_timestamp,
    format_utc_timestamp,
    parse_utc_timestamp,
    require_utc_now_second,
    require_utc_second,
)


def test_canonical_timestamp_round_trip() -> None:
    value = datetime(2026, 8, 29, 11, 52, 7, tzinfo=UTC)
    assert parse_utc_timestamp(format_utc_timestamp(value)) == value


def test_normalized_timestamp_converts_offset_to_utc() -> None:
    value = datetime(2026, 8, 29, 13, 52, 7, tzinfo=timezone(timedelta(hours=2)))
    assert format_normalized_utc_timestamp(value) == "2026-08-29T11:52:07Z"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (datetime(2026, 8, 29, 11, 52, 7), "occurred_at must be timezone-aware UTC"),
        (
            datetime(2026, 8, 29, 13, 52, 7, tzinfo=timezone(timedelta(hours=2))),
            "occurred_at must be timezone-aware UTC",
        ),
        (
            datetime(2026, 8, 29, 11, 52, 7, 1, tzinfo=UTC),
            "occurred_at must use whole-second precision",
        ),
    ],
)
def test_timestamp_requirement_fails_closed(value: datetime, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        require_utc_second(value, "occurred_at")


def test_now_requirement_preserves_legacy_error_label() -> None:
    with pytest.raises(ValueError, match="^now must be timezone-aware UTC$"):
        require_utc_now_second(datetime(2026, 8, 29, 11, 52, 7))
