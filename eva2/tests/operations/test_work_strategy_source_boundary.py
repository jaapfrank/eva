"""Only a pure worker-strategy literal can pass the existing evaluator/importer."""
import ast
import base64
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eva.bootstrap.delegated_digital_runner import _TaskModel
from eva.operations import component_evaluation as evaluation
from eva.operations.component_evaluation import CandidateRequest, ComponentEvaluator, EvaluationConfig, WORK_STRATEGY_PATH
from tools import import_component_release as importer
from tests.operations.test_import_component_release import release_factory, prepare, save_package


DEFAULT = ("Bij grotere opdrachten: bewaar een beknopt uitvoerplan met save_work_progress, "
    "werk de stappen bij na betekenisvolle voortgang en noteer de eerstvolgende actie "
    "voor hervatten. Besteed geen plan-call aan een eenvoudige opdracht. Een opgeslagen "
    "plan is nog geen resultaat; voer het werk daadwerkelijk uit. Bij een blokkade "
    "bewaar je de stand en benoem je wat nodig is. Bestaande budgetten blijven gelden. ")
BASE = """# preserved UTF-8: café
HOST_TASK = "unchanged host task"
class _TaskModel:
    def _work_strategy(self):
        return "before"

    def generate(self, *args, **kwargs):
        return self.model.generate(*args, **kwargs)
# preserved trailing bytes
"""
IMAGE = "sha256:" + "a" * 64
COMMAND = ("/usr/local/bin/python", "-I", "-c", "fixed-independent-suite")


def literal(text):
    return BASE.replace('return "before"', 'return ' + repr(text))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def leg(stdout=""):
    return {"exit_code":0,"timed_out":False,"stdout_truncated":False,"stderr_truncated":False,
        "stdout":stdout,"stderr":"","elapsed_ms":1}


@pytest.mark.parametrize("extra_context", [False, True])
def test_standard_self_context_and_call_order_exactly_preserve_previous_generate(extra_context):
    source = Path(inspect.getsourcefile(_TaskModel)).read_text()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "_TaskModel")
    generated = deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "generate"))
    changed = []
    class RestoreLiteral(ast.NodeTransformer):
        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self" and node.func.attr == "_work_strategy"):
                assert not node.args and not node.keywords
                changed.append(True)
                return ast.copy_location(ast.Constant(DEFAULT), node)
            return self.generic_visit(node)
    original = RestoreLiteral().visit(generated)
    # Independently pinned AST before the extraction, including every string/arg/order.
    assert sha(ast.dump(original, include_attributes=False).encode()) == "495127a5a0ac5ead4ecc2e0257c9f1f3c89ec32f523dfc4f2294d0746df05c92"
    assert changed == [True]
    captured = []
    model = SimpleNamespace(generate=lambda *a, **kw: captured.append((a, kw)) or "same-result")
    task = _TaskModel(model, "exact host task",
        browser_context=(lambda: ("browser one", "browser two")) if extra_context else None,
        corrections=({"instruction":"owner correction"},) if extra_context else (),
        budget_context=(lambda: "host budget") if extra_context else None,
        attempt_work_id="work:current" if extra_context else None)
    task.continuation_feedback = ("existing continuation",) if extra_context else ()
    inherited = ("existing context",) if extra_context else ()
    assert task._work_strategy() == DEFAULT
    assert task.generate("input", self_context=inherited, marker="unchanged") == "same-result"
    args, kwargs = captured[0]
    assert args == ("input",) and kwargs["marker"] == "unchanged"
    context = kwargs["self_context"]
    task_index = 7 if extra_context else 0
    assert context[task_index] == "Je voert nu deze aangenomen achtergrondopdracht uit: exact host task"
    assert context[task_index + 2] == DEFAULT and context.count(DEFAULT) == 1
    if extra_context:
        assert context[:4] == ("existing context", "browser one", "browser two", "host budget")
        assert context[4].startswith("Huidige uitvoerpoging: work:current")
        assert context[5].endswith("owner correction") and context[6] == "existing continuation"


@pytest.mark.parametrize("old,new", [
    ('HOST_TASK = "unchanged host task"', 'HOST_TASK = "new authority"'),
    ('# preserved UTF-8: café', '# changed UTF-8: café'),
    ('return self.model.generate', 'return self.provider.generate'),
    ('self, *args, **kwargs', 'self, *args, budget=None, **kwargs'),
    ('def _work_strategy(self)', 'def _work_strategy(self, task="other")'),
    ('# preserved trailing bytes', '    def added(self):\n        return "other"\n# preserved trailing bytes'),
])
def test_importer_and_evaluator_reject_every_change_outside_literal_body(release_factory, old, new):
    candidate = literal("a useful candidate strategy").replace(old, new)
    with pytest.raises(ValueError, match="outside the work strategy body"):
        evaluation.validate_work_strategy_candidate(BASE.encode(), candidate.encode())
    release = release_factory(BASE, candidate, WORK_STRATEGY_PATH)
    with pytest.raises(ValueError, match="outside the work strategy body"):
        prepare(release)


@pytest.mark.parametrize("body", [
    'return self.task', 'return str("candidate")', 'return "a" + "b"', 'return f"{self.task}"',
    'import os\n        return "candidate"', 'x = "candidate"\n        return x',
    '"docstring"\n        return "candidate"', 'return None', 'return ""', 'return "   "',
    'return ' + repr("x" * 4097), 'return ' + repr("€" * 1400),
])
def test_executable_or_unbounded_body_refused_before_any_candidate_exec(release_factory, body):
    candidate = BASE.replace('return "before"', body)
    with pytest.raises(ValueError, match="bounded nonempty literal"):
        evaluation.validate_work_strategy_candidate(BASE.encode(), candidate.encode())
    release = release_factory(BASE, candidate, WORK_STRATEGY_PATH)
    with pytest.raises(ValueError, match="bounded nonempty literal"):
        prepare(release)


