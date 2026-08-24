#!/bin/sh
# Read-only: this wrapper has no apply/recreate path.
set -eu

if [ "$#" -ne 1 ]; then
    printf '%s\n' "usage: $0 /absolute/path/to/eva2-b-runtime.env" >&2
    exit 2
fi

case "$1" in
    /*) ;;
    *)
        printf '%s\n' "the B environment-file path must be absolute" >&2
        exit 2
        ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
eva2_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

# Do not preserve HOME, Docker variables, daemon variables, or shell config.
exec env -i \
    LC_ALL=C \
    PYTHONPATH="$eva2_root/src:$eva2_root" \
    /usr/bin/python3 -m eva.operations.core_b_preflight "$1"
