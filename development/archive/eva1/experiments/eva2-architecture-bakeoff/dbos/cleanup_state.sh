#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "gebruik: $0 STATE_DIR" >&2
  exit 2
fi
if [[ ! -d "$1" ]]; then
  echo "state-dir bestaat niet: $1" >&2
  exit 2
fi
state_dir=$(cd "$1" && pwd -P)
if [[ "$state_dir" != /tmp/* ]]; then
  echo "DBOS bakeoff-state moet onder /tmp staan: $state_dir" >&2
  exit 2
fi
if [[ ! -e "$state_dir/dbos-postgres.json" ]]; then
  exit 0
fi

candidate_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_tool="$candidate_root/dbos_state.py"
postgres_image=$(python3 "$state_tool" image)
postgres_image_id=$(docker image inspect "$postgres_image" --format '{{.Id}}')
readarray -t state < <(
  python3 "$state_tool" values "$state_dir" "$postgres_image_id"
)
postgres_data_dir=${state[7]}
binding_sha256=${state[6]}

"$candidate_root/dbos/remove_container.sh" "$state_dir"
cleaner_name="eva2-dbos-clean-${binding_sha256:0:20}"
host_uid=$(id -u)
host_gid=$(id -g)
docker run --rm \
  --name "$cleaner_name" \
  --network none \
  --user 0 \
  --entrypoint /bin/sh \
  --mount "type=bind,src=$postgres_data_dir,dst=/state" \
  "$postgres_image" \
  -c 'find /state -mindepth 1 -delete && chown "$1:$2" /state && chmod 700 /state' \
  cleanup "$host_uid" "$host_gid"
python3 "$state_tool" forget "$state_dir" "$postgres_image_id"
echo "DBOS PostgreSQL-container, credentials en datadir opgeruimd: $state_dir" >&2
