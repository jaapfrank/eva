#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "gebruik: $0 STATE_DIR" >&2
  exit 2
fi
candidate_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_tool="$candidate_root/dbos_state.py"
postgres_image=$(python3 "$state_tool" image)
postgres_image_id=$(docker image inspect "$postgres_image" --format '{{.Id}}')
readarray -t state < <(
  python3 "$state_tool" values "$1" "$postgres_image_id"
)
container_name=${state[0]}
state_id=${state[1]}
image_digest=${state[3]}
bound_image_id=${state[4]}
credentials_sha256=${state[5]}
binding_sha256=${state[6]}
postgres_data_dir=${state[7]}
password_file=${state[8]}

if ! docker inspect "$container_name" >/dev/null 2>&1; then
  echo "DBOS PostgreSQL-container was al afwezig; state blijft bewaard" >&2
  exit 0
fi
actual_state_id=$(docker inspect --format \
  '{{ index .Config.Labels "eva2.dbos.state-id" }}' "$container_name")
actual_digest=$(docker inspect --format \
  '{{ index .Config.Labels "eva2.dbos.image-digest" }}' "$container_name")
actual_credentials=$(docker inspect --format \
  '{{ index .Config.Labels "eva2.dbos.credentials-sha256" }}' "$container_name")
actual_binding=$(docker inspect --format \
  '{{ index .Config.Labels "eva2.dbos.binding-sha256" }}' "$container_name")
actual_image_id=$(docker inspect --format '{{.Image}}' "$container_name")
actual_data_dir=$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}' \
  "$container_name")
actual_password_file=$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/run/secrets/eva2-postgres-password"}}{{.Source}}{{end}}{{end}}' \
  "$container_name")
if [[ "$actual_state_id" != "$state_id" \
  || "$actual_digest" != "$image_digest" \
  || "$actual_credentials" != "$credentials_sha256" \
  || "$actual_binding" != "$binding_sha256" \
  || "$actual_image_id" != "$bound_image_id" \
  || "$actual_data_dir" != "$postgres_data_dir" \
  || "$actual_password_file" != "$password_file" ]]; then
  echo "container wijkt af van sealed state; niets verwijderd" >&2
  exit 1
fi

docker stop --time 30 "$container_name" >/dev/null
if docker inspect "$container_name" >/dev/null 2>&1; then
  docker rm "$container_name" >/dev/null 2>&1 || true
fi
if docker inspect "$container_name" >/dev/null 2>&1; then
  echo "PostgreSQL-container bleef onverwacht bestaan" >&2
  exit 1
fi
echo "DBOS PostgreSQL-container verwijderd; state blijft bewaard: $1" >&2
