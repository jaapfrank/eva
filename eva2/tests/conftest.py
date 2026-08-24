from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import tempfile

import pytest


@pytest.fixture
def private_tmp_path() -> Iterator[Path]:
    """Use the native Linux filesystem so POSIX ownership/mode tests are real."""

    with tempfile.TemporaryDirectory(prefix="eva2-secret-", dir="/tmp") as directory:
        yield Path(directory)
