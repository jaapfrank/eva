"""Canonical whole-second UTC timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def require_utc_second(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole-second precision")


def require_utc_now_second(value: datetime) -> None:
    require_utc_second(value, "now")


def parse_utc_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def format_utc_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_required_utc_timestamp(value: datetime) -> str:
    require_utc_second(value, "timestamp")
    return format_utc_timestamp(value)


def format_normalized_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
