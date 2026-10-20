"""Apply accepted saved Python variants through the existing verified save route."""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re

from eva.operations.component_evaluation import validate_saved_artifact_target
from eva.contracts.canonical_json import canonical_sha256


def publication_plan(package):
    from eva.operations.component_final_assessment import validate_selected_package
    validate_selected_package(package)
    target = validate_saved_artifact_target(package.get("source_target"),
        relative_path=package["path"], baseline_sha256=package["baseline_sha256"])
    source = package["candidate_content"]
    package_binding = target.get("skill_package_binding")
    if len(source.encode()) > (65536 if package_binding is not None else 16384):
        raise ValueError("saved procedure source exceeds its existing improvement bound")
    destination = _destination(target, package["candidate_sha256"])
    if len(destination) > 240:
        raise ValueError("saved procedure destination exceeds the existing tool schema")
    return {"contract": "EvaSavedArtifactPublication/v1",
        "review_id": package["candidate_package_sha256"], "source_target": target,
        "baseline_sha256": package["baseline_sha256"],
        "candidate_sha256": package["candidate_sha256"], "destination_path": destination,
        "improvement_description": package["expected_gain"],
        **(_skill_publication(target, source) if package_binding is not None else {})}


def _destination(target, candidate_sha256):
    original = PurePosixPath(target["artifact_path"])
    if not original.is_absolute() or ".." in original.parts:
        raise ValueError("saved procedure destination differs")
    if target.get("skill_package_binding") is not None:
        if not original.name.endswith(".skill.json"):
            raise ValueError("saved package destination differs")
        return str(original.with_name(original.name[:-len(".skill.json")]
            + ".improved-" + candidate_sha256 + ".skill.json"))
    if original.suffix != ".py":
        raise ValueError("saved procedure destination differs")
    return str(original.with_name(original.stem + ".improved-" + candidate_sha256 + ".py"))


def _skill_publication(target, source):
    from eva.application.skill_packages import derived_manifest
    binding = target["skill_package_binding"]
    manifest = derived_manifest(binding["package_manifest"], binding["resource_path"], source.encode())
    return {"skill_package": {"candidate_manifest": manifest,
        "candidate_package_sha256": canonical_sha256(manifest)}}


def validate_candidate_artifact(publication, candidate_content, artifact_content):
    """Verify only the already reviewed bytes/manifest; this grants no authority."""
    if not isinstance(artifact_content, str) or not isinstance(candidate_content, str):
        return False
    if hashlib.sha256(candidate_content.encode()).hexdigest() != publication["candidate_sha256"]:
        return False
    if "skill_package" not in publication:
        return len(artifact_content.encode()) <= 16384 and artifact_content == candidate_content
    if len(artifact_content.encode()) > 65536:
        return False
    from eva.application.skill_packages import matches_reviewed_package
    expected = publication["skill_package"]
    binding = publication["source_target"]["skill_package_binding"]
    return (canonical_sha256(expected["candidate_manifest"]) == expected["candidate_package_sha256"]
        and matches_reviewed_package(artifact_content, expected["candidate_manifest"],
            binding["resource_path"], candidate_content.encode()))


def materialize_candidate_artifact(publication, candidate_content, resolved):
    """Rebuild one immutable package variant from the fresh host-bound source."""
    target = publication["source_target"]
    if "skill_package_binding" not in target:
        artifact = candidate_content
    else:
        import json
        from eva.application.skill_packages import AgentSkillPackage, replace_python_resource
        from eva.contracts.canonical_json import canonical_json_bytes
        binding = target["skill_package_binding"]
        raw = resolved.get("baseline_package_content")
        if (not isinstance(raw, str)
                or hashlib.sha256(raw.encode()).hexdigest() != binding["package_content_sha256"]):
            raise ValueError("current complete package source changed")
        original = AgentSkillPackage.from_wire(json.loads(raw))
        if (original.manifest() != binding["package_manifest"]
                or hashlib.sha256(original.resource(binding["resource_path"])).hexdigest()
                   != target["content_sha256"]):
            raise ValueError("current package resource or support context differs")
        variant = replace_python_resource(original, binding["resource_path"], candidate_content.encode())
        artifact = canonical_json_bytes(variant.to_wire()).decode("utf-8")
    if not validate_candidate_artifact(publication, candidate_content, artifact):
        raise ValueError("candidate artifact differs from the reviewed publication")
    return artifact


def _resolve_original(procedures, turn, target):
    resource_path = target.get("skill_package_binding", {}).get("resource_path")
    if resource_path is None:
        return procedures.resolve_improvement_source(turn, target["procedure_reference_id"])
    return procedures.resolve_improvement_source(turn, target["procedure_reference_id"], resource_path=resource_path)


