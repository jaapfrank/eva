#!/usr/bin/env bash
set -Eeuo pipefail

candidate_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
scratch_root=$(mktemp -d /tmp/eva2-dbos-candidate.XXXXXX)
happy_state="$scratch_root/state-happy"
crash_state="$scratch_root/state-crash"
reject_state="$scratch_root/state-reject"
mira_state="$scratch_root/state-mira"
fixture_pid=""

cleanup() {
  if [[ -n "$fixture_pid" ]]; then
    kill "$fixture_pid" 2>/dev/null || true
    wait "$fixture_pid" 2>/dev/null || true
  fi
  "$candidate_root/dbos/cleanup_state.sh" "$happy_state" 2>/dev/null || true
  "$candidate_root/dbos/cleanup_state.sh" "$crash_state" 2>/dev/null || true
  "$candidate_root/dbos/cleanup_state.sh" "$reject_state" 2>/dev/null || true
  "$candidate_root/dbos/cleanup_state.sh" "$mira_state" 2>/dev/null || true
  if [[ "$scratch_root" == /tmp/eva2-dbos-candidate.* ]]; then
    rm -rf "$scratch_root"
  fi
}
trap cleanup EXIT INT TERM

python3 -m venv "$scratch_root/venv"
python_bin="$scratch_root/venv/bin/python"
"$python_bin" -m pip install --quiet --disable-pip-version-check \
  -r "$candidate_root/requirements-dbos.txt"
installed_dbos_version=$(
  "$python_bin" -c 'from importlib.metadata import version; print(version("dbos"))'
)
if [[ "$installed_dbos_version" != "2.30.0" ]]; then
  echo "verwachtte DBOS 2.30.0, kreeg $installed_dbos_version" >&2
  exit 1
fi
free_port() {
  "$python_bin" -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

start_fixture() {
  local scenario=$1
  fixture_port=$(free_port)
  "$python_bin" "$candidate_root/fixture_server.py" \
    --db "$scratch_root/fixture-${scenario}.sqlite" \
    --port "$fixture_port" \
    >"$scratch_root/fixture-${scenario}.log" 2>&1 &
  fixture_pid=$!
  for _ in $(seq 1 60); do
    if "$python_bin" - "$fixture_port" <<'PY'
import sys
import urllib.request

try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=0.2).read()
except Exception:
    raise SystemExit(1)
PY
    then
      return
    fi
    sleep 0.1
  done
  echo "fixture startte niet voor scenario $scenario" >&2
  exit 1
}

stop_fixture() {
  kill "$fixture_pid"
  wait "$fixture_pid" 2>/dev/null || true
  fixture_pid=""
}

validate_outcome() {
  local outcome_path=$1
  local fixture_url=$2
  local expected_reconciled=$3
  local expected_effect_attempts=$4
  local contract_path=$5
  PYTHONPATH="$candidate_root" "$python_bin" - \
    "$outcome_path" "$fixture_url" "$expected_reconciled" \
    "$expected_effect_attempts" "$contract_path" <<'PY'
import sys

from common import EpisodeOutcome, FixtureClient, load_contract
from oracle import validate_episode

outcome = EpisodeOutcome.model_validate_json(open(sys.argv[1], encoding="utf-8").read())
assert len(outcome.receipts) == 3
assert [r.operation for r in outcome.receipts] == [
    "calendar.create", "document.create", "delivery.send",
]
expected_reconciled = sys.argv[3] == "true"
assert outcome.receipts[0].reconciled is expected_reconciled
evidence = FixtureClient(sys.argv[2]).evidence()
assert len(evidence["effects"]) == 3
assert len(evidence["model_calls"]) == 1
posts = [
    call for call in evidence["calls"]
    if call["method"] == "POST" and call["path"] == "/v1/effects"
]
assert len(posts) == int(sys.argv[4])
assert len({call["idempotency_key"] for call in posts}) == 3
validate_episode(load_contract(sys.argv[5]), outcome, evidence)
PY
}

validate_dbos_storage() {
  local state_dir=$1
  local expected_steps=$2
  local expected_errors=$3
  local episode_id=$4
  local container_name
  local row
  local step_counts
  container_name=$(python3 - "$state_dir/dbos-postgres.json" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["container_name"])
PY
  )
  row=$(docker exec "$container_name" psql -U eva2 -d eva2 -At \
    -F '|' -c \
    "SELECT status,serialization FROM eva2_dbos_candidate.workflow_status WHERE workflow_uuid='eva2-dbos:${episode_id}'")
  step_counts=$(docker exec "$container_name" psql -U eva2 -d eva2 -At \
    -F '|' -c \
    "SELECT count(*),count(*) FILTER (WHERE error IS NOT NULL) FROM eva2_dbos_candidate.operation_outputs WHERE workflow_uuid='eva2-dbos:${episode_id}'")
  if [[ "$row" != "SUCCESS|portable_json" \
    || "$step_counts" != "$expected_steps|$expected_errors" ]]; then
    echo "ongeldige DBOS PostgreSQL-state voor $state_dir: $row, $step_counts" >&2
    exit 1
  fi
}

