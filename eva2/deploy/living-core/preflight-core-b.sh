#!/bin/sh
# Read-only: this wrapper has no apply/recreate path.
set -eu

if [ "$#" -eq 1 ]; then
    mode=managed
    environment_file=$1
elif [ "$#" -eq 2 ] && [ "$1" = "--host-only" ]; then
    mode=host_only
    environment_file=$2
else
    printf '%s\n' "usage: $0 [--host-only] /absolute/path/to/eva2-b-runtime.env" >&2
    exit 2
fi

case "$environment_file" in
    /*) ;;
    *)
        printf '%s\n' "the B environment-file path must be absolute" >&2
        exit 2
        ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
eva2_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

# Do not preserve HOME, Docker variables, daemon variables, or shell config.
if [ "$mode" = host_only ]; then
    exec env -i \
        LC_ALL=C \
        PYTHONPATH="$eva2_root/src:$eva2_root" \
        /usr/bin/python3 -m eva.operations.core_b_preflight --host-only "$environment_file"
fi

exec env -i \
    LC_ALL=C \
    PYTHONPATH="$eva2_root/src:$eva2_root" \
    /usr/bin/python3 -m eva.operations.core_b_preflight "$environment_file"
