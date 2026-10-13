"""Bounded DBBench development adapter without Docker access in the worker.

AgentBench's DBBench task normally asks AgentRL's Docker driver to create a
database for every sample.  That gives the task worker Docker API authority
and inherits an upstream 32-GiB MySQL setting.  This development-only adapter
keeps the upstream task and evaluator unchanged, but receives one separately
started, disposable MySQL endpoint through a manual controller.  It is never
an official score configuration: a scored run must use the upstream environment
or publish an explicit comparability exception.
"""

from __future__ import annotations

import uuid
from typing import Optional, Union

from agentrl.worker.environment._base import EnvironmentController
from src.server.tasks.dbbench.task import DBBenchTask


class _ExternalMySqlEnvironmentController(EnvironmentController):
    """Lease labels for one caller-owned disposable MySQL endpoint only."""

    def __init__(self, delegation: object, host: str) -> None:
        super().__init__(delegation)  # type: ignore[arg-type]
        if not host or "://" in host or "/" in host or any(char.isspace() for char in host):
            raise ValueError("external DBBench MySQL host is invalid")
        self._host = host

    async def start_session(
        self, subtypes: Union[list[str], str], immutable: bool = True, **_unused: object
    ) -> tuple[str, dict[str, str], dict[str, str]]:
        requested = [subtypes] if isinstance(subtypes, str) else subtypes
        if requested != ["mysql"] or not immutable:
            raise ValueError("external DBBench environment only admits immutable MySQL sessions")
        session_id = f"external-dbbench-{uuid.uuid4()}"
        return session_id, {"mysql": session_id}, {"mysql": self._host}

    async def renew_session(self, session_id: str) -> None:
        if not session_id.startswith("external-dbbench-"):
            raise ValueError("external DBBench session is invalid")

    async def end_session(self, session_id: str) -> None:
        if not session_id.startswith("external-dbbench-"):
            raise ValueError("external DBBench session is invalid")


class ExternalMySqlDbBenchTask(DBBenchTask):
    """Upstream DBBench task with one caller-provisioned disposable MySQL host."""

    def __init__(
        self,
        *,
        external_mysql_host: str,
        env_driver: str = "manual",
        env_options: Optional[dict] = None,
        **configs: object,
    ) -> None:
        if env_driver != "manual" or env_options not in (None, {}):
            raise ValueError("external DBBench task only accepts its fixed manual environment")
        super().__init__(env_driver="manual", env_options={"urls": {"mysql": external_mysql_host}}, **configs)
        self.env_controller = _ExternalMySqlEnvironmentController(self.env_delegation, external_mysql_host)
