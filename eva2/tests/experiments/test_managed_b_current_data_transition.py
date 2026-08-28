"""Regression checks for the current-route managed-B data transition probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "experiments" / "managed-b-clean-graph-v1" / "probe.py"
SPEC = importlib.util.spec_from_file_location("managed_b_clean_graph_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_count_window_accepts_one_consistent_logical_snapshot() -> None:
    probe._validate_count_window(
        {"public.a": 4, "public.b": 7},
        {"public.a": 5, "public.b": 7},
        {"public.a": 6, "public.b": 7},
    )


@pytest.mark.parametrize(
    ("before", "restored", "after"),
    [
        ({"public.a": 4}, {"public.a": 7}, {"public.a": 6}),
        ({"public.a": 4}, {"public.b": 4}, {"public.a": 4}),
        ({"public.a": 4}, {"public.a": 4}, {"public.a": 4, "public.b": 0}),
    ],
)
def test_count_window_rejects_out_of_window_or_changed_tables(
    before: dict[str, int], restored: dict[str, int], after: dict[str, int]
) -> None:
    with pytest.raises(probe.ProbeError):
        probe._validate_count_window(before, restored, after)


def test_count_parser_is_closed_and_digest_is_order_independent() -> None:
    assert probe._parse_counts("public.b=2\npublic.a=1\n") == {
        "public.a": 1,
        "public.b": 2,
    }
    assert probe._count_digest({"public.a": 1, "public.b": 2}) == (
        probe._count_digest({"public.b": 2, "public.a": 1})
    )
    with pytest.raises(probe.ProbeError):
        probe._parse_counts("public.a=1\npublic.a=2\n")


def test_current_route_clone_is_logical_read_only_and_disposable() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")

    assert 'CURRENT_ROUTE_CORE = "eva2-candidate-lease-core"' in source
    assert 'CURRENT_ROUTE_POSTGRES = "eva2-candidate-794ff73-pg"' in source
    assert 'CURRENT_ROUTE_HINDSIGHT = "eva2-hindsight-memory-live"' in source
    assert 'CURRENT_ROUTE_PORT = "44706"' in source
    assert "--format=custom" in source
    assert '"--exit-on-error"' in source
    assert '"--no-owner"' in source
    assert '"--no-privileges"' in source
    assert '"down", "--volumes", "--remove-orphans"' in source
    assert "--clone-current-route" in source
    assert "docker stop eva2-candidate" not in source
    assert "docker rm eva2-candidate" not in source
    assert "docker volume rm eva2-candidate" not in source

