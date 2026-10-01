#!/usr/bin/env python3
"""Open the packaged installer using its pinned interpreter."""
from pathlib import Path

from tools.launch_packaged_installer import main


if __name__ == "__main__":
    main(package_root=Path(__file__).resolve().parent)
