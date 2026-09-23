"""Host-selected, source-bound profile for private digital background work."""
from dataclasses import dataclass
from datetime import UTC
import hashlib
from pathlib import Path
import re

from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkGrant, DelegatedDigitalWorkSpec


DIGITAL_PROFILE_ID = "worker:digital-work:v4"
DIGITAL_OUTPUT_SHA256 = canonical_sha256({
    "contract": "EvaDigitalWorkOutput/v4", "format": "grounded_conversation_reply",
    "tool_evidence": "indexed_verified_connector_receipts", "semantic_verification": False,
})
_ROOT = Path(__file__).resolve().parents[1]
_SOURCES = (
    "contracts/delegated_work.py", "contracts/delegated_digital_work.py",
    "bootstrap/delegated_digital_profile.py", "application/background_execution.py",
    "application/conversation.py", "application/cancellation.py",
    "adapters/postgres/background_model_journal.py",
    "adapters/postgres/conversation_journal.py", "adapters/postgres/conversation_actions.py",
    "adapters/postgres/external_conversation_tools.py", "adapters/postgres/delegated_work.py",
    "operations/delegated_work_worker.py",
)


@dataclass(frozen=True, slots=True)
class DelegatedDigitalProfile:
    """Configuration comes from the admitted host route, never model-selected URLs."""
    model_profile_id: str
    configuration_sha256: str
    engine_release_sha256: str
    capability_ids: tuple[str, ...]
    execution_source_sha256: tuple[tuple[str, str], ...]
    maximum_model_runs: int = 8
    maximum_completed_model_responses: int = 8
    maximum_tool_calls: int = 6
    maximum_input_tokens: int = 65_536
    maximum_output_tokens: int = 4_000
    timeout_seconds: int = 300
    maximum_cost_usd_micros: int | None = None

    def __post_init__(self):
        if (not isinstance(self.model_profile_id, str) or not self.model_profile_id.strip()
                or not re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", self.engine_release_sha256)):
            raise ValueError("digital profile requires explicit host model and engine releases")
        if not isinstance(self.capability_ids, tuple) or not isinstance(self.execution_source_sha256, tuple):
            raise ValueError("digital profile collections must be immutable")
        if not self.execution_source_sha256:
            raise ValueError("digital profile must pin its execution implementation")
        names = []
        for name, digest in self.execution_source_sha256:
            path = Path(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name
                    or not name.endswith(".py") or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("digital execution source pin is invalid")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("digital execution source pins must be unique")
        self.grant()

    def grant(self):
        return DelegatedDigitalWorkGrant(
            contract="DelegatedDigitalWorkGrant/v4", schema_version=4,
            context_refs=[], capability_ids=list(self.capability_ids), workspace_ref=None,
            child_network_access=False, delegation_depth=1, maximum_claims_before_call_start=2,
            maximum_model_runs=self.maximum_model_runs,
            maximum_completed_model_responses=self.maximum_completed_model_responses,
            maximum_tool_calls=self.maximum_tool_calls, maximum_input_tokens=self.maximum_input_tokens,
            maximum_output_tokens=self.maximum_output_tokens,
            maximum_total_tokens=self.maximum_input_tokens + self.maximum_output_tokens,
            maximum_cost_usd_micros=self.maximum_cost_usd_micros, timeout_seconds=self.timeout_seconds,
        )

    @property
    def release(self):
        sources = {name: hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() for name in _SOURCES}
        for name, expected in self.execution_source_sha256:
            actual = hashlib.sha256((_ROOT / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("digital execution source differs from host release")
            sources[name] = actual
        return DelegatedWorkReleaseSelector(
            canonical_sha256({"profile": DIGITAL_PROFILE_ID, "grant": self.grant().to_payload()}),
            self.configuration_sha256,
            canonical_sha256({"contract": "EvaDigitalWorkProgram/v4", "sources": sources}),
            self.engine_release_sha256, DIGITAL_OUTPUT_SHA256,
        )

    def build_spec(self, *, source, source_ingress_sha256, **identity):
        def stamp(value):
            if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
                raise ValueError("digital work timestamps require aware whole seconds")
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        created, deadline = identity.pop("created_at"), identity.pop("deadline")
        release = self.release
        if "execution_id" not in identity:
            if identity.get("generation") != 1:
                raise ValueError("digital resume must retain the initial execution identity")
            identity["execution_id"] = identity["work_id"]
        return DelegatedDigitalWorkSpec(
            contract="DelegatedDigitalWork/v4", schema_version=4, audience="private",
            worker_name="digital-worker", worker_profile_id=DIGITAL_PROFILE_ID,
            worker_profile_release_sha256=release.worker_profile_release_sha256,
            model_profile_id=self.model_profile_id, model_profile_release_sha256=self.configuration_sha256,
            worker_program_sha256=release.worker_program_sha256,
            engine_release_sha256=self.engine_release_sha256, output_schema_sha256=DIGITAL_OUTPUT_SHA256,
            grant=self.grant().to_payload(), source=source, source_ingress_sha256=source_ingress_sha256,
            created_at=stamp(created), deadline=stamp(deadline), **identity,
        )