validate_pending_storage() {
  local state_dir=$1
  local container_name
  local row
  local step_count
  container_name=$(python3 - "$state_dir/dbos-postgres.json" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["container_name"])
PY
  )
  row=$(docker exec "$container_name" psql -U eva2 -d eva2 -At \
    -F '|' -c \
    "SELECT status,serialization FROM eva2_dbos_candidate.workflow_status WHERE workflow_uuid='eva2-dbos:architecture:noor:001'")
  step_count=$(docker exec "$container_name" psql -U eva2 -d eva2 -At \
    -c \
    "SELECT count(*) FROM eva2_dbos_candidate.operation_outputs WHERE workflow_uuid='eva2-dbos:architecture:noor:001'")
  if [[ "$row" != "PENDING|portable_json" || "$step_count" != "4" ]]; then
    echo "crash liet geen hervatbare PostgreSQL-state achter: $row, $step_count" >&2
    exit 1
  fi
}

validate_pre_restore() {
  local fixture_url=$1
  PYTHONPATH="$candidate_root" "$python_bin" - "$fixture_url" <<'PY'
import sys
from datetime import datetime

from common import FixtureClient

evidence = FixtureClient(sys.argv[1]).evidence()
assert len(evidence["model_calls"]) == 1
assert [effect["operation"] for effect in evidence["effects"]] == ["calendar.create"]
attempts = [
    call for call in evidence["calls"]
    if call["method"] == "POST" and call["path"] == "/v1/effects"
]
assert [call["status"] for call in attempts] == [429, 201]
limited = datetime.fromisoformat(attempts[0]["happened_at"].replace("Z", "+00:00"))
accepted = datetime.fromisoformat(attempts[1]["happened_at"].replace("Z", "+00:00"))
assert (accepted - limited).total_seconds() >= 0.25
PY
}

validate_failure() {
  local outcome_path=$1
  local fixture_url=$2
  local expected_operation=$3
  PYTHONPATH="$candidate_root" "$python_bin" - \
    "$outcome_path" "$fixture_url" "$expected_operation" "$contract" <<'PY'
import sys

from common import EpisodeFailureOutcome, FixtureClient, load_contract
from oracle import validate_definitive_failure

outcome = EpisodeFailureOutcome.model_validate_json(
    open(sys.argv[1], encoding="utf-8").read()
)
evidence = FixtureClient(sys.argv[2]).evidence()
validate_definitive_failure(
    load_contract(sys.argv[4]), outcome, evidence, sys.argv[3],
)
PY
}

candidate_call_count() {
  local fixture_url=$1
  PYTHONPATH="$candidate_root" "$python_bin" - "$fixture_url" <<'PY'
import sys

from common import FixtureClient

evidence = FixtureClient(sys.argv[1]).evidence()
control = {"/v1/evidence", "/v1/faults", "/v1/model-calls"}
print(sum(call["path"].split("?", 1)[0] not in control for call in evidence["calls"]))
PY
}

contract="$candidate_root/contract/episode-noor-v1.json"
mira_contract="$candidate_root/contract/episode-noor-mira-v1.json"

echo "happy path" >&2
"$candidate_root/dbos/prepare_state.sh" "$happy_state"
start_fixture happy
happy_fixture="http://127.0.0.1:${fixture_port}"
"$python_bin" "$candidate_root/dbos_candidate.py" \
  --state-dir "$happy_state" \
  --fixture "$happy_fixture" \
  --contract "$contract" \
  >"$scratch_root/happy.out"
validate_outcome "$scratch_root/happy.out" "$happy_fixture" false 3 "$contract"
validate_dbos_storage "$happy_state" 7 0 architecture:noor:001
cat "$scratch_root/happy.out"
stop_fixture
"$candidate_root/dbos/cleanup_state.sh" "$happy_state"

echo "hard crash after remote accept, then restart" >&2
"$candidate_root/dbos/prepare_state.sh" "$crash_state"
start_fixture crash
crash_fixture="http://127.0.0.1:${fixture_port}"
PYTHONPATH="$candidate_root" "$python_bin" - "$crash_fixture" <<'PY'
import sys

from common import FixtureClient

