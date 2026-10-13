"""Small, secret-free-at-rest access boundary for Eva's local cockpit."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import secrets

from fastapi import HTTPException, Request


_BASIC_OWNER = "eva-owner"
_MINIMUM_TOKEN_LENGTH = 32
_MAXIMUM_TOKEN_LENGTH = 1024


def validate_dashboard_access_token(value: str) -> str:
    """Accept one bounded opaque token without logging or normalising it."""

    if not isinstance(value, str) or not (_MINIMUM_TOKEN_LENGTH <= len(value) <= _MAXIMUM_TOKEN_LENGTH):
        raise ValueError("dashboard access token has an invalid length")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError("dashboard access token contains whitespace or controls")
    return value


@dataclass(frozen=True, slots=True)
class DashboardAccess:
    """Optional owner gate; absent only for the local read-only development view."""

    token: str | None = None

    def __post_init__(self) -> None:
        if self.token is not None:
            validate_dashboard_access_token(self.token)

    def require(self, request: Request) -> None:
        if self.token is None:
            return
        candidate = _candidate_from_authorization(request.headers.get("authorization"))
        if candidate is not None and secrets.compare_digest(candidate, self.token):
            return
        raise HTTPException(
            status_code=401,
            detail="dashboard authentication required",
            headers={
                "Cache-Control": "no-store",
                "WWW-Authenticate": 'Basic realm="Eva cockpit", charset="UTF-8"',
            },
        )


def _candidate_from_authorization(raw: str | None) -> str | None:
    if not raw:
        return None
    scheme, separator, value = raw.partition(" ")
    if not separator or not value or value.strip() != value:
        return None
    if scheme.casefold() == "bearer":
        return value
    if scheme.casefold() != "basic":
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator or username != _BASIC_OWNER:
        return None
    return password


__all__ = ["DashboardAccess", "validate_dashboard_access_token"]
