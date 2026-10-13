from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[3] / "src/tools" / "run_pc05_text_latency.py"
SPEC = importlib.util.spec_from_file_location("run_pc05_text_latency", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_corpus_has_stable_cold_warm_pairs() -> None:
    corpus = MODULE.build_corpus(turns=200, run_seed=42)

    assert len(corpus) == 200
    assert sum(turn.phase == "cold" for turn in corpus) == 100
    assert sum(turn.phase == "warm" for turn in corpus) == 100
    assert corpus[0].principal_id == corpus[1].principal_id
    assert corpus[0].message_id != corpus[1].message_id
    assert corpus[2].principal_id != corpus[0].principal_id
    assert len({turn.message_id for turn in corpus}) == 200


def test_corpus_rejects_invalid_shape_and_seed() -> None:
    for turns, seed in ((1, 1), (3, 1), (200, 0), (200, 10_000)):
        try:
            MODULE.build_corpus(turns=turns, run_seed=seed)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid corpus settings were accepted")


def test_first_complete_token_requires_a_boundary() -> None:
    assert MODULE.first_complete_token("SNEL") is False
    assert MODULE.first_complete_token("SNEL ") is True
    assert MODULE.first_complete_token("SNEL klaar.") is True


def test_exact_reply_allows_only_one_terminal_period() -> None:
    assert MODULE.normalized_exact_reply("SNEL klaar") == "SNEL klaar"
    assert MODULE.normalized_exact_reply("SNEL klaar.") == "SNEL klaar"
    assert MODULE.normalized_exact_reply('"SNEL klaar"') == "SNEL klaar"
    assert MODULE.normalized_exact_reply("SNEL klaar!") != "SNEL klaar"
    assert MODULE.normalized_exact_reply("SNEL klaar..") != "SNEL klaar"


def test_nearest_rank_percentile_keeps_failures_in_denominator() -> None:
    values = [100] * 19 + [20_000]

    assert MODULE.percentile(values, 0.50) == 100
    assert MODULE.percentile(values, 0.95) == 100
    assert MODULE.percentile(values, 1.0) == 20_000


def test_summary_counts_failures_and_target_misses() -> None:
    observations = [
        {
            "success": True,
            "firstContentMilliseconds": 900,
            "completeMilliseconds": 1000,
        },
        {
            "success": False,
            "firstContentMilliseconds": 20_000,
            "completeMilliseconds": 20_000,
        },
    ]

    summary = MODULE.summarize(observations, target_ms=2000)

    assert summary["turns"] == 2
    assert summary["successfulTurns"] == 1
    assert summary["failedTurns"] == 1
    assert summary["turnsWithinTarget"] == 1
    assert summary["firstContentMaximumMilliseconds"] == 20_000
