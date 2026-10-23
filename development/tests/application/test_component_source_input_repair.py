"""Invalid source selectors remain correctable within the existing task."""
import pytest
from eva.adapters.postgres.component_improvement_actions import ComponentImprovementActions
from eva.application.conversation import ConversationActionInputRejected, ConversationConflict


def test_invalid_resource_can_be_corrected_to_installed_source(tmp_path):
    source = tmp_path / "bootstrap"
    source.mkdir()
    (source / "camera_observer.py").write_text("VALUE = 1\n")
    adapter = ComponentImprovementActions.__new__(ComponentImprovementActions)
    adapter.root = tmp_path
    adapter.source_resolver = lambda *a, **k: pytest.fail("must not resolve an artifact")
    with pytest.raises(ConversationActionInputRejected) as caught:
        adapter._source("", resource_path="bootstrap/camera_observer.py")
    assert caught.value.fields == ("resource_path",)
    assert "omit resource_path" in caught.value.prompt
    result, binding = adapter._source("bootstrap/camera_observer.py", resource_path=None)
    assert result["content"] == "VALUE = 1\n"
    assert binding is None


def test_saved_reference_still_requires_existing_resolver():
    adapter = ComponentImprovementActions.__new__(ComponentImprovementActions)
    adapter.source_resolver = None
    with pytest.raises(ConversationConflict, match="route unavailable"):
        adapter._source("procedure-reference:" + "a" * 64, resource_path="scripts/read.py")


def test_installed_read_keeps_path_boundary(tmp_path):
    adapter = ComponentImprovementActions.__new__(ComponentImprovementActions)
    adapter.root = tmp_path
    with pytest.raises(ValueError, match="relative"):
        adapter._source("../outside.py")


@pytest.mark.parametrize("resource", [None, ""])
def test_execute_uses_normalized_optional_selectors(tmp_path, resource):
    from tests.application.test_scoped_component_evaluation_actions import adapter
    from eva.adapters.postgres.component_improvement_actions import READ
    from eva.application.conversation import ConversationActionCall
    component, checks, _ = adapter()
    component.action_names = (READ,)
    component.root = tmp_path
    component.evaluator = None
    component._source = ComponentImprovementActions._source.__get__(component)
    (tmp_path / "example.py").write_text("VALUE = 1\n")
    call = ConversationActionCall("read", READ,
        {"path": "example.py", "resource_path": resource, "start_line": None})
    result = component.execute(component.source, call, step_index=3)
    assert result.call == call
    assert result.output["operation_result"]["content"] == "VALUE = 1\n"
    assert component.execute(component.source, call, step_index=3) == result
    assert sum(kind == "dispatch" for kind, turn in checks) == 1


def test_absent_runtime_source_is_correctable_without_claiming_module_absent(tmp_path):
    component = ComponentImprovementActions.__new__(ComponentImprovementActions)
    component.root = tmp_path
    with pytest.raises(ConversationActionInputRejected) as error:
        component._source("bootstrap/not_installed.py")
    assert error.value.fields == ("path",)
    listing, _ = component._source("")
    assert listing["kind"] == "directory"
