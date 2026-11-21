#!/bin/sh
set -eu
board=$(dirname "$0")
# Always generate the pristine inner image first; it has empty DATA and cannot
# accidentally contain an installer image from a previous incremental build.
support/scripts/genimage.sh -c "$board/genimage.cfg"
# Fakeroot ownership is inherited by genimage/mke2fs; no real host chown/mount.
"$HOST_DIR/bin/fakeroot" -- python3 "$board/pack-install-media.py" \
    --images "$BINARIES_DIR" --build-dir "$BUILD_DIR" \
    --template "$board/genimage-usb.cfg" \
    --genimage "$HOST_DIR/bin/genimage" --debugfs "$HOST_DIR/sbin/debugfs"
