"""Configure the official MCP Server once and capture a sanitized read-only probe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import stat
import time
from typing import Any
from urllib import error, parse, request as urllib_request

from candidate import (
    AssistToolAdmission,
    HomeAssistantOfficialMcpClient,
    HomeAssistantOfficialMcpConfig,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--configure", action="store_true")
    return parser.parse_args()


class _HomeAssistantConfigApi:
    def __init__(self, base_url: str, token: str) -> None:
        parsed = parse.urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError("Home Assistant configuration URL is invalid")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}), _NoRedirect()
        )

    def entries(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/config/config_entries/entry")
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise RuntimeError("Home Assistant configuration inventory is invalid")
        return payload

    def create_mcp_server(self) -> str:
        started = self._request(
            "POST",
            "/api/config/config_entries/flow",
            {"handler": "mcp_server"},
        )
        if (
            not isinstance(started, dict)
            or started.get("type") != "form"
            or started.get("step_id") != "user"
            or not isinstance(started.get("flow_id"), str)
        ):
            raise RuntimeError("Home Assistant MCP configuration flow did not start")
        flow_id = started["flow_id"]
        try:
            finished = self._request(
                "POST",
                f"/api/config/config_entries/flow/{parse.quote(flow_id, safe='')}",
                {"llm_hass_api": ["assist"]},
            )
        except Exception:
            self._request(
                "DELETE",
                f"/api/config/config_entries/flow/{parse.quote(flow_id, safe='')}",
            )
            raise
        if not isinstance(finished, dict) or finished.get("type") != "create_entry":
            raise RuntimeError("Home Assistant MCP configuration flow did not finish")
        result = finished.get("result")
        entry_id = result.get("entry_id") if isinstance(result, dict) else None
        if not isinstance(entry_id, str) or not entry_id:
            entries = self.mcp_entries()
            if len(entries) != 1 or not isinstance(entries[0].get("entry_id"), str):
                raise RuntimeError("Home Assistant MCP configuration entry is ambiguous")
            entry_id = entries[0]["entry_id"]
        return entry_id

    def mcp_entries(self) -> list[dict[str, Any]]:
        return [entry for entry in self.entries() if entry.get("domain") == "mcp_server"]

    def delete_entry(self, entry_id: str) -> None:
        result = self._request(
            "DELETE",
            f"/api/config/config_entries/entry/{parse.quote(entry_id, safe='')}",
        )
        if not isinstance(result, dict):
            raise RuntimeError("Home Assistant MCP rollback returned no receipt")

    def _request(self, method: str, path: str, payload: object = None) -> object:
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib_request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "HA-Frontend-Base": self._base_url,
            },
        )
        try:
            with self._opener.open(request, timeout=8) as response:  # noqa: S310
                raw = response.read(1024 * 1024 + 1)
        except error.HTTPError as exc:
            raise RuntimeError(
                f"Home Assistant configuration request failed with HTTP {exc.code}"
            ) from exc
        if len(raw) > 1024 * 1024:
            raise RuntimeError("Home Assistant configuration response is too large")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Home Assistant configuration response is not JSON"
            ) from exc


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


def _private_token(path: Path) -> str:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or details.st_nlink != 1:
        raise RuntimeError("Home Assistant token file is not a private regular file")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise RuntimeError("Home Assistant token file is accessible by other users")
    raw = path.read_bytes()
    if not 16 <= len(raw) <= 4_097:
        raise RuntimeError("Home Assistant token file size is invalid")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("Home Assistant token file is invalid") from exc
    if not token or any(character.isspace() for character in token):
        raise RuntimeError("Home Assistant token is invalid")
    return token


def main() -> None:
    arguments = _arguments()
    token = _private_token(arguments.token_file)
    api = _HomeAssistantConfigApi(arguments.base_url, token)
    existing = api.mcp_entries()
    if len(existing) > 1:
        raise RuntimeError("Several Home Assistant MCP Server entries already exist")
    created_entry_id: str | None = None
    if not existing:
        if not arguments.configure:
            raise RuntimeError("Home Assistant MCP Server is not configured")
        created_entry_id = api.create_mcp_server()
    entry = api.mcp_entries()
    if len(entry) != 1 or not isinstance(entry[0].get("entry_id"), str):
        if created_entry_id is not None:
            api.delete_entry(created_entry_id)
        raise RuntimeError("Home Assistant MCP Server entry did not become unique")
    entry_id = entry[0]["entry_id"]
    try:
        client = HomeAssistantOfficialMcpClient(
            HomeAssistantOfficialMcpConfig(arguments.base_url, token)
        )
        last_error: Exception | None = None
        for _attempt in range(20):
            try:
                catalog = client.catalog()
                break
            except Exception as exc:  # endpoint setup can finish just after entry creation
                last_error = exc
                time.sleep(0.25)
        else:
            assert last_error is not None
            raise last_error
        context_tool = catalog.exact("GetLiveContext")
        context = client.call(
            admission=AssistToolAdmission(
                "GetLiveContext",
                "read",
                context_tool.input_schema_sha256,
            ),
            arguments={},
            expected_catalog_sha256=catalog.sha256,
        )
    except Exception:
        if created_entry_id is not None:
            api.delete_entry(created_entry_id)
            if api.mcp_entries():
                raise RuntimeError(
                    "Home Assistant MCP probe failed and rollback was incomplete"
                ) from None
        raise
    output = {
        "catalogSha256": catalog.sha256,
        "configuredByProbe": created_entry_id is not None,
        "entryIdSha256": hashlib.sha256(entry_id.encode()).hexdigest(),
        "getLiveContextInputSchemaSha256": context_tool.input_schema_sha256,
        "getLiveContextResultSha256": context.response_sha256,
        "observedAt": datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "resultStored": False,
        "status": "ready_read_only",
        "toolCount": len(catalog.tools),
    }
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
