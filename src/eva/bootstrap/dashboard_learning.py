"""Read the owner's real initiative, reflection and component-action history."""
from datetime import UTC, datetime
import hashlib
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import operational_events as events, PostgresOperationalTimeline
from eva.contracts.canonical_json import canonical_sha256


def _data(row):
    if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
        raise RuntimeError("learning record integrity differs")
    data=row.payload.get("data")
    if not isinstance(data,dict):raise RuntimeError("learning record is invalid")
    return data


def _principals(service):
    ids={str(service.owner_principal_id)}
    loader=service._private_bridge_principal_loader
    alias=loader() if loader is not None else None
    if alias:ids.add(alias)
    return ids


def _reflections(conn, owner, hashes, principals):
    e=events.c
    rows=conn.execute(sa.select(events).where(e.tenant_id==owner._scope.tenant_id,
        e.installation_id==owner._scope.installation_id,e.event_type=="MEMORY_REFLECTION_COMPLETED",
        e.payload["data"]["principal_sha256"].astext.in_(hashes)).order_by(e.occurred_at.desc(),e.thread_id).limit(21)).all()
    context=conn.execute(sa.select(e.payload["data"]["style_mirror_sha256"].astext.label("digest"),
        sa.func.max(e.occurred_at).label("last_used")).where(e.tenant_id==owner._scope.tenant_id,
        e.installation_id==owner._scope.installation_id,e.event_type=="CONVERSATION_MODEL_MEMORY_CONTEXT",
        e.payload["data"]["principal_id"].astext.in_(principals)).group_by("digest")).all()
    # Verify each claimed later binding separately before presenting it as evidence.
    result=[]
    for row in rows[:20]:
        d=_data(row);digest=d.get("after_content_sha256");last=None
        for item in context:
            if item.digest==digest and item.last_used>row.occurred_at:
                bound=conn.execute(sa.select(events).where(e.tenant_id==owner._scope.tenant_id,
                    e.installation_id==owner._scope.installation_id,e.event_type=="CONVERSATION_MODEL_MEMORY_CONTEXT",
                    e.payload["data"]["principal_id"].astext.in_([p for p in principals if hashlib.sha256(p.encode()).hexdigest()==d["principal_sha256"]]),
                    e.payload["data"]["style_mirror_sha256"].astext==digest,
                    e.occurred_at>row.occurred_at).order_by(e.occurred_at.desc()).limit(1)).first()
                if bound is not None:_data(bound);last=bound.occurred_at.isoformat()
        result.append({"id":row.thread_id,"at":row.occurred_at.isoformat(),"disposition":d.get("disposition"),
            "source_fact_count":d.get("source_fact_count"),"changed":digest is not None and digest!=d.get("before_content_sha256"),
            "content_sha256":digest,"later_context_at":last,
            "meaning":"Latere contextbinding bewijst aangeboden geheugen, geen bewezen gedragsverbetering."})
    return result,len(rows)>20


def _component_records(conn, owner, works, principals):
    mapping={}
    for work in works[:64]:
        spec=work.spec;source=getattr(spec,"source",None)
        if source is None or not hasattr(spec,"execution_id") or source.principal_id not in principals:continue
        if (source.tenant_id,source.installation_id)!=(owner._tenant_id,owner._installation_id):continue
        if source.audience.kind!="private_person" or source.audience.principal_id!=source.principal_id:continue
        for step in range(min(256,spec.grant.maximum_tool_calls+1)):
            thread="component-draft:"+canonical_sha256({"execution":spec.execution_id,"source":source.model_dump(mode="json"),"step":step})
            mapping[thread]=spec.work_id
    if not mapping:return [],False
    e=events.c;rows=conn.execute(sa.select(events).where(e.tenant_id==owner._scope.tenant_id,
        e.installation_id==owner._scope.installation_id,e.event_type=="COMPONENT_ACTION_RECORDED",
        e.thread_id.in_(mapping)).order_by(e.occurred_at.desc(),e.thread_id).limit(65)).all()
    stages={"draft_own_component_improvement":"candidate","evaluate_own_component_improvement":"evaluation",
        "compare_own_component_improvements":"selection","prepare_own_component_release":"review",
        "apply_reviewed_component_improvement":"application_request","get_reviewed_component_application":"application_status"}
    result=[]
    for row in rows[:64]:
        d=_data(row);stage=stages.get(d.get("name"))
        if stage is None:continue
        output=d.get("output",{});report=output.get("operation_result",{});
        if output.get("status")!="verified" or not isinstance(report,dict):raise RuntimeError("component receipt is invalid")
        package=report.get("package",report);package=package if isinstance(package,dict) else {}
        state=report.get("state") or report.get("decision")
        state=state if isinstance(state,str) and len(state)<=160 else None
        result.append({"id":row.thread_id,"at":row.occurred_at.isoformat(),"work_id":mapping[row.thread_id],
            "stage":stage,"state":state,"path":str(package.get("path",package.get("relative_path","")))[:240],
            "candidate_sha256":package.get("candidate_sha256"),"reference_retained":state in {"reference_retained","tie_reference_retained","baseline_retained"},
            "applied":stage=="application_status" and report.get("state")=="completed" and report.get("installed") is True,
            "meaning":"Opgeslagen ontvangstbewijs op dit tijdstip; voorstel, beoordeling en installatie zijn afzonderlijke feiten."})
    return result,len(rows)>64 or len(works)>64


