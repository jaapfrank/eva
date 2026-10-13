#!/bin/sh
set -eu
umask 077
mkdir -p "$HOME/.claude-server-commander"
if [ ! -f "$HOME/.claude-server-commander/config.json" ]; then
    cp /opt/eva-programming/default-config.json "$HOME/.claude-server-commander/config.json"
fi
exec node /opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js
