from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connector_sdk.fixture_runner import worker_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(worker_main("channel"))