def initiative_history(runtime):
    from eva.bootstrap.dashboard_work_context import owner_turn
    service,turn=owner_turn(runtime);owner=runtime._owner_initiatives();now=datetime.now(UTC).replace(microsecond=0)
    principals=_principals(service);hashes={hashlib.sha256(p.encode()).hexdigest() for p in principals}
    works=service.store.list_owned(**service._owner,limit=65)
    page=owner.page(now=now,include_result_preview=True)
    timeline=PostgresOperationalTimeline(owner._engine,owner._scope)
    with owner._engine.begin() as conn:
        owner._authorize(conn,now)
        for card in page["cards"]:
            thread="intent:"+card["intent_id"];timeline.load(thread);history=timeline.events(thread)
            first=history[0];adoption=first.payload["data"]
            if first.event_type!="PERSONAL_INTENT_ADOPTED" or adoption.get("principal_id")!=str(owner._owner):raise RuntimeError("initiative history owner differs")
            outcome=next((e for e in reversed(history) if e.event_type in {"PERSONAL_INITIATIVE_COMPLETED","PERSONAL_PROTOTYPE_COMPLETED"}),None)
            card["adopted_at"]=first.occurred_at.isoformat()
            card["result_at"]=None if outcome is None else outcome.occurred_at.isoformat()
            card["work_id"]=None
            if outcome is not None:
                identifier=outcome.payload["data"].get("work_id")
                if identifier and any(w.spec.work_id==identifier for w in works):card["work_id"]=identifier
            card["feedback_later_use"]="Geen afzonderlijke latere gedragstoepassing bij deze bijdrage geregistreerd."
            feedback = next((item for item in reversed(history)
                if item.event_type == "PERSONAL_INITIATIVE_FEEDBACK"), None)
            card["feedback_application"] = _feedback_application(conn, owner, timeline, card, feedback, now)
            if card["feedback_application"] is not None:
                application = card["feedback_application"]
                labels = {"unknown": "onbekend", "positive": "positief", "negative": "negatief", "mixed": "gemengd"}
                card["feedback_later_use"] = (
                    "Feedback meegewogen in latere afweging: " + application["adjustment"] + " "
                    + " · ".join(label + ": " + labels[application[key]] for key, label in
                        (("usefulness", "Nut"), ("timing", "Timing"), ("nuisance", "Hinder")))
                    + ". Dit is Eva’s afweging; het bewijst nog geen verbetering.")

        reflections,reflection_truncated=_reflections(conn,owner,hashes,principals)
        components,component_truncated=_component_records(conn,owner,works,principals)
    mirrors=[];memory=runtime._memory;bank=memory._bank_id(owner._tenant_id,owner._installation_id)
    for principal in sorted(principals):
        try:content=memory._recall_style_mirror(bank,principal);state="available" if content else "absent"
        except Exception:content=None;state="unavailable"
        mirrors.append({"state":state,"content":content,"meaning":"Afgeleide communicatiestijl, geen nieuw zelfstandig getoetst feit."})
    with owner._engine.begin() as conn:owner._authorize(conn,datetime.now(UTC))
    service.validate_authority(turn)
    if _principals(service)!=principals:raise RuntimeError("private alias changed during history read")
    return {**page,"contract":"EvaOwnerInitiativeHistory/v1","learning":{"reflections":reflections,
        "reflections_truncated":reflection_truncated,"current_styles":mirrors,"components":components,
        "components_truncated":component_truncated,"meaning":"Echte opgeslagen registraties; een reflectie of voorstel is geen bewezen verbetering. Geen modelproef gestart door deze pagina."}}


def _feedback_application(conn, owner, timeline, card, feedback, now):
    """Read an actual later private decision bound to the current feedback event."""
    if feedback is None:
        return None
    from eva.application.human_understanding import validate_feedback_application
    if canonical_sha256(feedback.payload) != feedback.payload_sha256:
        raise RuntimeError("initiative feedback integrity differs")
    source = {"intent_id": card["intent_id"], "owner_feedback": feedback.payload["data"]["feedback"],
        "owner_feedback_sha256": feedback.payload_sha256}
    e = events.c
    prefix = "sensory:initiative-review:" + hashlib.sha256(str(owner._owner).encode()).hexdigest()[:24] + ":"
    rows = conn.execute(sa.select(events).where(e.tenant_id == owner._scope.tenant_id,
        e.installation_id == owner._scope.installation_id, e.thread_id.startswith(prefix),
        e.event_type.in_(("WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED")),
        e.occurred_at >= feedback.occurred_at, e.occurred_at <= now,
        e.payload["data"]["feedback_application"]["intent_id"].astext == card["intent_id"],
        e.payload["data"]["feedback_application"]["feedback_sha256"].astext == feedback.payload_sha256,
    ).order_by(e.occurred_at.desc()).limit(1)).all()
    if not rows:
        return None
    row = rows[0]
    data = _data(row)
    history = timeline.events(row.thread_id)
    owner._validate_review_source(row.thread_id, history[0], household=False)
    application = validate_feedback_application(data["feedback_application"], source=source)
    return {**application, "decided_at": row.occurred_at.isoformat(),
        "decision_sha256": bytes(row.payload_sha256).hex(),
        "disposition": data["disposition"], "meaning": "provisional_decision_link_not_proven_improvement"}
