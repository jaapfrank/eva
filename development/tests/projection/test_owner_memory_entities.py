"""UI entity names use the same tagged recall and do not widen ordinary context."""
from dataclasses import replace
import pytest
from eva.adapters.projection.hindsight import HindsightMemoryEngine,HindsightProjectionConfig
from tests.projection.test_hindsight import _query,PRINCIPAL,NOW

@pytest.mark.parametrize('purpose', ['reply','owner_memory_view'])
def test_entity_names_only_for_owner_presentation(monkeypatch,purpose):
    engine=HindsightMemoryEngine(HindsightProjectionConfig('http://isolated.invalid'))
    requests=[]
    def request(method,path,body=None,**kw):
        requests.append(body)
        return 200,{'results':[{'id':'private-person-fact','text':'Mila is my daughter.','type':'world',
            'scores':{'final':0.9},'mentioned_at':NOW,'entities':['Mila',123,'x'*121],
            'metadata':{'principal_id':PRINCIPAL,'privacy_class':'internal','audience_kind':'private_person',
                'audience_id':PRINCIPAL,'trust':'direct_user','source_ref':'person-source','evidence_ref':'person-evidence'}}]}
    monkeypatch.setattr(engine,'_request',request)
    monkeypatch.setattr(engine,'ensure_bank',lambda *a:'isolated-person-bank')
    monkeypatch.setattr(engine,'_recall_style_mirror',lambda *a:None)
    query=replace(_query(),query_purpose=purpose)
    memory=engine.recall_for_presence(query)
    assert memory.confirmed_facts
    bodies=[b for b in requests if b and 'include' in b]
    assert all(b['tags'] and b['tags_match']=='all_strict' for b in bodies)
    if purpose=='owner_memory_view':
        assert all(b['include']['entities']=={'max_tokens':256} for b in bodies)
        assert memory.confirmed_facts[0].value['mentioned_names']==['Mila']
    else:
        assert all(b['include']['entities'] is None for b in bodies)
        assert 'mentioned_names' not in memory.confirmed_facts[0].value
