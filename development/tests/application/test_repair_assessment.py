"""Assessment isolation preserves ordinary memory scope without another store."""
from dataclasses import replace
import pytest

from eva.application.conversation import ConversationTurn, ConversationConflict
from eva.application.memory import RecallCurrentFacts
from eva.application.repair_assessment import RepairAssessmentMemory, repair_assessment_task


def fixture():
    turn=ConversationTurn.private('tenant','installation','owner','initiative-task:example',
        'initiative-execute:example','Synthetic separate assessment','2026-10-22T12:00:00Z')
    query=RecallCurrentFacts(recall_id='recall:one',installation_id=turn.installation_id,
        tenant_id=turn.tenant_id,principal_id=turn.principal_id,audience=turn.audience,
        conversation_id=turn.conversation_id,project_id=None,query_purpose='conversation_reply',
        requested_valid_at=turn.occurred_at,scope={'kind':'person','principal_id':turn.principal_id},
        subject_ids=(turn.principal_id,),query_text='PRIVATE_CANDIDATE_SEARCH')
    return turn,query


def test_both_memory_paths_are_empty_and_recheck_authority():
    turn,query=fixture();checks=[]
    def authorize(source):
        assert source==turn;checks.append(source)
    memory=RepairAssessmentMemory(turn,authorize)
    for recall in (memory.recall_for_presence,memory.recall_for_presence_fast):
        result=recall(query)
        assert result.recall.selected==[] and result.confirmed_facts==() and result.style_mirror is None
        assert 'PRIVATE_CANDIDATE_SEARCH' not in str(result)
    assert len(checks)==2
    memory.authorize=lambda _:(_ for _ in ()).throw(ConversationConflict('revoked'))
    with pytest.raises(ConversationConflict):memory.recall_for_presence(query)


def test_assessment_memory_cannot_cross_source_scope():
    turn,query=fixture();memory=RepairAssessmentMemory(turn,lambda _:None)
    with pytest.raises(ConversationConflict):
        memory.recall_for_presence(replace(query,conversation_id='another-conversation'))


def test_original_behavior_author_cannot_be_given_candidate_or_comparison():
    fields=dict(target_id='a'*64,sources={'application/example.py':'b'*64},observation={})
    with pytest.raises(ValueError,match='must not receive a candidate'):
        repair_assessment_task('original_behavior_oracle',**fields,candidate_sha256='c'*64)
    task=repair_assessment_task('original_behavior_oracle',**fields)
    assert '/workspace/repair/oracle.json' in task and 'candidate_sha256' not in task
