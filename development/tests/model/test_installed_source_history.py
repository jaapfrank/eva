"""Archive exact older installed source pages only behind saved work and authority."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from eva.adapters.model.pydantic_conversation import PydanticConversationModel as Model
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict
from eva.contracts.canonical_json import canonical_sha256
from tests.model.test_catalogue_model_view import _working_note, _checkpoint_ha_read, _context_archive_route
from tests.model.test_authored_artifact_context import _latest, _raw


def source():
    content = "# Original historical source code\n" * 150
    return ConversationActionReceipt(ConversationActionCall("source", "read_own_component_source",
        {"path":"application/example.py","start_line":1,"resource_path":""}),
        {"status":"verified","scope":"source_read","component_step":2,"operation_result":{
            "kind":"python","path":"application/example.py","source_sha256":"a"*64,
            "start_line":1,"total_lines":200,"content":content,"next_line":151}})


def test_exact_old_page_can_be_read_from_existing_authorized_archive():
    from tests.connectors.test_connector_results import ConnectorResults, TURN
    old=source();checkpoint=_working_note("saved",1,"Observed the installed implementation.")
    history=(old,checkpoint,_latest());frozen=_raw(history)
    assert old in Model._receipt_context_selection(history)[0]
    selected,overview=Model._receipt_context_selection(history,archive_discovery_reads=True)
    assert selected==(checkpoint,history[-1])
    index=json.loads(overview.split("\n",1)[1]);row=index['historical_discovery_reads'][0]
    assert row['receipt']==old.output and row['call']['arguments']==old.call.arguments
    assert row['source_receipt_sha256']==canonical_sha256(old.output)
    assert row['installed_source_history'] is True
    _,_,timeline,_,stopped,authorize=_checkpoint_ha_read()
    route=_context_archive_route(timeline,authorize)
    note=Model._archive_read_overview(TURN,overview,route.archive_model_context)
    manifest=json.loads(note.split("\n",1)[1]);capture=ConnectorResults(timeline,TURN,authorize)
    args={'pointer':'/historical_discovery_reads/0/receipt/operation_result/content','offset':0,'limit':200,'query':None}
    assert capture.read(manifest['reader']['tool_id'],args)['text']==old.output['operation_result']['content'][:200]
    assert len(note)<len(overview) and _raw(history)==frozen
    stopped[0]=True
    with pytest.raises(ConversationConflict):capture.read(manifest['reader']['tool_id'],args)


@pytest.mark.parametrize('case',['failed','proposal','saved','target','path','range','hash','resource','latest','after_checkpoint','no_checkpoint'])
def test_current_artifacts_errors_and_unbound_pages_remain_literal(case):
    old=source();out=deepcopy(old.output);args=dict(old.call.arguments)
    if case=='failed':out['status']='failed'
    if case=='proposal':out['scope']='unexecuted_proposal'
    if case=='saved':args['path']=out['operation_result']['path']='saved_artifact_'+'a'*64+'.py'
    if case=='target':out['operation_result']['source_target']={}
    if case=='path':out['operation_result']['path']='another.py'
    if case=='range':out['operation_result']['next_line']=20
    if case=='hash':out['operation_result']['source_sha256']='invalid'
    if case=='resource':args['resource_path']='scripts/example.py'
    old=replace(old,call=replace(old.call,arguments=args),output=out)
    checkpoint=_working_note('saved',1,'Keep current work.')
    history=((checkpoint,old) if case=='latest' else (checkpoint,old,_latest()) if case=='after_checkpoint'
             else (old,_latest()) if case=='no_checkpoint' else (old,checkpoint,_latest()))
    assert old in Model._receipt_context_selection(history,archive_discovery_reads=True)[0]
