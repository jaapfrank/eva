"""Opaque owner token shape, shared without importing an HTTP framework."""
_MINIMUM_TOKEN_LENGTH = 32
_MAXIMUM_TOKEN_LENGTH = 1024


def validate_dashboard_access_token(value: str) -> str:
    """Accept one bounded opaque token without logging or normalising it."""

    if not isinstance(value, str) or not (_MINIMUM_TOKEN_LENGTH <= len(value) <= _MAXIMUM_TOKEN_LENGTH):
        raise ValueError("dashboard access token has an invalid length")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError("dashboard access token contains whitespace or controls")
    return value

