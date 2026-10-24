"""An existing successful dream, fresh context, erasure and failure isolation."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
from eva.application.housemate_development import with_current_development
from eva.application.memory import PresenceMemory, RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.bootstrap.housemate_development import HousemateDevelopment
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall
from tests.application.test_housemate_development import source, project, OWNER, NOW

AT = datetime(2026,10,24,12,tzinfo=UTC)


def query(**changes):
    base = RecallCurrentFacts(recall_id="test", tenant_id="house", installation_id="eva",
        principal_id=OWNER, audience=MemoryAudience.private_person(principal_id=OWNER),
        conversation_id=OWNER, project_id=None, query_purpose="conversation",
        requested_valid_at=NOW, scope={"kind":"private"}, subject_ids=(OWNER,), query_text="Hi", maximum_facts=8)
    return replace(base, **changes)


def empty_memory():
    return HindsightMemoryEngine._empty_presence(query())


class SelectionFixture:
    def __init__(self):
        self.current = None
        self.writes = 0
        self.projects = []

    def save(self, selection, *, now):
        if self.current and self.current["selection"] == selection:
            return False
        if not self.current and not selection["delta_refs"] and not selection["project_refs"]:
            return False
        self.writes += 1
        self.current = dict(selection=selection, selection_sha256=canonical_sha256(selection),revision=self.writes)
        return True

    def load(self, *, now):
        return self.current

    def current_projects(self, refs, *, now):
        return self.projects


def projection():
    data = dict(sources=[source("Be brief unless I request details")], projects=[project()], authorized=True, alias=None)
    class Reader:
        _owner, _binding, _tenant, _installation = OWNER,"binding","house","eva"
        @property
        def _bindings(self):
            return SimpleNamespace(private_bridge_principal=lambda **kw:data["alias"])
        def require_learning_query(self, q, *, now):
            if not data["authorized"]:
                raise RuntimeError("not authorized")
        def selected_private_sources(self, q, refs, *, now):
            return data["sources"]
    owner = SimpleNamespace(page=lambda **kw:dict(cards=data["projects"]))
    store = SelectionFixture()
    store.projects = data["projects"]
    dev = HousemateDevelopment(owner=owner, source_reader=Reader, clock=lambda:AT,selections=store)
    return dev, data, store


def completed(dev):
    return dev.after_reflection("house","eva",OWNER,SimpleNamespace(observation_error_type=None))


def test_real_dream_boundary_then_later_read_is_source_bound_and_idempotent():
    dev,data,store = projection()
    assert dev.read_current(query()) is None
    assert completed(dev)
    assert not completed(dev) and store.writes == 1
    first = dev.read_current(query())
    assert first["personality_deltas"][0]["adjustment"].startswith("Be brief")
    assert first["inner_state"]["current_curiosity"] == data["projects"][0]["question"]
    # Read-only selection does not run or re-run projects and never saves state.
    assert store.writes == 1
    data["sources"] = []
    store.projects = [{**data["projects"][0],"status":"stopped","phase":"personal_stopped"}]
    assert dev.read_current(query()) is None
    assert store.writes == 1  # erasure takes effect without waiting for another dream.


def test_cached_state_is_removed_when_sources_disappear_or_scope_changes():
    dev,data,store = projection();assert completed(dev)
    cached = with_current_development(empty_memory(),query(),dev)
    assert len(cached.confirmed_facts) == 1
    assert cached.confirmed_facts[0].value["semantic_trust"] == "model_inference_unconfirmed"
    assert not cached.confirmed_facts[0].value["is_execution_permission"]
    data["authorized"] = False
    assert with_current_development(cached,query(),dev).confirmed_facts == ()
    assert dev.last_error is not None


@pytest.mark.parametrize("changes",[
    {"project_id":"a-project"},
    {"scope":{"kind":"household_event","source":"home_assistant","event_type":"state_changed"}},
    {"principal_id":"daughter","audience":MemoryAudience.private_person(principal_id="daughter"),"subject_ids":("daughter",)},
])
def test_private_state_not_exported_to_project_household_or_other_person(changes):
    dev,_,_ = projection();assert completed(dev)
    assert dev.read_current(query(**changes)) is None


def test_same_state_reaches_exact_private_initiative_review():
    dev,_,_ = projection();assert completed(dev)
    ordinary = dev.read_current(query())
    initiative = dev.read_current(query(conversation_id="sensory:initiative-review",scope={
        "kind":"private_opportunity","source":"initiative-review","event_type":"private_opportunity_review"}))
    assert initiative == ordinary


def test_optional_projection_failure_cannot_repeat_successful_native_reflection(monkeypatch):
    calls = []
    class Failing:
        def after_reflection(self,*args):
            raise RuntimeError("local receipt unavailable")
        def read_current(self,query):
            raise RuntimeError("local read unavailable")
    memory = HindsightMemoryEngine(HindsightProjectionConfig("http://unused"),development_projection=Failing())
    receipt = object()
    monkeypatch.setattr(memory,"_maintain_person_scope",lambda *args:(calls.append(args),receipt)[1])
    assert memory.maintain_person_scope("house","eva",OWNER) is receipt
    assert len(calls) == 1
    assert with_current_development(empty_memory(),query(),Failing()).confirmed_facts == ()


def test_unobserved_or_other_person_reflection_does_not_create_experience():
    dev,data,store=projection()
    assert not dev.after_reflection("house","eva",OWNER,None)
    assert not dev.after_reflection("house","eva","daughter",SimpleNamespace(observation_error_type=None))
    assert store.writes == 0
    data["sources"] = [];data["projects"] = []
    assert not completed(dev) and store.writes == 0


def test_real_contact_scope_uses_same_current_projection_and_rejects_observation_binding():
    dev,_,_ = projection();assert completed(dev)
    contact = query(scope={"kind":"person","principal_id":OWNER})
    state = dev.read_current(contact)
    assert state == dev.read_current(query())
    assert dev.read_current(replace(contact,scope={**contact.scope,
        "observation_binding_sha256":"a"*64})) is None
    # Exercise the actual memory adapter's contact entry, without a model/network.
    memory = HindsightMemoryEngine(HindsightProjectionConfig("http://unused"),development_projection=dev)
    memory._require_private_recall_audience = lambda q:None
    current = memory.read_contact_context(contact)
    assert len(current.confirmed_facts) == 1
    assert current.confirmed_facts[0].predicate == "eva.development_state"


def test_failed_local_processing_remains_visible_after_successful_read():
    dev,_,store = projection();assert completed(dev)
    previous = store.save
    store.save = lambda *a,**k:(_ for _ in ()).throw(RuntimeError("storage unavailable"))
    assert not completed(dev) and dev.projection_error == "RuntimeError"
    assert dev.read_current(query()) is not None
    assert dev.last_error == "RuntimeError"
    store.save = previous
    completed(dev)
    assert dev.last_error is None


def test_contact_state_is_bounded_and_does_not_accumulate_prompt_history():
    dev,data,store=projection()
    data["sources"]=[source("A"*400,at=f"2026-10-24T09:00:0{i}Z") for i in range(3)]
    assert completed(dev)
    first=with_current_development(empty_memory(),query(),dev)
    assert len(first.confirmed_facts)==1
    second=with_current_development(first,query(),dev)
    assert len(second.confirmed_facts)==1
    assert len(second.confirmed_facts[0].value["text"].encode("utf-8")) <= 2048


def test_full_memory_budget_keeps_other_facts_and_skips_optional_read():
    from eva.contracts.planning import ConfirmedFact
    # Even a similar predicate without this projection's identity is foreign.
    foreign = ConfirmedFact(fact_id="other-memory",subject=OWNER,predicate="eva.development_state",
        value={"text":"An unrelated original source"},source_ref="another-source",evidence_ref="another-evidence",
        observed_at=NOW,trust="direct_user")
    original=replace(empty_memory(),confirmed_facts=(foreign,))
    class NoRead:
        def read_current(self,q):
            pytest.fail("optional state must not add reads when the budget is already full")
    projected=with_current_development(original,query(maximum_facts=1),NoRead())
    assert projected.confirmed_facts == (foreign,)


def test_restricted_recall_classes_cannot_widen_to_private_project_context():
    dev,_,_=projection();assert completed(dev)
    assert dev.read_current(query(allowed_privacy_classes=("public",))) is None