@pytest.mark.parametrize("candidate", [
    literal("candidate").replace("def _work_strategy", "async def _work_strategy"),
    literal("candidate").replace("    def generate", "    def _work_strategy"),
    literal("candidate") + "\nclass _TaskModel:\n    pass\n",
])
def test_wrong_or_extra_methods_classes_are_rejected(release_factory, candidate):
    with pytest.raises(ValueError, match="work strategy requires"):
        evaluation.validate_work_strategy_candidate(BASE.encode(), candidate.encode())
    release = release_factory(BASE, candidate, WORK_STRATEGY_PATH)
    with pytest.raises(ValueError, match="work strategy requires"):
        prepare(release)


def test_importer_rechecks_exact_baseline_before_body_admission(release_factory):
    release = release_factory(BASE, literal("candidate"), WORK_STRATEGY_PATH)
    release["target"].write_text(BASE.replace("café", "cafe"))
    with pytest.raises(ValueError, match="release baseline changed"):
        prepare(release)


def test_real_importer_applies_valid_literal_to_disposable_checkout_without_running_it(release_factory, monkeypatch, capsys):
    baseline = Path(inspect.getsourcefile(_TaskModel)).read_text()
    candidate = baseline.replace('return ("Bij grotere opdrachten:', 'return ("Bij grotere eigen opdrachten:', 1)
    release = release_factory(baseline, candidate, WORK_STRATEGY_PATH)
    receipt_file = release["review_file"].parent / "source-import.json"
    monkeypatch.setattr("sys.argv", ["import_component_release.py", "--package", str(release["package_file"]),
        "--review", str(release["review_file"]), "--checkout", str(release["checkout"]),
        "--receipt", str(receipt_file), "--apply"])
    # Git metadata is supplied by the existing isolated release fixture; the
    # actual importer validates bindings/bytes, reserves receipt and writes source.
    importer.main()
    capsys.readouterr()
    assert release["target"].read_bytes() == candidate.encode()
    receipt = json.loads(receipt_file.read_text())
    assert receipt["state"] == "source_imported_not_installed" and receipt["installed"] is False
    assert receipt["baseline_sha256"] == sha(baseline.encode()) and receipt["candidate_sha256"] == sha(candidate.encode())
    assert importer._work_strategy_surroundings(baseline.encode()) == importer._work_strategy_surroundings(candidate.encode())


def configured_evaluator(monkeypatch, baseline=BASE, readback=None):
    calls = []
    monkeypatch.setattr(evaluation, "_capture", lambda *args, **kwargs: leg(IMAGE))
    evaluator = ComponentEvaluator(EvaluationConfig(IMAGE, COMMAND))
    def container(image, command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            assert command[-1] == "/opt/eva/src/eva/" + WORK_STRATEGY_PATH
            assert kwargs["output_limit"] == 90112
            return leg(json.dumps(readback or {"sha256":sha(baseline.encode()),
                "source_base64":base64.b64encode(baseline.encode()).decode()}))
        assert tuple(command) == COMMAND
        if "mount" in kwargs:
            actual, target = kwargs["mount"]
            assert target == "/opt/eva/src/eva/" + WORK_STRATEGY_PATH
            assert Path(actual).stat().st_mode & 0o777 == 0o444
            evaluation.validate_work_strategy_candidate(baseline.encode(), Path(actual).read_bytes())
        return leg("fixed result")
    monkeypatch.setattr(evaluator, "_container", container)
    return evaluator, calls


@pytest.mark.parametrize("variant", ["valid", "outside", "executable", "wrong_baseline", "false_readback", "missing_readback"])
def test_existing_fixed_evaluator_checks_exact_source_boundary_before_running_either_suite(monkeypatch, variant):
    content = literal("candidate")
    if variant == "outside": content = content.replace('HOST_TASK = "unchanged host task"', 'HOST_TASK = "tampered"')
    if variant == "executable": content = BASE.replace('return "before"', 'return self.task')
    readback = None
    if variant == "false_readback": readback = {"sha256":sha(BASE.encode()), "source_base64":base64.b64encode(b"wrong bytes").decode()}
    if variant == "missing_readback": readback = {"sha256":sha(BASE.encode())}
    evaluator, calls = configured_evaluator(monkeypatch, readback=readback)
    request = CandidateRequest(WORK_STRATEGY_PATH, "b" * 64 if variant == "wrong_baseline" else sha(BASE.encode()), content)
    if variant == "valid":
        report = evaluator.evaluate(request)
        assert len(calls) == 3 and "mount" not in calls[1][1]
        assert report["relative_path"] == WORK_STRATEGY_PATH
        assert report["baseline_sha256"] == request.baseline_sha256
        assert report["candidate_sha256"] == sha(content.encode())
        assert report["command"] == list(COMMAND) and report["semantic_improvement_verified"] is False
    else:
        with pytest.raises(ValueError): evaluator.evaluate(request)
        assert len(calls) == 1  # Neither baseline suite nor candidate was executed.


def test_literal_maximum_and_crlf_utf8_outside_bytes_are_preserved(release_factory):
    baseline = BASE.replace("\n", "\r\n")
    candidate = baseline.replace('return "before"', 'return ' + repr("é" * 2048))
    evaluation.validate_work_strategy_candidate(baseline.encode(), candidate.encode())
    release = release_factory(baseline, candidate, WORK_STRATEGY_PATH)
    assert prepare(release)[2] == candidate.encode()
