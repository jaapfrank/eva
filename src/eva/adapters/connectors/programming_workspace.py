"""Task-bound client for the existing host's isolated programming service."""
from dataclasses import dataclass
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx

from eva.application.cancellation import WorkInterrupted, run_cancellable


@dataclass(frozen=True)
class ProgrammingWorkspaceClient:
    url: str
    token_file: str
    timeout_seconds: int = 120

    def _token(self):
        token = Path(self.token_file).read_text().strip()
        if not 32 <= len(token) <= 256 or any(c.isspace() for c in token):
            raise ValueError("programming service credential unavailable")
        return token

    async def _post(self, payload):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=self.timeout_seconds) as client:
            async with client.stream("POST", self.url + "/programming", json=payload,
                    headers={"Authorization": "Bearer " + self._token()}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 256 * 1024:
                        raise ValueError("programming response exceeds bound")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("programming response must be an object")
        return result

    def request(self, session_key, workspace_key, action, **arguments):
        payload = {"session_key": session_key, "workspace_key": workspace_key,
                   "action": action, **arguments}
        try:
            return asyncio.run(run_cancellable(lambda: self._post(payload)))
        except WorkInterrupted:
            # A cancelled HTTP request does not terminate a remote child process.
            # The stop request deliberately runs outside the cancelled scope.
            self.close(session_key)
            raise

    def close(self, session_key):
        return asyncio.run(self._post({"session_key": session_key, "action": "close"}))


def configured_programming_workspace():
    path = os.environ.get("EVA_PROGRAMMING_WORKSPACE_CLIENT_FILE")
    if not path:
        return None
    data = json.loads(Path(path).read_text())
    if (not isinstance(data, dict) or set(data) != {"url", "token_file", "timeout_seconds"}
            or not isinstance(data["url"], str) or not isinstance(data["token_file"], str)
            or not Path(data["token_file"]).is_absolute()
            or type(data["timeout_seconds"]) is not int or not 1 <= data["timeout_seconds"] <= 300):
        raise ValueError("invalid programming client configuration")
    url = urlsplit(data["url"])
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        raise ValueError("invalid programming service endpoint")
    return ProgrammingWorkspaceClient(**data)