FixtureClient(sys.argv[1]).set_fault("calendar.create", "rate_limit")
PY
set +e
EVA_BAKEOFF_CRASH_AT=calendar.create:after_remote_accept \
  "$python_bin" "$candidate_root/dbos_candidate.py" \
    --state-dir "$crash_state" \
    --fixture "$crash_fixture" \
    --contract "$contract" \
    >"$scratch_root/crash-first.out"
crash_status=$?
set -e
if [[ "$crash_status" -ne 86 ]]; then
  echo "verwachtte crash-exit 86, kreeg $crash_status" >&2
  exit 1
fi
validate_pre_restore "$crash_fixture"
validate_pending_storage "$crash_state"
crash_container=$(python3 - "$crash_state/dbos-postgres.json" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["container_name"])
PY
)
if ! docker exec "$crash_container" test -s /var/lib/postgresql/data/PG_VERSION; then
  echo "PostgreSQL-datadir mist vóór containerrestore" >&2
  exit 1
fi
old_container_id=$(docker inspect --format '{{.Id}}' "$crash_container")
"$candidate_root/dbos/remove_container.sh" "$crash_state"
if docker inspect "$crash_container" >/dev/null 2>&1; then
  echo "oude PostgreSQL-container bestaat nog" >&2
  exit 1
fi
"$candidate_root/dbos/prepare_state.sh" "$crash_state"
new_container_id=$(docker inspect --format '{{.Id}}' "$crash_container")
if [[ "$old_container_id" == "$new_container_id" ]]; then
  echo "PostgreSQL-container is niet werkelijk gereconstrueerd" >&2
  exit 1
fi
"$python_bin" "$candidate_root/dbos_candidate.py" \
  --state-dir "$crash_state" \
  --fixture "$crash_fixture" \
  --contract "$contract" \
  >"$scratch_root/restarted.out"
validate_outcome "$scratch_root/restarted.out" "$crash_fixture" true 4 "$contract"
validate_dbos_storage "$crash_state" 7 0 architecture:noor:001
cat "$scratch_root/restarted.out"
stop_fixture
"$candidate_root/dbos/cleanup_state.sh" "$crash_state"

echo "definitieve reject wordt terminale deeluitkomst" >&2
"$candidate_root/dbos/prepare_state.sh" "$reject_state"
start_fixture reject
reject_fixture="http://127.0.0.1:${fixture_port}"
PYTHONPATH="$candidate_root" "$python_bin" - "$reject_fixture" <<'PY'
import sys

from common import FixtureClient

FixtureClient(sys.argv[1]).set_fault("document.create", "reject")
PY
"$python_bin" "$candidate_root/dbos_candidate.py" \
  --state-dir "$reject_state" \
  --fixture "$reject_fixture" \
  --contract "$contract" \
  >"$scratch_root/reject.out"
validate_failure "$scratch_root/reject.out" "$reject_fixture" document.create
validate_dbos_storage "$reject_state" 6 0 architecture:noor:001
calls_before_replay=$(candidate_call_count "$reject_fixture")
"$python_bin" "$candidate_root/dbos_candidate.py" \
  --state-dir "$reject_state" \
  --fixture "$reject_fixture" \
  --contract "$contract" \
  >"$scratch_root/reject-replayed.out"
calls_after_replay=$(candidate_call_count "$reject_fixture")
if ! cmp --silent "$scratch_root/reject.out" "$scratch_root/reject-replayed.out"; then
  echo "terminale failure-outcome wijzigde bij replay" >&2
  exit 1
fi
if [[ "$calls_before_replay" != "$calls_after_replay" ]]; then
  echo "terminale failure-replay deed opnieuw een connectorcall" >&2
  exit 1
fi
validate_failure "$scratch_root/reject-replayed.out" "$reject_fixture" document.create
cat "$scratch_root/reject.out"
stop_fixture
"$candidate_root/dbos/cleanup_state.sh" "$reject_state"

echo "tweede identity-pack op volledig schone state" >&2
"$candidate_root/dbos/prepare_state.sh" "$mira_state"
start_fixture mira
mira_fixture="http://127.0.0.1:${fixture_port}"
"$python_bin" "$candidate_root/dbos_candidate.py" \
  --state-dir "$mira_state" \
  --fixture "$mira_fixture" \
  --contract "$mira_contract" \
  >"$scratch_root/mira.out"
validate_outcome "$scratch_root/mira.out" "$mira_fixture" false 3 "$mira_contract"
validate_dbos_storage "$mira_state" 7 0 architecture:noor:mira:001
cat "$scratch_root/mira.out"
stop_fixture
"$candidate_root/dbos/cleanup_state.sh" "$mira_state"

echo "alle DBOS-scenario's geslaagd; persistente state stond onder $scratch_root" >&2
