from copy import deepcopy
from types import SimpleNamespace as NS
import hashlib

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import _registered_lifecycle_origin, _READINESS


def test_technical_zero_contact_requires_exact_original_source_and_spec():
    source = {"tenant_id":"tenant","installation_id":"installation","principal_id":"owner",
        "conversation_id":"technical-session","message_id":"technical-message"}
    spec = {"source":source,"work_id":"work:technical","owner_principal_id":"owner",
        "principal_binding_id":"binding","source_ingress_sha256":"1"*64,
        "task":"Improve actual work","created_at":"2026-10-12T00:00:00Z"}
    scope = canonical_sha256({k:source[k] for k in ("tenant_id","installation_id","principal_id")})
    turn = NS(project_id="component-work-evaluation:"+"2"*64+":construction")
    origin = {"kind":"registered_lifecycle_technical_probe","stage":"construction",
        "registration_sha256":"2"*64,"resources_sha256":"3"*64,
        "runtime_admission_sha256":"4"*64,"input_sha256":"5"*64,"scope_sha256":scope,
        "identity":{"task_id":spec["work_id"],"conversation_id":source["conversation_id"],
            "message_id":source["message_id"],"source_ingress_sha256":spec["source_ingress_sha256"]},
        "actor":dict(source,principal_binding_id="binding"),
        "spec_sha256":canonical_sha256(spec),
        "task_sha256":hashlib.sha256(spec["task"].encode()).hexdigest(),
        "project_id":turn.project_id,"ownerAuthoredText":False,
        "selfChosenInitiative":False,"foreground_contact_used":False}
    assert _registered_lifecycle_origin(origin,spec,turn,scope)
    for key,value in [("spec_sha256","0"*64),("task_sha256","0"*64),
                      ("project_id","ordinary-owner"),("ownerAuthoredText",True),
                      ("foreground_contact_used",True)]:
        altered = deepcopy(origin); altered[key] = value
        assert not _registered_lifecycle_origin(altered,spec,turn,scope)
    assert not _registered_lifecycle_origin(None,spec,turn,scope)
    alien = deepcopy(spec); alien["source"]["principal_id"] = "another-person"
    assert not _registered_lifecycle_origin(origin,alien,turn,scope)


def test_actual_core_readiness_program_compiles_without_running():
    compile(_READINESS,"actual-core-readiness-program","exec")
