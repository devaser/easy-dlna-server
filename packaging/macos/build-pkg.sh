#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)
#
# Builds the macOS installer (.pkg) of Easy DLNA Server for the processor of
# THIS Mac (Apple Silicon or Intel). Run it on a Mac:
#
#   bash packaging/macos/build-pkg.sh
#
# The result is dist/easydlna-<version>-<architecture>.pkg. It installs the
# program in /usr/local/lib/easydlna and the command in /usr/local/bin/easydlna.
# After installing, run "easydlna setup" to start it at login.
#
# Without an Apple Developer ID the package is not signed: macOS asks the user
# to confirm it (right-click the package, then Open). To sign it, set:
#   SIGN_IDENTITY="Developer ID Installer: Your Name (TEAMID)"
# To notarize it afterwards, use "xcrun notarytool submit" and "xcrun stapler".
#
# Optional environment variables:
#   VERSION      package version (default: app_version.py)
#   BINARY_DIR   use an already built PyInstaller folder instead of building

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "error: this script must be run on macOS" >&2
    exit 1
fi

VERSION="${VERSION:-$(python3 -c 'import app_version; print(app_version.__version__)')}"
ARCH="$(uname -m)"

BUILD="$ROOT/build/macos"
PAYLOAD="$BUILD/payload"
OUTPUT="$ROOT/dist/easydlna-${VERSION}-${ARCH}.pkg"

echo "==> Easy DLNA Server $VERSION ($ARCH)"

rm -rf "$BUILD"
mkdir -p "$BUILD" "$ROOT/dist"

# 1. Freeze the program (unless a built folder was given).
if [ -z "${BINARY_DIR:-}" ]; then
    echo "==> Building the program with PyInstaller"
    python3 -m venv "$BUILD/venv"
    "$BUILD/venv/bin/pip" install --quiet --upgrade pip
    "$BUILD/venv/bin/pip" install --quiet -r requirements-dev.txt
    "$BUILD/venv/bin/pyinstaller" --noconfirm --clean \
        --distpath "$BUILD/dist" --workpath "$BUILD/work" \
        packaging/easydlna.spec
    BINARY_DIR="$BUILD/dist/easydlna"
fi

if [ ! -x "$BINARY_DIR/easydlna" ]; then
    echo "error: $BINARY_DIR/easydlna not found or not executable" >&2
    exit 1
fi

# 2. Package layout.
echo "==> Assembling the package"
mkdir -p "$PAYLOAD/usr/local/lib/easydlna" "$PAYLOAD/usr/local/bin" "$PAYLOAD/usr/local/share/doc/easydlna"

cp -a "$BINARY_DIR/." "$PAYLOAD/usr/local/lib/easydlna/"
ln -s ../lib/easydlna/easydlna "$PAYLOAD/usr/local/bin/easydlna"

[ -f LICENSE ] && cp LICENSE "$PAYLOAD/usr/local/share/doc/easydlna/LICENSE"
[ -f README.md ] && cp README.md "$PAYLOAD/usr/local/share/doc/easydlna/README.md"
[ -f THIRD_PARTY_NOTICES.md ] && cp THIRD_PARTY_NOTICES.md "$PAYLOAD/usr/local/share/doc/easydlna/THIRD_PARTY_NOTICES.md"

# 3. Build the package.
echo "==> Building the .pkg"
UNSIGNED="$BUILD/easydlna-unsigned.pkg"

pkgbuild \
    --root "$PAYLOAD" \
    --identifier com.devaser.easydlna \
    --version "$VERSION" \
    --install-location / \
    "$UNSIGNED"

if [ -n "${SIGN_IDENTITY:-}" ]; then
    echo "==> Signing the package"
    productsign --sign "$SIGN_IDENTITY" "$UNSIGNED" "$OUTPUT"
else
    cp "$UNSIGNED" "$OUTPUT"
    echo "warning: the package is not signed (set SIGN_IDENTITY to sign it)"
fi

echo
echo "Done: $OUTPUT"
echo "Install it by opening it, then run: easydlna setup"
