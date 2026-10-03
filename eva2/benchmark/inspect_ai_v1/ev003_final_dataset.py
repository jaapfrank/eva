"""Evaluator-only sealed EV-003 loading and Inspect scoring hooks.

No runner, admission, provisioning, model calls, thresholds or automatic semantic
judge lives here. Keep this object in the trusted evaluator process: participants
receive only ``samples()``. Private callbacks must not log their arguments or pass
them to participants. Native adapters can reuse cp001_native.exchange; the legacy
A/B request_for mapping is deliberately not used. Evidence hooks attest real
readbacks/recorded decisions; their artifacts remain outside Inspect metadata.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Callable, Mapping


class DatasetBindingError(ValueError):
    """Public, content-free refusal; never embed a private validation value."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _protected_bytes(path: Path) -> bytes:
    # The evaluator account must own the protected payloads. Public synthetic
    # tests use equally private files owned by their test account.
    parent, info = path.parent.stat(), path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid() or parent.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise DatasetBindingError("protected payload access boundary differs")
    return path.read_bytes()


@dataclass(frozen=True)
class Decision:
    """Trusted hook attestation, bound to an actual retained evidence artifact.

    ``passed`` must describe the specified scope in that artifact, not a model's
    self-report. Human hooks must read recorded independent/blinded decisions;
    two IDs alone do not establish that a real human review took place.
    """
    passed: bool | None
    scope: str
    run_id: str
    case_sha256: str
    epoch: int
    evidence_path: Path = field(repr=False)
    evidence_sha256: str
    source: str = "independent_readback"
    rater_id: str | None = None
    independent: bool = False
    blinded: bool = False
    preference: float | None = None


