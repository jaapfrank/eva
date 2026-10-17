#!/usr/bin/env python3
"""Operator entrypoint for the shared reviewed component source importer."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from eva.operations.component_release_import import (
    digest,
    canonical,
    load_object,
    git,
    checked_evidence,
    _active_context_surroundings,
    _work_strategy_surroundings,
    prepare,
    _ACTIVE_CONTEXT_PATH,
    _WORK_STRATEGY_PATH,
    ALLOWED_PATHS,
)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target, baseline, raw, receipt = prepare(args.package, args.review, args.checkout)
    if args.apply:
        if args.receipt is None:
            parser.error("--apply requires a new --receipt path outside the release checkout")
        if args.receipt.resolve().is_relative_to(args.checkout.resolve()):
            parser.error("receipt must be outside the release checkout")
        # Reserve a new receipt before changing source; never overwrite evidence.
        with args.receipt.open("x", encoding="utf-8") as output:
            with target.open("r+b") as source:
                if source.read() != baseline:
                    raise ValueError("source changed immediately before import")
                source.seek(0)
                source.write(raw)
                source.truncate()
            receipt["state"] = "source_imported_not_installed"
            output.write(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
