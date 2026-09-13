"""Closed host-selected document plan; request identities confer no authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re

from eva.application.durable_goals import DurableGoalError, DurableGoalSpec, GoalArtifact
from eva.contracts.canonical_json import canonical_sha256


_CONTRACT = "EvaDocumentGoalPlan/v2"
_HASH = re.compile(r"[0-9a-f]{64}")
_PATH = re.compile(r"/proof-state/[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:txt|md)")
_MAX_CONTENT_BYTES = 12_000


@dataclass(frozen=True, slots=True)
class PreparedDocumentGoal:
    """Host definition for one chat project; never parsed from tool arguments."""

    goal: DurableGoalSpec
    plan_sha256: str
    conversation_project_id: str | None

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict:
        return {"contract": "EvaPreparedDocumentGoal/v1", "goal": self.goal.to_payload(),
                "plan_sha256": self.plan_sha256,
                "conversation_project_id": self.conversation_project_id}

    @classmethod
    def from_json(cls, raw: str, plan: DocumentGoalPlan) -> PreparedDocumentGoal:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise DurableGoalError("prepared document goal repeats a field")
                result[key] = value
            return result

        try:
            if not isinstance(raw, str) or len(raw.encode()) > 65_536:
                raise ValueError("size")
            payload = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(payload, dict) or set(payload) != {
                "contract", "goal", "plan_sha256", "conversation_project_id"
            } or payload["contract"] != "EvaPreparedDocumentGoal/v1":
                raise ValueError("shape")
            project = payload["conversation_project_id"]
            if project is not None and (
                not isinstance(project, str) or not project.strip() or len(project) > 240
            ):
                raise ValueError("project")
            goal = DurableGoalSpec.from_payload(payload["goal"])
            if (payload["plan_sha256"] != plan.sha256 or goal.sha256 != plan.goal_sha256
                    or goal.goal_id != plan.goal_id or goal.owner_id != plan.owner_principal_id
                    or goal.followup_instruction is None or goal.pivot_instruction is not None):
                raise ValueError("binding")
            return cls(goal, plan.sha256, project)
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise DurableGoalError("prepared document goal shape or binding differs") from error


@dataclass(frozen=True, slots=True)
class DocumentStep:
    operation_id: str
    request_binding_sha256: str
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class DocumentGoalPlan:
    goal_id: str
    goal_sha256: str
    owner_principal_id: str
    tenant_id: str
    installation_id: str
    authority_configuration_sha256: str
    tool_configuration_sha256: str
    first_path: str
    final_path: str
    first_content: str
    final_prefix: str
    final_suffix: str
    second_content: str | None = None

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "second_content" and value is None:
                continue
            if not isinstance(value, str):
                raise DurableGoalError(f"document plan {name} must be text")
            if name.endswith("_sha256") and _HASH.fullmatch(value) is None:
                raise DurableGoalError(f"document plan {name} is invalid")
        for value in (self.goal_id, self.owner_principal_id, self.tenant_id, self.installation_id):
            if not value.strip() or len(value) > 240:
                raise DurableGoalError("document plan identity is invalid")
        if any(_PATH.fullmatch(path) is None for path in (self.first_path, self.final_path)):
            raise DurableGoalError("document plan path is outside the flat private workspace")
        if self.first_path == self.final_path:
            raise DurableGoalError("document steps require distinct output paths")
        for content in (self.first_content, self.second_content if self.second_content is not None else self.final_prefix + self.first_content + self.final_suffix):
            if not content.strip() or len(content.encode()) > _MAX_CONTENT_BYTES:
                raise DurableGoalError("document plan content is outside its byte limit")

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def to_payload(self) -> dict[str, str]:
        payload = asdict(self)
        if self.second_content is None:
            payload.pop("second_content")
        return {"contract": _CONTRACT if self.second_content is None else "EvaDocumentGoalPlan/v3", **payload}

    @classmethod
    def from_json(cls, raw: str) -> DocumentGoalPlan:
        if not isinstance(raw, str) or len(raw.encode()) > 65_536:
            raise DurableGoalError("document plan file is too large")

        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise DurableGoalError("document plan repeats a field")
                result[key] = value
            return result

        try:
            payload = json.loads(raw, object_pairs_hook=unique_fields)
            if not isinstance(payload, dict):
                raise DurableGoalError("document plan contract is invalid")
            contract = payload.pop("contract", None)
            if (contract not in {_CONTRACT, "EvaDocumentGoalPlan/v3"}
                    or (contract == _CONTRACT and "second_content" in payload)
                    or (contract == "EvaDocumentGoalPlan/v3" and not isinstance(payload.get("second_content"), str))):
                raise DurableGoalError("document plan contract is invalid")
            return cls(**payload)
        except (ValueError, TypeError) as error:
            raise DurableGoalError("document plan shape is invalid") from error

    def require_host_binding(
        self,
        goal: DurableGoalSpec,
        *,
        owner_principal_id: str,
        tenant_id: str,
        installation_id: str,
        authority_configuration_sha256: str,
        tool_configuration_sha256: str,
    ) -> None:
        """Compare against host-loaded authority/settings, never request values.

        This is identity/configuration binding only. The provider must separately
        call existing authorize_current immediately before every new effect.
        """
        if (
            goal.goal_id != self.goal_id
            or goal.sha256 != self.goal_sha256
            or goal.owner_id != owner_principal_id
            or goal.followup_instruction is None
            or self.owner_principal_id != owner_principal_id
            or self.tenant_id != tenant_id
            or self.installation_id != installation_id
            or self.authority_configuration_sha256 != authority_configuration_sha256
            or self.tool_configuration_sha256 != tool_configuration_sha256
        ):
            raise DurableGoalError("document plan differs from the host-authorized goal")

    def final_content(self, previous: GoalArtifact) -> str:
        if previous.content != self.first_content:
            raise DurableGoalError("previous document content differs from the sealed plan")
        return self.second_content if self.second_content is not None else self.final_prefix + previous.content + self.final_suffix

    def step(self, number: int, previous: GoalArtifact | None = None) -> DocumentStep:
        if type(number) is not int or number not in (1, 2):
            raise DurableGoalError("document step is outside the two-step plan")
        if (number == 1 and previous is not None) or (number == 2 and previous is None):
            raise DurableGoalError("document step has an invalid previous-artifact binding")
        content = self.first_content if previous is None else self.final_content(previous)
        path = self.first_path if number == 1 else self.final_path
        identity = {"goal_sha256": self.goal_sha256, "plan_sha256": self.sha256, "step": number}
        binding = {
            **identity,
            "tool_configuration_sha256": self.tool_configuration_sha256,
            "path": path,
            "content": content,
            "previous_artifact_sha256": (
                None if previous is None else canonical_sha256(previous.to_payload())
            ),
        }
        return DocumentStep(canonical_sha256(identity), canonical_sha256(binding), path, content)
