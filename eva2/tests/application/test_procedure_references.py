from copy import deepcopy
from dataclasses import replace
import pytest
from eva.application.procedure_references import (
    ProcedureScope, ProcedureReference, ProcedureReferences, VerifiedSaveSource,
    ProcedureReferenceRejected,
)
NOW="2026-09-13T06:00:00Z"
UNTIL="2026-10-13T06:00:00Z"
SCOPE=ProcedureScope("family","home","person:owner")

class SourceStore:
    def __init__(self):self.documents={}
    def record_source(self,document):
        old=self.documents.get(document.document_id)
        if old is not None and old!=document:raise ValueError('replay differs')
        self.documents[document.document_id]=document
    def resolve_source(self,document_id,**scope):return self.documents.get(document_id)

class Host:
    def __init__(self):
        self.allowed=True;self.active=True;self.reads=0;self.bytes=b'Custom review procedure, revision one.'
        args={'path':'/approved/review.md','content':self.bytes.decode()}
        expected={'content':args['content']}
        admission={'principal_id':SCOPE.principal_id,'conversation_id':'source-conversation',
            'source_message_id':'source-message','action_call_id':'save1','action_arguments':args,
            'request_arguments':dict(args),'request_observation_arguments':{'path':args['path']},
            'request_expected_proof':expected,'request_tool_configuration_sha256':'a'*64,
            'receipt_output':{'transport':'mcp_gateway','tool_configuration_sha256':'a'*64}}
        self.source=VerifiedSaveSource(SCOPE,admission,'STATE_VERIFIED',{
            'expected':expected,'observed':dict(expected),'evidence':{'source':'mcp_gateway_readback','observation_sha256':'b'*64}})
    def load_save(self,scope,call_id):return self.source
    def permits(self,scope,path):return self.allowed
    def source_active(self,scope,message):return self.active
    def read_current(self,scope,path):self.reads+=1;return self.bytes

def setup():
    host,store=Host(),SourceStore();return host,store,ProcedureReferences(store,host)

def designate(service):
    return service.designate(SCOPE,source_call_id='save1',use_when='Review a draft using our saved procedure',now=NOW,retention_until=UNTIL)

def test_explicit_reference_resolves_candidates_without_personal_facts():
    host,store,service=setup();assert not store.documents # ordinary saves do not register themselves
    reference=designate(service);assert isinstance(reference,ProcedureReference)
    doc=store.documents[reference.document_id]
    assert doc.trust=='model_inference_unconfirmed' and doc.audience.kind=='private_person'
    reads=host.reads
    resolved=service.resolve_candidates(SCOPE,(reference.document_id,),now=NOW)
    assert resolved[0].reference==reference
    assert resolved[0].untrusted_content==host.bytes.decode()
    assert host.reads==reads+1  # reuse the validation read, no second fetch
    # Nonmatching search emits no candidate; no hidden file reads are performed.
    reads=host.reads;assert service.resolve_candidates(SCOPE,(),now=NOW)==();assert host.reads==reads

@pytest.mark.parametrize('change',['invoke','unverified','wrong_owner','wrong_path','mismatch','read'])
def test_invalid_save_sources_rejected(change):
    host,store,service=setup();s=deepcopy(host.source);a=s.admission
    if change=='invoke':a['receipt_output'].update(operation_kind='invoke',external_state_verified=False)
    if change=='read':a['receipt_output']['operation_kind']='read'
    if change=='unverified':s=replace(s,terminal_event_type='RECOVERY_FAILED')
    if change=='wrong_owner':a['principal_id']='person:other'
    if change=='wrong_path':a['request_observation_arguments']['path']='/other'
    if change=='mismatch':s.proof['observed']['content']='invented'
    host.source=s
    with pytest.raises(ProcedureReferenceRejected):designate(service)
    assert not store.documents

@pytest.mark.parametrize('change',['expired','mandate','source','content','receipt','scope','erased'])
def test_invalid_candidate_not_returned(change):
    host,store,service=setup();ref=designate(service);now=NOW;scope=SCOPE
    if change=='expired':now=UNTIL
    if change=='mandate':host.allowed=False
    if change=='source':host.active=False
    if change=='content':host.bytes=b'new revision'
    if change=='receipt':host.source.admission['source_message_id']='changed'
    if change=='scope':scope=replace(SCOPE,principal_id='other')
    if change=='erased':store.documents.clear();assert service.resolve_candidates(scope,(ref.document_id,),now=now)==();return
    with pytest.raises(ProcedureReferenceRejected):service.resolve_candidates(scope,(ref.document_id,),now=now)

def test_revocation_during_read_blocks_return():
    host,store,service=setup();ref=designate(service)
    def read(scope,path):host.allowed=False;return host.bytes
    host.read_current=read
    with pytest.raises(ProcedureReferenceRejected):service.resolve_candidates(SCOPE,(ref.document_id,),now=NOW)


def test_oversized_verified_content_is_rejected_without_truncation():
    from eva.application.procedure_references import MAX_RESOLVED_CONTENT_BYTES
    host,store,service=setup()
    content="é"*(MAX_RESOLVED_CONTENT_BYTES//2+1)
    host.bytes=content.encode()
    a=host.source.admission
    a['action_arguments']['content']=content
    a['request_arguments']['content']=content
    a['request_expected_proof']['content']=content
    host.source.proof['expected']['content']=content
    host.source.proof['observed']['content']=content
    ref=designate(service)
    reads=host.reads
    with pytest.raises(ProcedureReferenceRejected,match="bound exceeded"):
        service.resolve_candidates(SCOPE,(ref.document_id,),now=NOW)
    assert host.reads==reads+1