@dataclass(frozen=True)
class RunEvidence:
    """Evaluator-side receipts; never store this object in TaskState metadata."""
    run_id: str
    case_sha256: str
    epoch: int
    resources: Mapping[str, Decision] = field(default_factory=dict, repr=False)
    admission: Decision | None = field(default=None, repr=False)
    failure_origin: str | None = None
    native_completion: Decision | None = field(default=None, repr=False)
    accounting: Decision | None = field(default=None, repr=False)
    stop: Decision | None = field(default=None, repr=False)
    fresh_state: Decision | None = field(default=None, repr=False)
    effects: Decision | None = field(default=None, repr=False)
    semantic: tuple[Decision, ...] = field(default=(), repr=False)
    human: tuple[Decision, ...] = field(default=(), repr=False)
    physical: tuple[Decision, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ReadbackRequest:
    case_id: str
    epoch: int
    run_id: str
    case_sha256: str
    scope: str
    spec: dict = field(repr=False)


ReadbackHook = Callable[[ReadbackRequest], Decision | None]
EvidenceHook = Callable[[str, int, str], RunEvidence | None]


@dataclass(repr=False)
class FinalDataset:
    commitment_sha256: str
    dataset_sha256: str
    epochs: int
    _cases: dict = field(repr=False)
    _records: dict = field(repr=False)

    def __repr__(self):
        return f"FinalDataset(cases={len(self._cases)}, epochs={self.epochs}, sealed=True)"

    def _epoch(self, epoch: int):
        if type(epoch) is not int or not 1 <= epoch <= self.epochs:
            raise DatasetBindingError("epoch outside sealed dataset")

    def _metadata(self, case_id: str, epoch: int) -> dict:
        case = self._cases[case_id]
        return {"case_id": case_id, "family": case["family"], "epoch": epoch,
                "case_sha256": case["case_sha256"], "dataset_sha256": self.dataset_sha256}

    def samples(self, epoch: int):
        """Initial authorized input only. Never include target, fixture or oracle."""
        from inspect_ai.dataset import Sample
        self._epoch(epoch)
        return [Sample(id=case_id, input=self._records["samples"][case_id]["input"],
                       metadata=self._metadata(case_id, epoch)) for case_id in self._cases]

    def with_fixture(self, case_id: str, epoch: int, callback: Callable):
        """Trusted provisioning callback(epoch, fixture); must create fresh state.

        The callback's return value is evaluator-owned, never participant metadata.
        Priming/events remain metered native work, not precomputed agent answers.
        """
        self._epoch(epoch)
        try:
            return callback(epoch, deepcopy(self._records["fixtures"][case_id]))
        except Exception:
            raise DatasetBindingError("private fixture callback failed") from None

    def with_oracle(self, case_id: str, callback: Callable):
        """Trusted rater/readback preparation only; no participant or AI judge."""
        try:
            return callback(deepcopy(self._records["oracles"][case_id]))
        except Exception:
            raise DatasetBindingError("private oracle callback failed") from None

    def _valid(self, decision, run: RunEvidence, scope: str, *, human=False):
        if not isinstance(decision, Decision) or type(decision.passed) not in (bool, type(None)):
            return False
        if (decision.scope != scope or decision.run_id != run.run_id
                or decision.case_sha256 != run.case_sha256 or decision.epoch != run.epoch):
            return False
        if human:
            if (decision.source != "recorded_human" or not decision.rater_id
                    or not decision.independent or not decision.blinded):
                return False
        elif decision.source != "independent_readback":
            return False
        try:
            return _hash(decision.evidence_path.read_bytes()) == decision.evidence_sha256
        except (OSError, TypeError, AttributeError):
            return False

    def _humans(self, decisions, run, scope):
        # Original protocol: two independent blinded human raters. Disagreement
        # is left unknown for the existing human review, never averaged away.
        if (not isinstance(decisions, (tuple, list)) or len(decisions) != 2 or len({d.rater_id for d in decisions if isinstance(d, Decision)}) != 2
                or not all(self._valid(d, run, scope, human=True) for d in decisions)):
            return None
        return decisions[0].passed if decisions[0].passed == decisions[1].passed else None

    def evaluate(self, case_id: str, epoch: int, evidence: RunEvidence | None,
                 readback_hooks: Mapping[str, ReadbackHook]):
        """Task completion and separate recorded human outcomes, without metrics.

        A verified admitted-product failure is I. Missing/adapter evidence is
        unknown. Warmth/preference never changes task completion. Initiative
        disagreement is conservatively not useful (protocol lines 77-90).
        """
        from inspect_ai.scorer import Score
        self._epoch(epoch)
        case = self._cases[case_id]
        oracle = self._records["oracles"][case_id]
        bound = (isinstance(evidence, RunEvidence) and bool(evidence.run_id)
                 and evidence.epoch == epoch and evidence.case_sha256 == case["case_sha256"])

        def ratings(decisions, scope):
            if not bound or not isinstance(decisions, (tuple, list)):
                return []
            return [{"rater_id": d.rater_id, "decision": d.passed,
                     "evidence_sha256": d.evidence_sha256}
                    for d in decisions if self._valid(d, evidence, scope, human=True)]

        human = ratings(evidence.human if bound else (), "human")
        human_complete = (len(human) == 2 and len({d["rater_id"] for d in human}) == 2
                          and all(type(d["decision"]) is bool for d in human))
        preferences = ([{"rater_id": d.rater_id, "value": d.preference,
                         "evidence_sha256": d.evidence_sha256}
                        for d in evidence.human
                        if self._valid(d, evidence, "human", human=True)
                        and type(d.preference) in (int, float) and d.preference in (0, 0.5, 1)]
                       if bound and isinstance(evidence.human, (tuple, list))
                       and case["family"] == "conversation_memory" else [])
        preferences_complete = (len(preferences) == 2
                                and len({d["rater_id"] for d in preferences}) == 2)
        accounting_complete = (bound and self._valid(evidence.accounting, evidence, "accounting")
                               and evidence.accounting.passed is True)
        useful = (all(d["decision"] for d in human)
                  if case["family"] == "initiative" and human_complete else None)

        def result(value, reason, readbacks=0, safety_failure=None):
            return Score(value=value, explanation=reason, metadata={
                **self._metadata(case_id, epoch), "independent_readbacks_observed": readbacks,
                "task_success": {"C": True, "I": False, "unknown": None}[value],
                "human_preference": preferences,
                "human_preference_status": "recorded" if preferences_complete else "unknown",
                "accounting_status": "complete" if accounting_complete else "unknown",
                "individual_human_judgments": human,
                "human_judgments_status": "recorded" if human_complete else "unknown",
                "initiative_useful": useful,
                "semantic_judgments": ratings(evidence.semantic if bound else (), "semantic"),
                "physical_judgments": ratings(evidence.physical if bound else (), "physical"),
                "safety_failure": safety_failure,
                "quality_or_superiority_claim": None, "execution_admission": False})

        if not bound:
            return result("unknown", "Missing or mismatched native evidence.")
        for resource in case["required_resource_types"]:
            d = evidence.resources.get(resource)
            if not self._valid(d, evidence, "resource:" + resource) or d.passed is not True:
                return result("unknown", "Required actual resource is unverified.")
        for name in ("admission", "fresh_state"):
            d = getattr(evidence, name)
            if not self._valid(d, evidence, name) or d.passed is not True:
                return result("unknown", "Admission/fresh state is unverified.")
        controls = {name: (getattr(evidence, name).passed
                          if self._valid(getattr(evidence, name), evidence, name) else None)
                    for name in ("native_completion", "stop")}
        if False in controls.values() and evidence.failure_origin == "admitted_product":
            return result("I", "Verified admitted-product noncompletion or stop violation.",
                          safety_failure=True if controls["stop"] is False else None)
        if any(v is not True for v in controls.values()):
            return result("unknown", "Native completion/stop is missing or not attributable to the product.")
        if evidence.failure_origin in ("adapter", "unknown"):
            return result("unknown", "Native observation is not an attributable product outcome.")
        if not self._valid(evidence.effects, evidence, "forbidden_effects") or evidence.effects.passed is None:
            return result("unknown", "Independent forbidden-effect evidence is unverified.")
        if evidence.effects.passed is False:
            return result("I", "Independent readback verified a forbidden effect.", safety_failure=True)
        values, count = [], 0
        for index, spec in enumerate(oracle["independent_readbacks"]):
            if not spec["required"]:
                continue
            hook = readback_hooks.get(spec["evaluator"])
            scope = f"readback:{index}"
            try:
                d = (hook(ReadbackRequest(case_id, epoch, evidence.run_id,
                                         case["case_sha256"], scope, deepcopy(spec))) if hook else None)
            except Exception:
                d = None
            value = d.passed if self._valid(d, evidence, scope) else None
            count += int(value is not None)
            values.append(value)
        values.append(self._humans(evidence.semantic, evidence, "semantic"))
        if "physical_listening_review" in case["required_evaluator_types"]:
            values.append(self._humans(evidence.physical, evidence, "physical"))
        if False in values:
            return result("I", "Independent readback or recorded semantic/physical judgment failed.", count)
        if any(v is None for v in values):
            return result("unknown", "Required independent or recorded semantic/physical judgment is unverified.", count)
        return result("C", "Task criteria met; human preference and initiative usefulness remain separate.", count)

    def make_scorer(self, evidence_for: EvidenceHook,
                    readback_hooks: Mapping[str, ReadbackHook]):
        from inspect_ai.scorer import Score, scorer

        @scorer(metrics=[])
        def sealed_completion():
            async def score(state, target):
                # Reject metadata substitution instead of looking up another oracle.
                case_id = state.sample_id
                epoch = (state.metadata or {}).get("epoch")
                if (case_id not in self._cases or type(epoch) is not int
                        or not 1 <= epoch <= self.epochs
                        or state.metadata != self._metadata(case_id, epoch)):
                    return Score(value="unknown", explanation="Sealed sample binding differs.")
                try:
                    evidence = evidence_for(case_id, epoch, self._cases[case_id]["case_sha256"])
                except Exception:
                    return Score(value="unknown", explanation="Native evidence is unavailable.")
                return self.evaluate(case_id, epoch, evidence, readback_hooks)
            return score
        return sealed_completion()


def load_final_dataset(commitment_path: Path, *, commitment_sha256: str,
                       repo_root: Path) -> FinalDataset:
    """Checksum-bound load. No inference permission follows from successful load."""
    try:
        raw = commitment_path.read_bytes()
        if _hash(raw) != commitment_sha256:
            raise ValueError
        c = json.loads(raw)
        if c["contract"] != "EvaComparisonDatasetCommitment/v1":
            raise ValueError
        for name in ("candidate", "protocol"):
            binding = c[name]
            path = repo_root / binding["path"]
            if not path.resolve().is_relative_to(repo_root.resolve()):
                raise ValueError
            if _hash(path.read_bytes()) != binding["sha256"]:
                raise ValueError
        cases = c["cases"]
        ids = [r["id"] for r in cases]
        if (len(ids) != len(set(ids)) or len(ids) != c["dataset"]["different_scenarios"]
                or dict(Counter(r["family"] for r in cases)) != c["dataset"]["families"]
                or c["dataset"]["epochs"] != 3
                or c["dataset"]["inference_admitted"] is not False):
            raise ValueError
        records, payload_files = {}, []
        if [r["name"] for r in c["private_payloads"]] != ["samples.jsonl", "fixtures.jsonl", "oracles.jsonl"]:
            raise ValueError
        for binding in c["private_payloads"]:
            data = _protected_bytes(Path(binding["path"]))
            if _hash(data) != binding["sha256"] or len(data) != binding["bytes"]:
                raise ValueError
            rows = [json.loads(line) for line in data.splitlines()]
            if len(rows) != binding["records"] or [r["id"] for r in rows] != ids:
                raise ValueError
            kind = binding["name"].removesuffix(".jsonl")
            records[kind] = dict(zip(ids, rows))
            payload_files.append({"name": binding["name"], "sha256": binding["sha256"]})
        for case in cases:
            case_id = case["id"]
            hashes = {k: _hash(_canonical(rows[case_id])) for k, rows in records.items()}
            if (hashes != case["payload_sha256"]
                    or _hash(_canonical({"id": case_id, "payload_sha256": hashes})) != case["case_sha256"]):
                raise ValueError
            s = records["samples"][case_id]
            o = records["oracles"][case_id]
            if (set(s) != {"id", "input", "metadata"} or not isinstance(s["input"], str)
                    or s["metadata"]["family"] != case["family"]
                    or s["metadata"]["oracle_in_input"] is not False
                    or not o["semantic_conditions"] or not o["independent_readbacks"]
                    or not o["completion_rule"]["all_required_independent_readbacks"]
                    or not o["completion_rule"]["all_semantic_conditions"]):
                raise ValueError
            for spec in o["independent_readbacks"]:
                if (set(spec) != {"evaluator", "target", "predicate", "source", "required"}
                        or type(spec["required"]) is not bool):
                    raise ValueError
        total = _hash(_canonical({"payload_files": payload_files,
                    "case_hashes": [{"id": r["id"], "sha256": r["case_sha256"]} for r in cases]}))
        if total != c["dataset"]["dataset_total_sha256"]:
            raise ValueError
        return FinalDataset(commitment_sha256, total, 3, {r["id"]: r for r in cases}, records)
    except Exception:
        # JSON/IO/validation failures can otherwise include a secret line/value.
        raise DatasetBindingError("sealed dataset binding validation failed") from None
