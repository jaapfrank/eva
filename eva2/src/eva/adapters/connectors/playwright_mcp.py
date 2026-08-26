"""One sealed, credential-free Playwright MCP page-read workflow.

Playwright MCP is the browser engine.  This adapter deliberately is not a
generic browser agent: it can only navigate to the release-owned URL and take
one accessibility snapshot in the same isolated MCP session.  Web text is
treated as untrusted data and can never select another tool or URL.
"""

from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from eva.contracts.canonical_json import canonical_sha256

from .mcp import (
    McpReadOnlyError,
    McpStreamableHttpSession,
)


PLAYWRIGHT_MCP_VERSION = "0.0.79"
PLAYWRIGHT_SERVER_VERSION = "1.63.0-alpha-2026-08-05"
PLAYWRIGHT_MCP_AMD64_DIGEST = (
    "sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9"
)
PLAYWRIGHT_NAVIGATE_TOOL_SHA256 = (
    "4bd2f88138e75bb639e57c781bacc3465723eff08879f84976b76d21a4a66295"
)
PLAYWRIGHT_SNAPSHOT_TOOL_SHA256 = (
    "b4df87ac5b5691cf8ac04cad340509c5fd93496996ae4a1d3499553ae6103b68"
)
_MAX_REQUIRED_TEXTS = 8


class PlaywrightMcpError(RuntimeError):
    """The fixed browser read could not be proved safely."""


@dataclass(frozen=True, slots=True)
class PlaywrightMcpReadProfile:
    """Release-owned inputs for exactly one page-read proof."""

    endpoint: str
    page_url: str
    expected_title: str
    required_visible_texts: tuple[str, ...]
    expected_snapshot_sha256: str
    server_image_digest: str
    sandbox_profile_sha256: str
    timeout_milliseconds: int = 15_000
    maximum_request_bytes: int = 8_192
    maximum_response_bytes: int = 262_144
    snapshot_depth: int = 8

    def __post_init__(self) -> None:
        _require_http_url(self.endpoint, label="Playwright MCP endpoint")
        _require_http_url(self.page_url, label="admitted page URL")
        if not self.expected_title or len(self.expected_title) > 200:
            raise ValueError("expected browser title must contain 1-200 characters")
        if "\n" in self.expected_title or "\r" in self.expected_title:
            raise ValueError("expected browser title must use one line")
        if not 1 <= len(self.required_visible_texts) <= _MAX_REQUIRED_TEXTS:
            raise ValueError("browser proof requires 1-8 visible texts")
        if len(set(self.required_visible_texts)) != len(self.required_visible_texts):
            raise ValueError("browser proof visible texts must be unique")
        for text in self.required_visible_texts:
            if not text or len(text) > 500 or "\x00" in text:
                raise ValueError("browser proof visible text is invalid")
        _require_sha256(self.expected_snapshot_sha256, label="snapshot hash")
        if self.server_image_digest != PLAYWRIGHT_MCP_AMD64_DIGEST:
            raise ValueError("browser image is not the admitted Playwright MCP release")
        _require_sha256(self.sandbox_profile_sha256, label="sandbox profile hash")
        if not 1_000 <= self.timeout_milliseconds <= 30_000:
            raise ValueError("browser deadline must be between 1 and 30 seconds")
        if not 1_024 <= self.maximum_request_bytes <= 65_536:
            raise ValueError("browser request-byte budget is invalid")
        if not 8_192 <= self.maximum_response_bytes <= 1_048_576:
            raise ValueError("browser response-byte budget is invalid")
        if not 1 <= self.snapshot_depth <= 16:
            raise ValueError("browser snapshot depth must be between 1 and 16")


@dataclass(frozen=True, slots=True)
class PlaywrightMcpReadResult:
    """Sanitized proof that the exact page state was observed."""

    final_url: str
    title: str
    matched_visible_texts: tuple[str, ...]
    snapshot_sha256: str
    navigate_result_sha256: str
    tool_sequence: tuple[str, ...]
    server_image_digest: str
    sandbox_profile_sha256: str


