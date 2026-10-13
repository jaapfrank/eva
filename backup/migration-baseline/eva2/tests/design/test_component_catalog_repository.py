from __future__ import annotations

from pathlib import Path

from tests.design.test_component_catalog import _checker


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_repository_catalog_has_one_owner_for_every_tracked_scoped_path() -> None:
    assert _checker().check(REPOSITORY_ROOT) == []
