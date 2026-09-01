#!/bin/sh
set -eu

umask 077

fail() {
    printf '%s\n' "filesystem MCP proof refused: $1" >&2
    exit 2
}

read_private_secret() {
    label=$1
    path=$2
    minimum_bytes=$3

    [ -n "$path" ] || fail "$label file is not configured"
    [ -f "$path" ] || fail "$label file is not a regular file"
    [ ! -L "$path" ] || fail "$label file may not be a symlink"
    [ "$(stat -c %u -- "$path")" = "$(id -u)" ] || fail "$label file has the wrong owner"
    [ "$(stat -c %a -- "$path")" = "400" ] || fail "$label file must have mode 0400"

    byte_count=$(wc -c < "$path")
    [ "$byte_count" -ge "$minimum_bytes" ] || fail "$label file is too short"
    [ "$byte_count" -le 512 ] || fail "$label file is too large"
    clean_byte_count=$(tr -d '\r\n' < "$path" | wc -c)
    [ "$clean_byte_count" -eq "$byte_count" ] || fail "$label file contains a line break"

    cat -- "$path"
}

JWT_SECRET_KEY=$(read_private_secret "JWT secret" "${EVA_CONTEXTFORGE_JWT_SECRET_FILE:-}" 32)
AUTH_ENCRYPTION_SECRET=$(read_private_secret "encryption secret" "${EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE:-}" 32)
PLATFORM_ADMIN_PASSWORD=$(read_private_secret "admin password" "${EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE:-}" 16)
DEFAULT_USER_PASSWORD=$PLATFORM_ADMIN_PASSWORD

export JWT_SECRET_KEY AUTH_ENCRYPTION_SECRET PLATFORM_ADMIN_PASSWORD DEFAULT_USER_PASSWORD
unset EVA_CONTEXTFORGE_JWT_SECRET_FILE
unset EVA_CONTEXTFORGE_ENCRYPTION_SECRET_FILE
unset EVA_CONTEXTFORGE_ADMIN_PASSWORD_FILE

exec python3 -m mcpgateway.translate \
    --stdio "node /opt/eva2/filesystem-mcp/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js /proof-state" \
    --host 0.0.0.0 \
    --port 9000 \
    --expose-streamable-http \
    --stateless \
    --jsonResponse \
    --logLevel warning
