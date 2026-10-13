"""Offline regression tests: equal pairs, interruption/readback and unknowns."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[2] / "benchmark/inspect_ai_v1"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("cp001", HERE / "cp001.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PairedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(runner.FIXTURES.read_text())
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def sample(self, task="A"):
        return next(s for s in runner.schedule(self.plan) if s["task"]["id"] == task)

    def test_same_inputs_budgets_clean_starts_alternating_order(self):
        schedule = runner.schedule(self.plan)
        self.assertEqual(len(schedule), 8)
        self.assertEqual(
            [s["participant"] for s in schedule[:4]],
            ["eva2", "eva1-bottie", "eva1-bottie", "eva2"],
        )
        for offset in (0, 2, 4, 6):
            pair = schedule[offset : offset + 2]
            left, right = [
                runner.execute_sample(s, self.plan, self.root / s["id"]) for s in pair
            ]
            self.assertEqual(left["answer"], right["answer"])
            for a, b in zip(left["phases"], right["phases"], strict=True):
                for field in ("request_sha256", "request_bytes", "timeout_seconds"):
                    self.assertEqual(a[field], b[field])
            self.assertTrue(all(runner.fixture_checks(pair[0]["task"], left).values()))
            self.assertEqual(left["product_result"], "unknown")
            self.assertEqual(left["usage"]["model_calls"], 0)
            self.assertIsNone(left["usage"]["money"])
            self.assertTrue(all(v is None for v in left["human_review"].values()))
        with self.assertRaises(FileExistsError):
            runner.execute_sample(schedule[0], self.plan, self.root / schedule[0]["id"])

    def test_interruption_survives_new_process_and_independent_tamper_is_caught(self):
        sample = self.sample("B")
        result = runner.execute_sample(sample, self.plan, self.root / "b")
        self.assertEqual(result["phases"][0]["exit_code"], 75)
        self.assertEqual(result["readback"]["mutations"], ["save"])
        self.assertTrue(all(runner.fixture_checks(sample["task"], result).values()))
        for field, bad in [
            ("content", "wrong"),
            ("sha256", "wrong"),
            ("mutations", ["save", "save"]),
            ("sources_unchanged", False),
        ]:
            corrupt = deepcopy(result)
            corrupt["readback"][field] = bad
            self.assertFalse(
                all(runner.fixture_checks(sample["task"], corrupt).values())
            )
        self.assertLessEqual(
            result["latency_ms"]["first_content"], result["latency_ms"]["verified_end"]
        )
        self.assertIsNone(result["latency_ms"]["acknowledgement"])

    def test_unknown_failure_timeout_are_visible_not_losses_or_retries(self):
        for fault, expected in [
            ("unavailable", "admission_failed"),
            ("failure", "execution_failed"),
            ("timeout", "timeout"),
        ]:
            self.plan["budgets"]["phase_timeout_seconds"] = (
                0.15 if fault == "timeout" else 3
            )
            result = runner.execute_sample(
                self.sample(), self.plan, self.root / fault, fault
            )
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["product_result"], "unknown")
            self.assertIsNone(
                runner.fixture_checks(self.sample()["task"], result)["fixture_contract"]
            )
            self.assertLessEqual(len(result["phases"]), 1)
            self.assertEqual(result["usage"]["retry_calls"], 0)
            self.assertIsNone(result["latency_ms"]["verified_end"])
            if fault == "timeout":
                self.assertTrue(result["phases"][0]["terminated_and_reaped"])

    def test_enforces_input_output_phase_and_total_time_budgets(self):
        for field, value in [
            ("input_bytes", 1),
            ("output_bytes", 1),
            ("phases", 0),
            ("sample_timeout_seconds", 0),
        ]:
            plan = deepcopy(self.plan)
            plan["budgets"][field] = value
            result = runner.execute_sample(self.sample(), plan, self.root / field)
            self.assertEqual(result["status"], "budget_exceeded")
            self.assertIsNone(result["latency_ms"]["verified_end"])

    def test_unreachable_does_not_launch_worker(self):
        with patch.object(
            runner.subprocess, "run", side_effect=AssertionError("must not launch")
        ):
            result = runner.execute_sample(
                self.sample(), self.plan, self.root / "absent", "unavailable"
            )
        self.assertEqual(result["phases"], [])

    def test_seal_rejects_mismatched_commit_before_output(self):
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            runner.run(self.root / "forbidden", "0" * 40)
        self.assertFalse((self.root / "forbidden").exists())

    def test_changed_fixture_bytes_are_rejected_before_execution(self):
        changed = self.root / "changed.json"
        changed.write_bytes(runner.FIXTURES.read_bytes() + b" ")
        with self.assertRaisesRegex(RuntimeError, "differs from Git"):
            runner.proof.verify_source_checkout(
                runner.proof.current_source_commit(),
                {"eva2/benchmark/inspect_ai_v1/cp001_fixtures.json": changed},
            )


if __name__ == "__main__":
    unittest.main()
