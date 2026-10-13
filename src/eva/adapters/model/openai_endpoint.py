"""Shared validation for Eva's sealed OpenAI Responses endpoints."""

from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit


def responses_base_url(endpoint: str, *, invalid_error: str) -> str:
    parsed = urlsplit(endpoint)
    loopback_http = False
    if parsed.scheme == "http" and parsed.hostname is not None:
        try:
            loopback_http = ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or (parsed.scheme == "http" and not loopback_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/responses")
    ):
        raise ValueError(invalid_error)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path[: -len("responses")], "", "")
    )
