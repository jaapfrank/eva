from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPOSITORY_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import finalize_4a_evidence as finalizer  # noqa: E402
import run_gate as gates  # noqa: E402
from verify_source_bindings import VERIFICATION_CONTRACT, canonical_json_bytes  # noqa: E402


class GateFailClosedTests(unittest.TestCase):
    def _root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "evidence/4a").mkdir(parents=True)
        (root / "seed.txt").write_text("sealed input\n", encoding="utf-8")
        for relative in gates.LOCK_INPUTS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {relative}\n", encoding="utf-8")
        return temporary, root

    @staticmethod
    def _source_receipt() -> dict[str, object]:
        return {
            "contract": VERIFICATION_CONTRACT,
            "status": "passed",
            "expectedCount": 1,
            "actualCount": 1,
            "manifestPath": "release/design-source-bindings.json",
            "manifestSha256": "1" * 64,
            "bindingsFingerprint": "2" * 64,
            "errors": [],
        }

    def _source_verifier(self, _root: Path) -> dict[str, object]:
        return dict(self._source_receipt())

    @staticmethod
    def _environment_verifier(_root: Path) -> dict[str, object]:
        return {
            "checkerInterpreter": {"fixture": True},
            "declaredLockInputs": [],
            "distributionCount": 0,
            "distributionFingerprint": "0" * 64,
            "distributions": [],
            "dependenciesInstalledByGate": False,
            "dependencyResolutionPerformedByGate": False,
            "externalEnvironmentVerified": True,
            "networkUsedByGate": False,
            "toolchainLockPath": "release/gate-toolchain.lock.json",
            "toolchainLockSha256": "0" * 64,
        }

    @staticmethod
    def _commands(
        *, failed_gate: str | None = None, missing_gate: str | None = None
    ) -> dict[str, tuple[gates.CheckSpec, ...]]:
        result: dict[str, tuple[gates.CheckSpec, ...]] = {}
        for gate in gates.SUPPORTED_GATES:
            return_code = 7 if gate == failed_gate else 0
            required = ("missing-checker.py",) if gate == missing_gate else ()
            result[gate] = (
                gates.CheckSpec(
                    f"{gate.lower()}-check",
                    ("{python}", "-c", f"raise SystemExit({return_code})"),
                    required,
                ),
            )
        return result

    def test_cli_surface_has_exactly_three_gate_ids(self) -> None:
        self.assertEqual(
            (
                "CONTRACT-01",
                "PLATFORM-DESIGN-01",
                "SUPPLY-DESIGN-01",
            ),
            gates.SUPPORTED_GATES,
        )

    def test_current_requirement_locks_are_declared_gate_inputs(self) -> None:
        toolchain = json.loads(
            (REPOSITORY_ROOT / "release/gate-toolchain.lock.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("requirements-4d.lock", gates.LOCK_INPUTS)
        self.assertTrue(
            {row["path"] for row in toolchain["requirementLocks"]}.issubset(
                gates.LOCK_INPUTS
            )
        )

    def test_unknown_gate_is_rejected_before_evidence(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(gates.GateError):
            gates.run_gate(
                root,
                "LIVE-01",
                commands=self._commands(),
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        self.assertEqual([], list((root / "evidence/4a").iterdir()))

    def test_repository_local_gate_environment_is_rejected(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        (root / ".gate-venv").mkdir()
        with self.assertRaisesRegex(
            gates.GateError, "execution environment is forbidden"
        ):
            gates.build_input_snapshot(root)

    def test_tool_caches_are_not_release_inputs(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        expected = gates.build_input_snapshot(root)
        for relative in (
            ".ruff_cache/0.15.10/cache-entry",
            ".mypy_cache/3.12/cache-entry",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("disposable cache\n", encoding="utf-8")

        actual = gates.build_input_snapshot(root)

        self.assertEqual(expected, actual)

    def test_missing_prerequisite_is_not_proven_and_nonzero(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        exit_code, path, result = gates.run_gate(
            root,
            "CONTRACT-01",
            commands=self._commands(missing_gate="CONTRACT-01"),
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual(2, exit_code)
        self.assertEqual("not_proven", result["status"])
        self.assertEqual("not_proven", result["checks"][0]["status"])
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stem.rsplit("-", 1)[1]
        )

    def test_failed_checker_is_failed_and_nonzero(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        exit_code, _path, result = gates.run_gate(
            root,
            "PLATFORM-DESIGN-01",
            commands=self._commands(failed_gate="PLATFORM-DESIGN-01"),
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual(1, exit_code)
        self.assertEqual("failed", result["status"])
        self.assertEqual(7, result["checks"][0]["returnCode"])

    def test_source_or_input_change_during_run_fails_integrity(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        calls = 0

        def changing_source(_root: Path) -> dict[str, object]:
            nonlocal calls
            calls += 1
            receipt = self._source_receipt()
            if calls > 1:
                receipt["bindingsFingerprint"] = "3" * 64
            return receipt

        exit_code, _path, result = gates.run_gate(
            root,
            "CONTRACT-01",
            commands=self._commands(),
            source_verifier=changing_source,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual(1, exit_code)
        self.assertEqual("failed", result["status"])
        self.assertFalse(result["integrity"]["sourceBindingsStableDuringRun"])

        mutating_commands = self._commands()
        mutating_commands["SUPPLY-DESIGN-01"] = (
            gates.CheckSpec(
                "supply-design-01-check",
                (
                    "{python}",
                    "-c",
                    "from pathlib import Path; Path('changed.txt').write_text('changed')",
                ),
            ),
        )
        exit_code, _path, result = gates.run_gate(
            root,
            "SUPPLY-DESIGN-01",
            commands=mutating_commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual(1, exit_code)
        self.assertFalse(result["integrity"]["inputsStableDuringRun"])

    def _run_three_passed(
        self, root: Path, commands: dict[str, tuple[gates.CheckSpec, ...]]
    ) -> list[Path]:
        paths: list[Path] = []
        for gate in gates.SUPPORTED_GATES:
            exit_code, path, result = gates.run_gate(
                root,
                gate,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
            self.assertEqual(0, exit_code, result)
            paths.append(path)
        return paths

    def test_finalizer_emits_index_only_for_three_current_passes(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        commands = self._commands()
        paths = self._run_three_passed(root, commands)
        index_path, index = finalizer.finalize(
            root,
            commands=commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual("passed", index["status"])
        self.assertEqual("design_evidence_only", index["acceptance"])
        self.assertEqual(3, len(index["gates"]))
        self.assertTrue(index["claimBoundary"]["designOnly"])
        for claim in ("liveClaim", "migrationClaim", "installerClaim", "supportClaim"):
            self.assertIs(False, index["claimBoundary"][claim])
        self.assertEqual(canonical_json_bytes(index), index_path.read_bytes())
        self.assertEqual(
            hashlib.sha256(index_path.read_bytes()).hexdigest(),
            index_path.stem.rsplit("-", 1)[1],
        )
        self.assertEqual(
            {"passed"}, {json.loads(path.read_text())["status"] for path in paths}
        )

    def test_repeated_green_run_has_the_same_content_address(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        commands = self._commands()
        first_exit, first_path, _first = gates.run_gate(
            root,
            "CONTRACT-01",
            commands=commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        second_exit, second_path, _second = gates.run_gate(
            root,
            "CONTRACT-01",
            commands=commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        self.assertEqual(0, first_exit)
        self.assertEqual(0, second_exit)
        self.assertEqual(first_path, second_path)

    def test_finalizer_rejects_missing_stale_and_tampered_evidence(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        commands = self._commands()
        for gate in gates.SUPPORTED_GATES[:2]:
            gates.run_gate(
                root,
                gate,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        with self.assertRaises(finalizer.FinalizationError):
            finalizer.finalize(
                root,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        self.assertEqual([], list((root / "evidence/4a").glob("4a-index-*.json")))

        _exit, tampered, _result = gates.run_gate(
            root,
            "SUPPLY-DESIGN-01",
            commands=commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        tampered.write_bytes(tampered.read_bytes() + b" ")
        with self.assertRaises(finalizer.FinalizationError):
            finalizer.finalize(
                root,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        self.assertEqual([], list((root / "evidence/4a").glob("4a-index-*.json")))

        tampered.unlink()
        gates.run_gate(
            root,
            "SUPPLY-DESIGN-01",
            commands=commands,
            source_verifier=self._source_verifier,
            environment_verifier=self._environment_verifier,
        )
        (root / "new-input.txt").write_text("new input\n", encoding="utf-8")
        with self.assertRaises(finalizer.FinalizationError):
            finalizer.finalize(
                root,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        self.assertEqual([], list((root / "evidence/4a").glob("4a-index-*.json")))

    def test_finalizer_rejects_a_current_failed_gate(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        commands = self._commands(failed_gate="SUPPLY-DESIGN-01")
        for gate in gates.SUPPORTED_GATES:
            gates.run_gate(
                root,
                gate,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        with self.assertRaises(finalizer.FinalizationError):
            finalizer.finalize(
                root,
                commands=commands,
                source_verifier=self._source_verifier,
                environment_verifier=self._environment_verifier,
            )
        self.assertEqual([], list((root / "evidence/4a").glob("4a-index-*.json")))


if __name__ == "__main__":
    unittest.main()
