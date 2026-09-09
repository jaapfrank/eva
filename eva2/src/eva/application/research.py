"""Bounded, source-backed research artifacts for Eva's Living Core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Protocol
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class ResearchSource:
    """One cited web source returned by the model provider."""

    title: str
    url: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if (
            not self.title.strip()
            or len(self.title) > 500
            or parsed.scheme != "https"
            or not parsed.hostname
            or len(self.url) > 2_048
        ):
            raise ValueError("research source is invalid")


@dataclass(frozen=True, slots=True)
class ResearchArtifact:
    """A reopenable Markdown artifact bound to its cited source set."""

    title: str
    question: str
    content: str
    sources: tuple[ResearchSource, ...]
    researched_at: str
    provider: str

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > 200:
            raise ValueError("research title is invalid")
        if not self.question.strip() or len(self.question) > 2_000:
            raise ValueError("research question is invalid")
        if not self.content.strip() or len(self.content.encode("utf-8")) > 262_144:
            raise ValueError("research content is invalid")
        if len(self.sources) < 1:
            raise ValueError("research requires at least one cited source")
        if len({source.url for source in self.sources}) != len(self.sources):
            raise ValueError("research sources must be unique")
        if not self.provider.strip():
            raise ValueError("research provider is required")
        try:
            datetime.strptime(self.researched_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError("research timestamp must be canonical UTC") from error

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class CurrentResearchPort(Protocol):
    def research_current_options(
        self,
        *,
        question: str,
        title: str,
        options: tuple[str, ...],
        researched_at: str,
    ) -> ResearchArtifact: ...


__all__ = ["CurrentResearchPort", "ResearchArtifact", "ResearchSource"]
