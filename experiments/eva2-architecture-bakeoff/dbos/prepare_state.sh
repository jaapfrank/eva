#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "gebruik: $0 STATE_DIR" >&2
  exit 2
fi
if [[ "$1" != /tmp/* ]]; then
  echo "DBOS bakeoff-state moet onder /tmp staan: $1" >&2
  exit 2
fi

candidate_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_tool="$candidate_root/dbos_state.py"
mkdir -p "$1"
state_dir=$(cd "$1" && pwd -P)
if [[ "$state_dir" != /tmp/* ]]; then
  echo "DBOS bakeoff-state resolveert buiten /tmp: $state_dir" >&2
  exit 2
fi

postgres_image=$(python3 "$state_tool" image)
if ! postgres_image_id=$(docker image inspect "$postgres_image" --format '{{.Id}}' 2>/dev/null); then
  docker pull --quiet "$postgres_image" >/dev/null
  postgres_image_id=$(docker image inspect "$postgres_image" --format '{{.Id}}')
fi

metadata_path="$state_dir/dbos-postgres.json"
if [[ ! -e "$metadata_path" ]]; then
  python3 "$state_tool" init "$state_dir" "$postgres_image_id" >/dev/null
fi
readarray -t state < <(
  python3 "$state_tool" values "$state_dir" "$postgres_image_id"
)
container_name=${state[0]}
state_id=${state[1]}
bound_image=${state[2]}
image_digest=${state[3]}
bound_image_id=${state[4]}
credentials_sha256=${state[5]}
binding_sha256=${state[6]}
postgres_data_dir=${state[7]}
password_file=${state[8]}
postgres_user=${state[9]}
postgres_database=${state[10]}

verify_container() {
  local actual_state_id
  local actual_digest
  local actual_credentials
  local actual_binding
  local actual_image_id
  local actual_data_dir
  local actual_password_file
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
    echo "bestaande container wijkt af van sealed state; niets gewijzigd" >&2
    exit 1
  fi
}

if docker inspect "$container_name" >/dev/null 2>&1; then
  verify_container
  if [[ $(docker inspect --format '{{.State.Running}}' "$container_name") == true ]]; then
    postgres_binding=$(docker port "$container_name" 5432/tcp)
    postgres_port=${postgres_binding##*:}
    python3 "$state_tool" update-port \
      "$state_dir" "$postgres_image_id" "$postgres_port" >/dev/null
    docker exec "$container_name" pg_isready --quiet \
      -U "$postgres_user" -d "$postgres_database"
    echo "DBOS PostgreSQL-state is al gereed: $state_dir" >&2
    exit 0
  fi
  docker rm "$container_name" >/dev/null
fi

container_started=false
cleanup_failed_prepare() {
  if [[ "$container_started" == true ]]; then
    docker rm --force "$container_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup_failed_prepare ERR INT TERM

docker run --detach --rm \
  --name "$container_name" \
  --label "eva2.dbos.state-id=$state_id" \
  --label "eva2.dbos.image-digest=$image_digest" \
  --label "eva2.dbos.credentials-sha256=$credentials_sha256" \
  --label "eva2.dbos.binding-sha256=$binding_sha256" \
  --mount "type=bind,src=$postgres_data_dir,dst=/var/lib/postgresql/data" \
  --mount "type=bind,src=$password_file,dst=/run/secrets/eva2-postgres-password,readonly" \
  --publish 127.0.0.1::5432 \
  --env "POSTGRES_USER=$postgres_user" \
  --env "POSTGRES_DB=$postgres_database" \
  --env POSTGRES_PASSWORD_FILE=/run/secrets/eva2-postgres-password \
  "$bound_image" >/dev/null
container_started=true
verify_container

postgres_ready=false
for _ in $(seq 1 240); do
  if docker exec "$container_name" pg_isready --quiet \
    -U "$postgres_user" -d "$postgres_database"; then
    postgres_ready=true
    break
  fi
  sleep 0.25
done
if [[ "$postgres_ready" != true ]]; then
  docker logs "$container_name" >&2 || true
  echo "PostgreSQL werd niet binnen 60 seconden gereed" >&2
  exit 1
fi
postgres_binding=$(docker port "$container_name" 5432/tcp)
postgres_port=${postgres_binding##*:}
python3 "$state_tool" update-port \
  "$state_dir" "$postgres_image_id" "$postgres_port" >/dev/null
trap - ERR INT TERM
echo "DBOS PostgreSQL-state gereed of hersteld: $state_dir" >&2
