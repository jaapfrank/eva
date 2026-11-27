#!/usr/bin/env bash
# Bouwt `eva-unifi-connect-display.apk` via een schone Docker container (geen Android Studio nodig op Windows/Linux).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_APK="$SCRIPT_DIR/eva-unifi-connect-display.apk"

echo "[1/2] Eva UniFi Connect Display APK bouwen in Docker (Android API 28/33, arm64-v8a, 0 GMS)..."
docker run --rm \
  -v "$SCRIPT_DIR:/project" \
  -w /project \
  ghcr.io/cirruslabs/android-sdk:33 \
  bash -lc 'gradle --no-daemon assembleRelease && cp app/build/outputs/apk/release/app-release.apk /project/eva-unifi-connect-display.apk'

echo "[2/2] Klaar! Upload dit bestand in UniFi Connect -> Keuken Display -> Display Mode: Android App:"
echo "      $OUT_APK"
