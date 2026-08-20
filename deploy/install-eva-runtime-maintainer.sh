#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 1 ]; then
  echo "Gebruik als root: $0 /pad/buiten-de-repo/eva-runtime-self-repair.json" >&2
  exit 2
fi

INPUT_CONFIG=$1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG_TEMP=$(mktemp /tmp/eva-config.XXXXXX)
trap 'rm -f "$CONFIG_TEMP"' EXIT HUP INT TERM
/usr/bin/node -e 'const fs=require("fs"),[source,target]=process.argv.slice(1),c=fs.constants;const fd=fs.openSync(source,c.O_RDONLY|c.O_NOFOLLOW);try{const s=fs.fstatSync(fd);if(!s.isFile()||s.uid!==0||(s.mode&0o022)||s.size>1048576)process.exit(5);fs.writeFileSync(target,fs.readFileSync(fd),{mode:0o600})}finally{fs.closeSync(fd)}' "$INPUT_CONFIG" "$CONFIG_TEMP"
SOURCE_CONFIG=$CONFIG_TEMP
if systemctl is-active --quiet eva.service; then
  echo "Stop eva.service eerst bewust; deze installatie wijzigt de productiongrens." >&2
  exit 3
fi

JSON_FIELD='const v=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));process.stdout.write(String(v[process.argv[2]]||""))'
SERVICE_USER=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" service_user)
TEST_GATE_USER=eva-test-gate
PROJECT_ROOT=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" root)
RUNTIME_GROUP=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" runtime_read_group)
MAINTAINER_STATE=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" maintainer_state_dir)
TRACKED_MANIFEST=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" tracked_manifest)
RELEASE_MANIFEST=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" release_manifest)
PROTOCOL=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" protocol)
test "$SERVICE_USER" = eva
test "$RUNTIME_GROUP" = eva-runtime-read
test "$MAINTAINER_STATE" = /var/lib/eva-runtime-maintainer
test "$TRACKED_MANIFEST" = "$MAINTAINER_STATE/tracked-manifest.json"
test "$RELEASE_MANIFEST" = /etc/eva-runtime-self-repair.release.json
test "$PROTOCOL" = EvaRuntimeRepair/v3
case "$PROJECT_ROOT" in /*) ;; *) echo "De projectroot moet absoluut zijn." >&2; exit 4;; esac
printf '%s' "$PROJECT_ROOT" | grep -Eq '^/[A-Za-z0-9._/-]+$' || exit 4
test -d "$PROJECT_ROOT/src" && test -d "$PROJECT_ROOT/.git"
test "$(realpath -e -- "$PROJECT_ROOT")" = "$PROJECT_ROOT"

getent group "$SERVICE_USER" >/dev/null 2>&1 || groupadd --system "$SERVICE_USER"
getent group "$TEST_GATE_USER" >/dev/null 2>&1 || groupadd --system "$TEST_GATE_USER"
getent group "$RUNTIME_GROUP" >/dev/null 2>&1 || groupadd --system "$RUNTIME_GROUP"
id "$SERVICE_USER" >/dev/null 2>&1 \
  || useradd --system --gid "$SERVICE_USER" --home-dir /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
id "$TEST_GATE_USER" >/dev/null 2>&1 \
  || useradd --system --gid "$TEST_GATE_USER" --home-dir /nonexistent --shell /usr/sbin/nologin "$TEST_GATE_USER"
usermod -g "$SERVICE_USER" -G "$RUNTIME_GROUP" -d /nonexistent -s /usr/sbin/nologin "$SERVICE_USER"
usermod -g "$TEST_GATE_USER" -G "$RUNTIME_GROUP" -d /nonexistent -s /usr/sbin/nologin "$TEST_GATE_USER"
SERVICE_UID=$(id -u "$SERVICE_USER")
SERVICE_GID=$(id -g "$SERVICE_USER")
TEST_GATE_UID=$(id -u "$TEST_GATE_USER")
TEST_GATE_GID=$(id -g "$TEST_GATE_USER")
RUNTIME_GID=$(getent group "$RUNTIME_GROUP" | cut -d: -f3)
test "$SERVICE_UID" -gt 0 && test "$TEST_GATE_UID" -gt 0 && test "$SERVICE_UID" != "$TEST_GATE_UID"
test "$SERVICE_GID" -gt 0 && test "$TEST_GATE_GID" -gt 0 && test "$RUNTIME_GID" -gt 0
test "$SERVICE_GID" != "$TEST_GATE_GID" && test "$SERVICE_GID" != "$RUNTIME_GID" \
  && test "$TEST_GATE_GID" != "$RUNTIME_GID"
test "$(id -gn "$SERVICE_USER")" = "$SERVICE_USER"
test "$(id -gn "$TEST_GATE_USER")" = "$TEST_GATE_USER"
getent passwd "$SERVICE_USER" | grep -Eq "^${SERVICE_USER}:x:${SERVICE_UID}:${SERVICE_GID}:[^:]*:/nonexistent:/usr/sbin/nologin$"
getent passwd "$TEST_GATE_USER" | grep -Eq "^${TEST_GATE_USER}:x:${TEST_GATE_UID}:${TEST_GATE_GID}:[^:]*:/nonexistent:/usr/sbin/nologin$"
exact_identity_groups() {
  IDENTITY_USER=$1
  PRIMARY_GID=$2
  PRIMARY_GROUP=$3
  ACTUAL_GIDS=$(id -G "$IDENTITY_USER" | tr ' ' '\n' | sed '/^$/d' | sort -n | paste -sd ' ' -)
  EXPECTED_GIDS=$(printf '%s\n%s\n' "$PRIMARY_GID" "$RUNTIME_GID" | sort -n | paste -sd ' ' -)
  ACTUAL_GROUPS=$(id -Gn "$IDENTITY_USER" | tr ' ' '\n' | sed '/^$/d' | sort | paste -sd ' ' -)
  EXPECTED_GROUPS=$(printf '%s\n%s\n' "$PRIMARY_GROUP" "$RUNTIME_GROUP" | sort | paste -sd ' ' -)
  test "$ACTUAL_GIDS" = "$EXPECTED_GIDS" && test "$ACTUAL_GROUPS" = "$EXPECTED_GROUPS"
}
exact_identity_groups "$SERVICE_USER" "$SERVICE_GID" "$SERVICE_USER"
exact_identity_groups "$TEST_GATE_USER" "$TEST_GATE_GID" "$TEST_GATE_USER"
loginctl enable-linger "$TEST_GATE_USER"
systemctl restart "user@${TEST_GATE_UID}.service"
systemctl is-active --quiet "user@${TEST_GATE_UID}.service"
test -S "/run/user/${TEST_GATE_UID}/bus"
command -v setfacl >/dev/null 2>&1 || { echo "setfacl is vereist voor private parenttraversal." >&2; exit 5; }

# Alleen Eva krijgt traversal naar de private projectroot; er worden nergens
# world-read/world-executebits toegevoegd. De repository zelf is root-owned,
# groep-leesbaar en voor alle anderen volledig gesloten. Gitmetadata blijft root-only.
PARENT_PATH=$(dirname "$PROJECT_ROOT")
while [ "$PARENT_PATH" != / ]; do
  setfacl -m "u:$SERVICE_USER:--x" "$PARENT_PATH"
  PARENT_PATH=$(dirname "$PARENT_PATH")
done
setfacl -bR "$PROJECT_ROOT"
chown -R "root:$RUNTIME_GROUP" "$PROJECT_ROOT"
chmod -R u=rX,g=rX,o= "$PROJECT_ROOT"
chown -R root:root "$PROJECT_ROOT/.git"
chmod -R u=rwX,go= "$PROJECT_ROOT/.git"
chown -R eva:eva "$PROJECT_ROOT/data"
chmod -R u=rwX,go= "$PROJECT_ROOT/data"
if [ -f "$PROJECT_ROOT/.env" ]; then chown root:eva "$PROJECT_ROOT/.env"; chmod 0640 "$PROJECT_ROOT/.env"; fi

HEALTH_TOKEN_FILE=$(/usr/bin/node -e "$JSON_FIELD" "$SOURCE_CONFIG" health_token_file)
WEB_TOKEN=$(/usr/bin/node -e 'const fs=require("fs"),f=process.argv[1];if(!fs.existsSync(f))process.exit(0);const d=fs.readFileSync(f,"utf8").split(/\r?\n/).filter(l=>/^\s*(?:export\s+)?EVA_WEB_TOKEN\s*=/.test(l));if(d.length>1||(d[0]&&!/^EVA_WEB_TOKEN=(?:[A-Za-z0-9._~-]{32,256})?$/.test(d[0])))process.exit(5);process.stdout.write(d[0]?.slice(14)||"");' "$PROJECT_ROOT/.env")
if [ -z "$WEB_TOKEN" ]; then echo "EVA_WEB_TOKEN is verplicht voor root-livebewijs." >&2; exit 5; fi
printf '%s' "$HEALTH_TOKEN_FILE" | grep -Eq '^/etc/[A-Za-z0-9._/-]+$' || exit 5
install -o root -g root -m 0600 /dev/null "$HEALTH_TOKEN_FILE"
printf '%s' "$WEB_TOKEN" >"$HEALTH_TOKEN_FILE"

install -d -o root -g root -m 0711 "$MAINTAINER_STATE"
TRACKED_TEMP=$(mktemp /tmp/eva-tracked.XXXXXX)
SERVICE_TEMP=$(mktemp /tmp/eva-service.XXXXXX)
RELEASE_TEMP=$(mktemp /tmp/eva-release.XXXXXX)
BOOTSTRAP_TEMP=$(mktemp /tmp/eva-bootstrap.XXXXXX)
trap 'rm -f "$CONFIG_TEMP" "$TRACKED_TEMP" "$SERVICE_TEMP" "$RELEASE_TEMP" "$BOOTSTRAP_TEMP"' EXIT HUP INT TERM
/usr/bin/node -e 'const fs=require("fs"),cp=require("child_process");const [root,out,protocol]=process.argv.slice(1);const files=cp.execFileSync("/usr/bin/git",["-C",root,"ls-files","-z"],{encoding:"utf8"}).split("\0").filter(Boolean).sort();fs.writeFileSync(out,JSON.stringify({protocol,root,files,issued_at:new Date().toISOString()},null,2)+"\n");' "$PROJECT_ROOT" "$TRACKED_TEMP" "$PROTOCOL"
install -o root -g eva -m 0640 "$TRACKED_TEMP" "$TRACKED_MANIFEST"

install -o root -g "$RUNTIME_GROUP" -m 0550 "$SCRIPT_DIR/eva-runtime-maintainer.mjs" /usr/local/libexec/eva-runtime-maintainer.mjs
install -o root -g "$RUNTIME_GROUP" -m 0440 "$SCRIPT_DIR/eva-runtime-repair-protocol.mjs" /usr/local/libexec/eva-runtime-repair-protocol.mjs
install -d -o root -g root -m 0755 /usr/local/libexec/eva
RUNNER_RELEASE_VERIFY='import fs from "node:fs";import{pathToFileURL}from"node:url";const[marker,moduleFile,release,bootstrap,bootstrapSource,runner]=process.argv.slice(1);if(marker!=="eva-runner-release")throw new Error("ongeldige runnerreleasemarkering");const{validateIsolatedTestRelease}=await import(pathToFileURL(moduleFile));validateIsolatedTestRelease({release:fs.readFileSync(release),bootstrap:fs.readFileSync(bootstrap),bootstrapSource:fs.readFileSync(bootstrapSource),runner:fs.readFileSync(runner)});'
/usr/bin/node --input-type=module -e "$RUNNER_RELEASE_VERIFY" eva-runner-release \
  /usr/local/libexec/eva-runtime-maintainer.mjs \
  "$SCRIPT_DIR/../scripts/run-isolated-tests-release.json" \
  "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap" \
  "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap.c" \
  "$SCRIPT_DIR/../scripts/run-isolated-tests.mjs"
for RUNNER_SOURCE in run-isolated-tests-release.json run-isolated-tests-bootstrap \
  run-isolated-tests-bootstrap.c run-isolated-tests.mjs run-isolated-tests.test.mjs; do
  git -C "$PROJECT_ROOT" ls-files --error-unmatch "scripts/$RUNNER_SOURCE" >/dev/null
done
test -x /usr/bin/x86_64-linux-gnu-gcc-13
test "$(realpath -e /usr/bin/x86_64-linux-gnu-gcc-13)" = /usr/bin/x86_64-linux-gnu-gcc-13
test "$(stat -Lc '%u:%g:%a:%F:%h' /usr/bin/x86_64-linux-gnu-gcc-13)" = '0:0:755:regular file:1'
/usr/bin/x86_64-linux-gnu-gcc-13 -static -std=c11 -O2 -Wall -Wextra -Werror \
  -fno-ident -fno-record-gcc-switches -Wl,--build-id=none -s \
  -o "$BOOTSTRAP_TEMP" "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap.c"
/usr/bin/node --input-type=module -e "$RUNNER_RELEASE_VERIFY" eva-runner-release \
  /usr/local/libexec/eva-runtime-maintainer.mjs \
  "$SCRIPT_DIR/../scripts/run-isolated-tests-release.json" "$BOOTSTRAP_TEMP" \
  "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap.c" \
  "$SCRIPT_DIR/../scripts/run-isolated-tests.mjs"
cmp -s "$BOOTSTRAP_TEMP" "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap"
install -o root -g root -m 0755 "$BOOTSTRAP_TEMP" /usr/local/libexec/eva/run-isolated-tests-bootstrap
install -o root -g root -m 0444 "$SCRIPT_DIR/../scripts/run-isolated-tests-bootstrap.c" /usr/local/libexec/eva/run-isolated-tests-bootstrap.c
install -o root -g root -m 0444 "$SCRIPT_DIR/../scripts/run-isolated-tests.mjs" /usr/local/libexec/eva/run-isolated-tests.mjs
install -o root -g root -m 0444 "$SCRIPT_DIR/../scripts/run-isolated-tests-release.json" /usr/local/libexec/eva/run-isolated-tests-release.json
test "$(realpath -e /usr/local/libexec/eva/run-isolated-tests-bootstrap)" = /usr/local/libexec/eva/run-isolated-tests-bootstrap
test "$(stat -Lc '%u:%g:%a:%F:%h' /usr/local/libexec/eva/run-isolated-tests-bootstrap)" = '0:0:755:regular file:1'
for INSTALLED_RUNNER_SOURCE in /usr/local/libexec/eva/run-isolated-tests-bootstrap.c \
  /usr/local/libexec/eva/run-isolated-tests.mjs \
  /usr/local/libexec/eva/run-isolated-tests-release.json; do
  test "$(realpath -e "$INSTALLED_RUNNER_SOURCE")" = "$INSTALLED_RUNNER_SOURCE"
  test "$(stat -Lc '%u:%g:%a:%F:%h' "$INSTALLED_RUNNER_SOURCE")" = '0:0:444:regular file:1'
done
/usr/bin/node --input-type=module -e "$RUNNER_RELEASE_VERIFY" eva-runner-release \
  /usr/local/libexec/eva-runtime-maintainer.mjs \
  /usr/local/libexec/eva/run-isolated-tests-release.json \
  /usr/local/libexec/eva/run-isolated-tests-bootstrap \
  /usr/local/libexec/eva/run-isolated-tests-bootstrap.c \
  /usr/local/libexec/eva/run-isolated-tests.mjs
install -o root -g eva -m 0640 "$SOURCE_CONFIG" /etc/eva-runtime-self-repair.json
for UNIT in eva-runtime-maintainer.service eva-runtime-maintainer-recover.service eva-runtime-maintainer.path; do
  install -o root -g "$RUNTIME_GROUP" -m 0640 "$SCRIPT_DIR/$UNIT" "/etc/systemd/system/$UNIT"
done
EVA_SERVICE_RENDER='import fs from "node:fs";import{pathToFileURL}from"node:url";const[marker,moduleFile,source,target,serviceUser,runtimeReadGroup,projectRoot]=process.argv.slice(1);if(marker!=="eva-service-render")throw new Error("ongeldige rendermarkering");const{renderEvaServiceUnit}=await import(pathToFileURL(moduleFile));const rendered=renderEvaServiceUnit(fs.readFileSync(source,"utf8"),{serviceUser,runtimeReadGroup,projectRoot});fs.writeFileSync(target,rendered,{encoding:"utf8",mode:0o600});'
/usr/bin/node --input-type=module -e "$EVA_SERVICE_RENDER" \
  eva-service-render /usr/local/libexec/eva-runtime-maintainer.mjs "$SCRIPT_DIR/eva.service" "$SERVICE_TEMP" \
  "$SERVICE_USER" "$RUNTIME_GROUP" "$PROJECT_ROOT"
install -o root -g "$RUNTIME_GROUP" -m 0640 "$SERVICE_TEMP" /etc/systemd/system/eva.service
install -d -o root -g "$RUNTIME_GROUP" -m 0750 /etc/systemd/system/eva.service.d
install -o root -g "$RUNTIME_GROUP" -m 0640 "$SCRIPT_DIR/eva-runtime-maintainer.conf" /etc/systemd/system/eva.service.d/runtime-maintainer.conf

EVA_SERVICE_POSTCONDITION='import fs from "node:fs";import{pathToFileURL}from"node:url";const[marker,moduleFile,installed,serviceUser,runtimeReadGroup,projectRoot]=process.argv.slice(1);if(marker!=="eva-service-attest")throw new Error("ongeldige attestatiemarkering");const{renderEvaServiceUnit}=await import(pathToFileURL(moduleFile));const bytes=fs.readFileSync(installed,"utf8");if(renderEvaServiceUnit(bytes,{serviceUser,runtimeReadGroup,projectRoot})!==bytes)throw new Error("geïnstalleerde eva.service wijkt af van de zuivere render");'
/usr/bin/node --input-type=module -e "$EVA_SERVICE_POSTCONDITION" \
  eva-service-attest /usr/local/libexec/eva-runtime-maintainer.mjs /etc/systemd/system/eva.service \
  "$SERVICE_USER" "$RUNTIME_GROUP" "$PROJECT_ROOT"

/usr/bin/node -e 'const fs=require("fs"),crypto=require("crypto");const [out,protocol,...paths]=process.argv.slice(1);const files=Object.fromEntries(paths.sort().map(p=>[p,crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex")]));fs.writeFileSync(out,JSON.stringify({protocol,issued_at:new Date().toISOString(),files},null,2)+"\n");' "$RELEASE_TEMP" "$PROTOCOL" \
  /usr/local/libexec/eva-runtime-maintainer.mjs /usr/local/libexec/eva-runtime-repair-protocol.mjs \
  /usr/local/libexec/eva/run-isolated-tests-bootstrap \
  /usr/local/libexec/eva/run-isolated-tests-bootstrap.c \
  /usr/local/libexec/eva/run-isolated-tests.mjs \
  /usr/local/libexec/eva/run-isolated-tests-release.json \
  /etc/systemd/system/eva.service /etc/systemd/system/eva-runtime-maintainer.path \
  /etc/systemd/system/eva-runtime-maintainer.service /etc/systemd/system/eva-runtime-maintainer-recover.service \
  /etc/systemd/system/eva.service.d/runtime-maintainer.conf "$TRACKED_MANIFEST"
install -o root -g eva -m 0640 "$RELEASE_TEMP" "$RELEASE_MANIFEST"

/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs --prepare /etc/eva-runtime-self-repair.json
for CANDIDATE_READER in "$SERVICE_USER" "$TEST_GATE_USER"; do
  /usr/sbin/runuser -u "$CANDIDATE_READER" -- /usr/bin/test -x "$MAINTAINER_STATE/candidates"
  /usr/sbin/runuser -u "$CANDIDATE_READER" -- /usr/bin/test ! -w "$MAINTAINER_STATE/candidates"
done
systemctl daemon-reload
systemctl enable --now eva-runtime-maintainer.path
/usr/bin/node /usr/local/libexec/eva-runtime-maintainer.mjs --check /etc/eva-runtime-self-repair.json
