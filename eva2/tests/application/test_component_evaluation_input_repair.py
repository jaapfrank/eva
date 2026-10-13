from copy import deepcopy
import pytest
from eva.adapters.postgres.component_improvement_actions import DRAFT,EVALUATE
from eva.application.conversation import ConversationActionCall,ConversationActionInputRejected,ConversationConflict
from tests.application.test_scoped_component_evaluation_actions import adapter

@pytest.mark.parametrize("reference", ["missing", "wrong-kind", "current", "future"])
def test_no_draft_reference_is_repairable_before_dispatch(reference):
    component,checks,calls=adapter()
    if reference == "missing":
        component.timeline.rows.clear()
    elif reference == "wrong-kind":
        event=component.timeline.rows[component._thread(2)][0]
        event.payload["data"]["name"]="read_own_component_source"
        from eva.contracts.canonical_json import canonical_sha256
        event.payload_sha256=canonical_sha256(event.payload)
    step=3 if reference == "current" else 4 if reference == "future" else 2
    before=deepcopy(component.timeline.rows)
    with pytest.raises(ConversationActionInputRejected) as error:
        component.execute(component.source,ConversationActionCall("eval",EVALUATE,{"draft_step":step}),step_index=3)
    assert error.value.fields == ("draft_step",)
    assert "draft_own_component_improvement" in error.value.prompt
    assert not calls
    assert all(kind != "dispatch" for kind,_ in checks)
    assert component.timeline.rows == before


def test_unknown_draft_is_not_repaired_or_evaluated():
    component,checks,calls=adapter()
    event=component.timeline.rows[component._thread(2)][0]
    event.payload["data"]["output"]["status"]="unconfirmed"
    from eva.contracts.canonical_json import canonical_sha256
    event.payload_sha256=canonical_sha256(event.payload)
    with pytest.raises(ConversationConflict):
        component.execute(component.source,ConversationActionCall("eval",EVALUATE,{"draft_step":2}),step_index=3)
    assert not calls
