"""Closed runtime resource budget derived from the local installer authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UNKNOWN_COST_POLICY = "block_autonomous_spend"


class RuntimeResourceBudgetError(ValueError):
    """The runtime budget artifact is incomplete, stale or outside safe bounds."""


def _scope(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 240
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeResourceBudgetError(f"runtime budget {label} is invalid")
    return value


def _integer(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise RuntimeResourceBudgetError(
            f"runtime budget {label} is outside the safe range"
        )
    return value


@dataclass(frozen=True, slots=True)
class RuntimeResourceBudget:
    """One installation-scoped copy of the owner's confirmed resource limits."""

    tenant_id: str
    installation_id: str
    source_budget_sha256: str
    recall_context_tokens: int
    retrieval_nodes: int
    max_input_tokens: int
    max_output_tokens: int
    profile_context_tokens: int
    memory_records_per_person: int
    memory_bytes_per_person: int
    memory_records_per_group: int
    memory_bytes_per_group: int
    memory_records_per_project: int
    memory_bytes_per_project: int
    memory_records_per_installation: int
    memory_bytes_per_installation: int
    retention_days: int
    background_reflections_per_person_day: int
    background_reflections_per_installation_day: int
    external_monthly_spend_cap_eur_cents: int
    unknown_cost_policy: str = _UNKNOWN_COST_POLICY

    def __post_init__(self) -> None:
        _scope(self.tenant_id, label="tenant")
        _scope(self.installation_id, label="installation")
        if not _SHA256_RE.fullmatch(self.source_budget_sha256):
            raise RuntimeResourceBudgetError("runtime budget source hash is invalid")
        if self.unknown_cost_policy != _UNKNOWN_COST_POLICY:
            raise RuntimeResourceBudgetError("runtime budget cost policy differs")
        bounds = (
            (self.recall_context_tokens, "recall context", 2_000, 4_000),
            (self.retrieval_nodes, "retrieval nodes", 20, 100),
            (self.max_input_tokens, "input tokens", 4_000, 16_000),
            (self.max_output_tokens, "output tokens", 100, 1_200),
            (self.profile_context_tokens, "profile context", 100, 1_000),
            (self.memory_records_per_person, "memory records", 100, 10_000),
            (
                self.memory_bytes_per_person,
                "memory bytes",
                1_048_576,
                104_857_600,
            ),
            (self.memory_records_per_group, "group memory records", 0, 100_000),
            (
                self.memory_bytes_per_group,
                "group memory bytes",
                0,
                1_073_741_824,
            ),
            (
                self.memory_records_per_project,
                "project memory records",
                0,
                100_000,
            ),
            (
                self.memory_bytes_per_project,
                "project memory bytes",
                0,
                1_073_741_824,
            ),
            (
                self.memory_records_per_installation,
                "installation memory records",
                100,
                1_000_000,
            ),
            (
                self.memory_bytes_per_installation,
                "installation memory bytes",
                1_048_576,
                10_737_418_240,
            ),
            (self.retention_days, "retention days", 7, 3_650),
            (
                self.background_reflections_per_person_day,
                "person reflection count",
                0,
                100,
            ),
            (
                self.background_reflections_per_installation_day,
                "installation reflection count",
                0,
                1_000,
            ),
            (
                self.external_monthly_spend_cap_eur_cents,
                "external spend cap",
                0,
                1_000_000,
            ),
        )
        for value, label, minimum, maximum in bounds:
            _integer(value, label=label, minimum=minimum, maximum=maximum)
        for records, bytes_, label in (
            (
                self.memory_records_per_group,
                self.memory_bytes_per_group,
                "group",
            ),
            (
                self.memory_records_per_project,
                self.memory_bytes_per_project,
                "project",
            ),
        ):
            if (records == 0) != (bytes_ == 0):
                raise RuntimeResourceBudgetError(
                    f"runtime {label} memory records and bytes must both be zero or non-zero"
                )
        if self.memory_records_per_installation < max(
            self.memory_records_per_person,
            self.memory_records_per_group,
            self.memory_records_per_project,
        ):
            raise RuntimeResourceBudgetError(
                "runtime installation memory record budget is smaller than a scope budget"
            )
        if self.memory_bytes_per_installation < max(
            self.memory_bytes_per_person,
            self.memory_bytes_per_group,
            self.memory_bytes_per_project,
        ):
            raise RuntimeResourceBudgetError(
                "runtime installation memory byte budget is smaller than a scope budget"
            )
        if (
            self.background_reflections_per_installation_day
            < self.background_reflections_per_person_day
        ):
            raise RuntimeResourceBudgetError(
                "runtime installation reflection budget is smaller than person budget"
            )
        if not hmac.compare_digest(
            self.source_budget_sha256,
            hashlib.sha256(canonical_json_bytes(self.budget_payload())).hexdigest(),
        ):
            raise RuntimeResourceBudgetError("runtime budget source hash differs")

    def budget_payload(self) -> dict[str, object]:
        return {
            "background_reflections_per_installation_day": self.background_reflections_per_installation_day,
            "background_reflections_per_person_day": self.background_reflections_per_person_day,
            "contract": "EvaInstallerBudget/v2",
            "external_monthly_spend_cap_eur_cents": self.external_monthly_spend_cap_eur_cents,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "memory_bytes_per_person": self.memory_bytes_per_person,
            "memory_bytes_per_group": self.memory_bytes_per_group,
            "memory_bytes_per_installation": self.memory_bytes_per_installation,
            "memory_bytes_per_project": self.memory_bytes_per_project,
            "memory_records_per_person": self.memory_records_per_person,
            "memory_records_per_group": self.memory_records_per_group,
            "memory_records_per_installation": self.memory_records_per_installation,
            "memory_records_per_project": self.memory_records_per_project,
            "profile_context_tokens": self.profile_context_tokens,
            "recall_context_tokens": self.recall_context_tokens,
            "retention_days": self.retention_days,
            "retrieval_nodes": self.retrieval_nodes,
            "unknown_cost_policy": self.unknown_cost_policy,
        }

    def to_payload(self) -> dict[str, object]:
        return {
            "budget": self.budget_payload(),
            "contract": "EvaRuntimeResourceBudget/v2",
            "installation_id": self.installation_id,
            "source_budget_sha256": self.source_budget_sha256,
            "tenant_id": self.tenant_id,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_payload())

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        *,
        expected_tenant_id: str,
        expected_installation_id: str,
    ) -> "RuntimeResourceBudget":
        try:
            payload = require_canonical_bytes(raw)
        except ValueError as error:
            raise RuntimeResourceBudgetError("runtime budget artifact is invalid") from error
        if set(payload) != {
            "budget",
            "contract",
            "installation_id",
            "source_budget_sha256",
            "tenant_id",
        } or payload.get("contract") != "EvaRuntimeResourceBudget/v2":
            raise RuntimeResourceBudgetError("runtime budget artifact is invalid")
        budget = payload.get("budget")
        if not isinstance(budget, dict) or set(budget) != {
            "background_reflections_per_installation_day",
            "background_reflections_per_person_day",
            "contract",
            "external_monthly_spend_cap_eur_cents",
            "max_input_tokens",
            "max_output_tokens",
            "memory_bytes_per_person",
            "memory_bytes_per_group",
            "memory_bytes_per_installation",
            "memory_bytes_per_project",
            "memory_records_per_person",
            "memory_records_per_group",
            "memory_records_per_installation",
            "memory_records_per_project",
            "profile_context_tokens",
            "recall_context_tokens",
            "retention_days",
            "retrieval_nodes",
            "unknown_cost_policy",
        } or budget.get("contract") != "EvaInstallerBudget/v2":
            raise RuntimeResourceBudgetError("runtime budget values are invalid")
        tenant_id = _scope(payload.get("tenant_id"), label="tenant")
        installation_id = _scope(
            payload.get("installation_id"), label="installation"
        )
        if tenant_id != expected_tenant_id or installation_id != expected_installation_id:
            raise RuntimeResourceBudgetError("runtime budget scope differs")
        source_hash = payload.get("source_budget_sha256")
        if not isinstance(source_hash, str):
            raise RuntimeResourceBudgetError("runtime budget source hash is invalid")
        return cls(
            tenant_id=tenant_id,
            installation_id=installation_id,
            source_budget_sha256=source_hash,
            recall_context_tokens=budget["recall_context_tokens"],  # type: ignore[arg-type]
            retrieval_nodes=budget["retrieval_nodes"],  # type: ignore[arg-type]
            max_input_tokens=budget["max_input_tokens"],  # type: ignore[arg-type]
            max_output_tokens=budget["max_output_tokens"],  # type: ignore[arg-type]
            profile_context_tokens=budget["profile_context_tokens"],  # type: ignore[arg-type]
            memory_records_per_person=budget["memory_records_per_person"],  # type: ignore[arg-type]
            memory_bytes_per_person=budget["memory_bytes_per_person"],  # type: ignore[arg-type]
            memory_records_per_group=budget["memory_records_per_group"],  # type: ignore[arg-type]
            memory_bytes_per_group=budget["memory_bytes_per_group"],  # type: ignore[arg-type]
            memory_records_per_project=budget["memory_records_per_project"],  # type: ignore[arg-type]
            memory_bytes_per_project=budget["memory_bytes_per_project"],  # type: ignore[arg-type]
            memory_records_per_installation=budget["memory_records_per_installation"],  # type: ignore[arg-type]
            memory_bytes_per_installation=budget["memory_bytes_per_installation"],  # type: ignore[arg-type]
            retention_days=budget["retention_days"],  # type: ignore[arg-type]
            background_reflections_per_person_day=budget[
                "background_reflections_per_person_day"
            ],  # type: ignore[arg-type]
            background_reflections_per_installation_day=budget[
                "background_reflections_per_installation_day"
            ],  # type: ignore[arg-type]
            external_monthly_spend_cap_eur_cents=budget[
                "external_monthly_spend_cap_eur_cents"
            ],  # type: ignore[arg-type]
            unknown_cost_policy=budget["unknown_cost_policy"],  # type: ignore[arg-type]
        )


__all__ = ["RuntimeResourceBudget", "RuntimeResourceBudgetError"]