def verify_saved_release(review_id, release):
    # Imported lazily: the existing application queue calls this discriminator.
    from eva.operations.reviewed_component_application import _artifact, _path
    from eva.operations.component_release_import import canonical, checked_evidence, digest
    if set(release) != {"kind", "package", "review", "publication", "update_receipt"}:
        raise ValueError("saved release configuration differs")
    paths, objects = {}, {}
    for name in ("package", "review", "publication"):
        paths[name], objects[name] = _artifact(release[name])
    package, review, publication = (objects[name] for name in ("package", "review", "publication"))
    plan = publication_plan(package)
    if publication != plan or review_id != plan["review_id"]:
        raise ValueError("saved publication is not the selected package")
    if (review.get("contract") != "EvaComponentReleaseReview/v1"
            or review.get("decision") != "accept"
            or review.get("candidate_package_sha256") != review_id
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
        raise ValueError("saved artifact requires an independent accepted review")
    for name in ("measured_gain_evidence", "independent_review_evidence"):
        checked_evidence(paths["review"], review.get(name))
    receipt = _path(release["update_receipt"], exists=False)
    if receipt in paths.values():
        raise ValueError("saved application receipt collides with accepted inputs")
    import os, stat
    info = receipt.parent.stat()
    if info.st_uid != os.geteuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("saved application receipt directory must be host-owned")
    return {"kind": "saved_artifact", "review_id": review_id,
        "accepted_review_sha256": digest(canonical(review)), "receipt": receipt,
        "publication": plan, "candidate_content": package["candidate_content"]}


def receipt_confirms_application(receipt, publication, task_id):
    ref = receipt.get("reference", {})
    if not isinstance(ref, dict):
        return False
    expected_id = "procedure-reference:" + canonical_sha256({
        "scope": publication["source_target"]["scope"], "receipt": ref.get("receipt_sha256")})
    return (receipt.get("contract") == "EvaSavedArtifactApplication/v1"
        and receipt.get("status") == "saved_and_designated"
        and receipt.get("task_id") == task_id
        and receipt.get("publication") == publication
        and receipt.get("old_source_unchanged") is True
        and ref.get("path") == publication["destination_path"]
        and ref.get("content_sha256") == (
            receipt.get("candidate_artifact_sha256") if "skill_package" in publication
            else publication["candidate_sha256"])
        and ("skill_package" not in publication or (
            isinstance(receipt.get("candidate_artifact_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["candidate_artifact_sha256"]) is not None
            and receipt.get("skill_package_sha256") == publication["skill_package"]["candidate_package_sha256"]))
        and isinstance(ref.get("document_id"), str)
        and ref["document_id"] == expected_id
        and isinstance(ref.get("receipt_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", ref["receipt_sha256"]) is not None
        and ref.get("retention_until") == publication["source_target"]["retention_until"])


def apply_in_core(publication, candidate_content, task_id, *, engine, procedures, tools,
                  source_turn, check_ready):
    """Host-only application: no model commands and no new execution platform."""
    from eva.application.background_execution import BackgroundExecutionScope, background_execution
    from eva.application.conversation import ConversationActionCall
    from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
    import json
    target = validate_saved_artifact_target(publication["source_target"],
        baseline_sha256=publication["baseline_sha256"])
    if (hashlib.sha256(candidate_content.encode()).hexdigest() != publication["candidate_sha256"]
            or len(candidate_content.encode()) > (65536 if "skill_package_binding" in target else 16384)):
        raise ValueError("saved application candidate changed")
    expected_path = _destination(target, publication["candidate_sha256"])
    if publication["destination_path"] != expected_path:
        raise ValueError("saved application destination changed")
    improvement = publication.get("improvement_description")
    if not isinstance(improvement, str) or not improvement.strip() or "\x00" in improvement:
        raise ValueError("saved application applicability is unavailable")
    actual_scope = {key: getattr(source_turn, key) for key in
                    ("tenant_id", "installation_id", "principal_id", "project_id")}
    if actual_scope != target["scope"]:
        raise ValueError("saved application actor or project differs")

    def guard(turn):
        if turn != source_turn or check_ready(task_id) != "ready":
            raise ValueError("saved application task is stopped or no longer idle")
        procedures._host(turn)

    # A separate effect namespace preserves the task's existing tool slots.
    scope = BackgroundExecutionScope(source_turn, "reviewed-artifact:" + publication["review_id"],
                                     authorize_dispatch=guard)
    with background_execution(scope):
        guard(source_turn)
        resolved = _resolve_original(procedures, source_turn, target)
        if resolved["source_target"] != target:
            raise ValueError("saved application baseline changed")
        store = PostgresMemoryOutbox(engine, tenant_id=source_turn.tenant_id,
                                    installation_id=source_turn.installation_id)
        old = store.resolve_source(target["procedure_reference_id"],
            principal_id=target["scope"]["principal_id"], project_id=target["scope"]["project_id"])
        if old is None:
            raise ValueError("saved application baseline metadata unavailable")
        use_when = json.loads(old.content)["use_when"]
        artifact_content = materialize_candidate_artifact(publication, candidate_content, resolved)
        save = tools.execute(source_turn, ConversationActionCall(
            "reviewed-artifact-save:" + publication["review_id"], "save_workspace_document",
            {"path": publication["destination_path"], "content": artifact_content}), step_index=0)
        if save.output.get("status") != "verified":
            raise ValueError("saved application write is not verified")
        guard(source_turn)
        designation_arguments = {"document_path": publication["destination_path"],
            "use_when": (use_when[:450] + " — onafhankelijk beoordeelde verbetering: "
                         + improvement[:500])[:1000],
            "retention_until": target["retention_until"]}
        from eva.application.initiative_execution import current_initiative
        native_guard = current_initiative()
        if native_guard is not None:
            native_guard.bind_designation(designation_arguments)
        designation = procedures.execute(source_turn, ConversationActionCall(
            "reviewed-artifact-designate:" + publication["review_id"], "designate_reusable_procedure",
            designation_arguments), step_index=1)
        if designation.output.get("status") != "designated":
            raise ValueError("saved application designation is not verified")
        guard(source_turn)
        old_after = _resolve_original(procedures, source_turn, target)
        if old_after != resolved:
            raise ValueError("original saved source changed during application")
        return {"contract": "EvaSavedArtifactApplication/v1", "status": "saved_and_designated",
            "task_id": task_id, "publication": publication,
            "reference": designation.output["reference"], "old_source_unchanged": True,
            "new_model_requests": 0,
            **({"candidate_artifact_sha256": hashlib.sha256(artifact_content.encode()).hexdigest(),
                "skill_package_sha256": publication["skill_package"]["candidate_package_sha256"]}
               if "skill_package" in publication else {})}


def _verify_source_binding(spec, turn):
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    identity = {key: getattr(turn, key) for key in
                ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
    identity["audience"] = {"kind": "private_person", "principal_id": turn.principal_id}
    if (spec.get("audience") != "private" or turn.audience.kind != "private_person"
            or spec.get("source") != identity
            or spec.get("source_ingress_sha256") != canonical_sha256(PostgresConversationJournal._ingress_data(turn))):
        raise ValueError("saved application original ingress differs")


def runtime_apply(payload):
    """Fixed Docker-exec entry point using credentials already inside Eva's core."""
    import sqlalchemy as sa
    from uuid import UUID
    from eva.bootstrap.runtime import RuntimeSettings
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    from eva.adapters.postgres.current_catalog import delegated_work_runs as work
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, TimelineScope
    from eva.contracts.canonical_json import canonical_sha256
    from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
    from eva.operations.component_application_runtime import _execution_completion_state
    import asyncio
    from contextlib import nullcontext
    from eva.application.initiative_execution import initiative_execution
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    from eva.contracts.delegated_work import load_delegated_work_spec
    settings = RuntimeSettings.from_environment()
    engine = sa.create_engine(sqlalchemy_postgres_dsn(settings.database_dsn))
    try:
        timeline = PostgresOperationalTimeline(engine, TimelineScope(
            UUID(str(settings.dashboard_tenant_id)), UUID(str(settings.dashboard_installation_id))))
        def ready(task_id):
            with engine.begin() as c:
                timeline._scope(c)
                spec = c.execute(sa.select(work.c.spec_payload).where(work.c.work_id == task_id)).scalar_one()
                _verify_source_binding(spec, turn)
                rows = c.execute(sa.select(work.c.work_id, work.c.state, work.c.stop_requested_at, work.c.spec_payload)
                    .where(work.c.spec_payload["execution_id"].astext == spec["execution_id"])).mappings().all()
                state = _execution_completion_state(rows, task_id, payload["scope_sha256"], canonical_sha256)
                if native_guard is not None:
                    native_guard.check(turn)
                leased = c.scalar(sa.select(sa.func.count()).select_from(work).where(
                    work.c.lease_owner.is_not(None), work.c.lease_expires_at > sa.func.now()))
                return "waiting" if state == "ready" and leased else state
        with engine.begin() as c:
            timeline._scope(c)
            spec = c.execute(sa.select(work.c.spec_payload).where(work.c.work_id == payload["task_id"])).scalar_one()
        contribution = ProcedureReferenceProviderFactory.build(ProcedureReferenceProviderFactory.load_settings(), engine)
        procedures = contribution.external_tools[0]
        native_guard = None
        if type(load_delegated_work_spec(spec)) is DelegatedInitiativeDigitalWorkSpec:
            native_guard, turn = procedures.source_loader.native_authority().bind_reviewed_saved_application(
                payload["task_id"], payload["publication"], payload["candidate_content"])
        else:
            turn = _SourceTurnLoader(engine, procedures.authority)(spec["source"])
        _verify_source_binding(spec, turn)
        if turn.project_id != payload["publication"]["source_target"]["scope"]["project_id"]:
            raise ValueError("saved application task project differs")
        adapter = procedures.gateway_factory()
        asyncio.run(adapter.verify_catalog())
        tools = PostgresExternalConversationTools(engine, adapter.planner(), adapter,
            authorize_turn=procedures.authorize_turn)
        with initiative_execution(native_guard) if native_guard is not None else nullcontext():
            return apply_in_core(payload["publication"], payload["candidate_content"], payload["task_id"],
                engine=engine, procedures=procedures, tools=tools, source_turn=turn, check_ready=ready)
    finally:
        engine.dispose()
