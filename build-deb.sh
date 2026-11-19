#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)
#
# Builds the .deb package of Easy DLNA Server for the architecture of THIS
# machine (amd64 or arm64). Run it on Ubuntu or Debian, from anywhere:
#
#   sudo apt install python3 python3-venv
#   bash packaging/linux/build-deb.sh
#
# The result is dist/easydlna_<version>_<architecture>.deb
#
# The program is frozen with PyInstaller, so it needs the glibc of the machine
# where it was built: build on the OLDEST Ubuntu you want to support (for
# example 22.04) and it will also run on newer ones.
#
# Optional environment variables:
#   VERSION      package version (default: app_version.py)
#   MAINTAINER   "Name <email>" for the control file
#   BINARY_DIR   use an already built PyInstaller folder instead of building
#                (for testing the packaging)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-$(python3 -c 'import app_version; print(app_version.__version__)')}"
MAINTAINER="${MAINTAINER:-devaser <devaser@users.noreply.github.com>}"
ARCH="$(dpkg --print-architecture)"
GLIBC="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"

BUILD="$ROOT/build/deb"
PACKAGE="$BUILD/easydlna_${VERSION}_${ARCH}"
OUTPUT="$ROOT/dist/easydlna_${VERSION}_${ARCH}.deb"

echo "==> Easy DLNA Server $VERSION ($ARCH, glibc $GLIBC)"

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
install -d "$PACKAGE/DEBIAN" \
           "$PACKAGE/usr/lib/easydlna" \
           "$PACKAGE/usr/bin" \
           "$PACKAGE/usr/share/doc/easydlna"

cp -a "$BINARY_DIR/." "$PACKAGE/usr/lib/easydlna/"
ln -s ../lib/easydlna/easydlna "$PACKAGE/usr/bin/easydlna"

install -m 0644 "$HERE/debian/copyright" "$PACKAGE/usr/share/doc/easydlna/copyright"
[ -f README.md ] && install -m 0644 README.md "$PACKAGE/usr/share/doc/easydlna/README.md"
[ -f THIRD_PARTY_NOTICES.md ] && install -m 0644 THIRD_PARTY_NOTICES.md "$PACKAGE/usr/share/doc/easydlna/THIRD_PARTY_NOTICES.md"

# Nobody but root may write to the installed files.
find "$PACKAGE" -type d -exec chmod 0755 {} +
find "$PACKAGE/usr" -type f -exec chmod go-w {} +

# 3. Maintainer scripts and control file.
for script in postinst prerm postrm; do
    install -m 0755 "$HERE/debian/$script" "$PACKAGE/DEBIAN/$script"
done

INSTALLED_SIZE="$(du -sk "$PACKAGE/usr" | cut -f1)"

cat > "$PACKAGE/DEBIAN/control" <<CONTROL
Package: easydlna
Version: $VERSION
Section: video
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: libc6 (>= $GLIBC)
Recommends: systemd
Suggests: ufw
Homepage: https://github.com/devaser/easy-dlna-server
Description: Lightweight DLNA/UPnP media server
 Easy DLNA Server shares your video folders with TVs and media players on
 your local network. It supports external SRT subtitles and playback
 bookmarks, keeps the computer awake while it streams, and can run as a
 user service or as a system service.
 .
 Run "easydlna setup" after installing to choose how to run it.
CONTROL

# 4. Build.
echo "==> Building the .deb"
dpkg-deb --root-owner-group --build "$PACKAGE" "$OUTPUT"

echo
echo "Done: $OUTPUT"
echo "Install it with: sudo apt install $OUTPUT"
