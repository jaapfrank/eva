"""Operator entrypoint for the existing image-only runtime updater."""
import argparse
import json
from pathlib import Path
from eva.operations.runtime_image_update import (
    _command,
    require_current_storage,
    same_mounts,
    update_runtime,
    InstallerRuntimeUpdate,
    image_update_services,
)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = update_runtime(**vars(args))
    print(json.dumps({"status": result["status"], "services": list(result["services"])}))


if __name__ == "__main__":
    main()