class PlaywrightMcpReadAdapter:
    """Navigate once and verify one snapshot through the pinned sidecar."""

    def __init__(self, profile: PlaywrightMcpReadProfile) -> None:
        self._profile = profile
        self._opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}), _NoRedirect()
        )

    def read_fixed_page(self) -> PlaywrightMcpReadResult:
        deadline = time.monotonic() + self._profile.timeout_milliseconds / 1_000
        session = McpStreamableHttpSession(
            endpoint=self._profile.endpoint,
            opener=self._opener,
            deadline=deadline,
            maximum_request_bytes=self._profile.maximum_request_bytes,
            maximum_response_bytes=self._profile.maximum_response_bytes,
            response_progress_timeout_seconds=5.0,
        )
        try:
            initialized = session.request(
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "eva2-playwright-read", "version": "1"},
                },
                initialization=True,
            )
            _require_initialize(initialized)
            _require_server(initialized)
            session.complete_initialization(initialized)
            session.notify_initialized()
            tools = session.request(request_id=2, method="tools/list", params={})
            _require_tools(tools)
            navigate = session.request(
                request_id=3,
                method="tools/call",
                params={
                    "name": "browser_navigate",
                    "arguments": {"url": self._profile.page_url},
                },
            )
            navigate_text = _require_text_result(navigate, stage="navigation")
            navigate_url, navigate_title = _parse_page(navigate_text)
            _require_expected_page(
                navigate_url,
                navigate_title,
                profile=self._profile,
                stage="navigation",
            )
            snapshot = session.request(
                request_id=4,
                method="tools/call",
                params={
                    "name": "browser_snapshot",
                    "arguments": {"depth": self._profile.snapshot_depth},
                },
            )
            snapshot_text = _require_text_result(snapshot, stage="snapshot")
            final_url, final_title = _parse_page(snapshot_text)
            _require_expected_page(
                final_url,
                final_title,
                profile=self._profile,
                stage="snapshot",
            )
            snapshot_sha256 = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
            if snapshot_sha256 != self._profile.expected_snapshot_sha256:
                raise PlaywrightMcpError("browser snapshot differs from its sealed proof")
            if any(text not in snapshot_text for text in self._profile.required_visible_texts):
                raise PlaywrightMcpError("browser snapshot lacks required visible content")
        except PlaywrightMcpError:
            raise
        except McpReadOnlyError as exc:
            raise PlaywrightMcpError("Playwright MCP exchange failed safely") from exc
        except (OSError, socket.timeout, TimeoutError, TypeError, ValueError) as exc:
            raise PlaywrightMcpError("Playwright page read failed safely") from exc

        return PlaywrightMcpReadResult(
            final_url=final_url,
            title=final_title,
            matched_visible_texts=self._profile.required_visible_texts,
            snapshot_sha256=snapshot_sha256,
            navigate_result_sha256=hashlib.sha256(
                navigate_text.encode("utf-8")
            ).hexdigest(),
            tool_sequence=("browser_navigate", "browser_snapshot"),
            server_image_digest=self._profile.server_image_digest,
            sandbox_profile_sha256=self._profile.sandbox_profile_sha256,
        )


def _require_server(result: object) -> None:
    if not isinstance(result, dict):
        raise PlaywrightMcpError("Playwright MCP initialization is invalid")
    server = result.get("serverInfo")
    if not isinstance(server, dict) or server != {
        "name": "Playwright",
        "version": PLAYWRIGHT_SERVER_VERSION,
    }:
        raise PlaywrightMcpError("Playwright MCP server release drifted")


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _require_initialize(result: object) -> None:
    if not isinstance(result, dict) or result.get("protocolVersion") != "2025-06-18":
        raise PlaywrightMcpError("Playwright MCP protocol negotiation drifted")
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict) or not isinstance(
        capabilities.get("tools"), dict
    ):
        raise PlaywrightMcpError("Playwright MCP lacks tool capability")


def _require_tools(result: object) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        raise PlaywrightMcpError("Playwright MCP tool catalog is invalid")
    tools = result["tools"]
    expected = {
        "browser_navigate": PLAYWRIGHT_NAVIGATE_TOOL_SHA256,
        "browser_snapshot": PLAYWRIGHT_SNAPSHOT_TOOL_SHA256,
    }
    for name, expected_sha256 in expected.items():
        matches = [
            tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("name") == name
        ]
        if len(matches) != 1 or canonical_sha256(matches[0]) != expected_sha256:
            raise PlaywrightMcpError(f"Playwright MCP tool {name} drifted")


def _require_text_result(result: object, *, stage: str) -> str:
    if not isinstance(result, dict) or result.get("isError") is True:
        raise PlaywrightMcpError(f"browser {stage} reported an execution error")
    if set(result) - {"content", "isError"}:
        raise PlaywrightMcpError(f"browser {stage} returned unsupported fields")
    content = result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or set(content[0]) != {"type", "text"}
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise PlaywrightMcpError(f"browser {stage} returned unsupported content")
    return content[0]["text"]


def _parse_page(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) < 4 or lines[0] != "### Page":
        raise PlaywrightMcpError("browser result lacks a closed page header")
    url_prefix = "- Page URL: "
    title_prefix = "- Page Title: "
    if not lines[1].startswith(url_prefix) or not lines[2].startswith(title_prefix):
        raise PlaywrightMcpError("browser result page header is malformed")
    if lines[3] != "### Snapshot":
        raise PlaywrightMcpError("browser result lacks its snapshot marker")
    final_url = lines[1][len(url_prefix) :]
    title = lines[2][len(title_prefix) :]
    _require_http_url(final_url, label="observed page URL")
    if not title or len(title) > 200:
        raise PlaywrightMcpError("browser result title is invalid")
    return final_url, title


def _require_expected_page(
    url: str,
    title: str,
    *,
    profile: PlaywrightMcpReadProfile,
    stage: str,
) -> None:
    if url != profile.page_url:
        raise PlaywrightMcpError(f"browser {stage} escaped its admitted URL")
    if title != profile.expected_title:
        raise PlaywrightMcpError(f"browser {stage} title differs from its proof")


def _require_http_url(value: str, *, label: str) -> None:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError(f"{label} is invalid")
    parsed = urllib_parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{label} is outside the closed HTTP profile")
    if urllib_parse.urlunsplit(parsed) != value:
        raise ValueError(f"{label} is not canonical")


def _require_sha256(value: str, *, label: str) -> None:
    candidate = value.removeprefix("sha256:")
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"{label} must be lowercase sha256")


__all__ = [
    "PLAYWRIGHT_MCP_AMD64_DIGEST",
    "PLAYWRIGHT_MCP_VERSION",
    "PLAYWRIGHT_NAVIGATE_TOOL_SHA256",
    "PLAYWRIGHT_SERVER_VERSION",
    "PLAYWRIGHT_SNAPSHOT_TOOL_SHA256",
    "PlaywrightMcpError",
    "PlaywrightMcpReadAdapter",
    "PlaywrightMcpReadProfile",
    "PlaywrightMcpReadResult",
]
