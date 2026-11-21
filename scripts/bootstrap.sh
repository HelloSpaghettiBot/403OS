#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source dependencies.lock
downloads=${SURFACE_DOWNLOADS:-/run/media/system/Files/Downloads/surface-go-build}
if [[ $downloads == /run/media/system/Files/* ]]; then
    [[ $(findmnt -n -o TARGET -T /run/media/system/Files) == /run/media/system/Files ]] || {
        echo 'Mount the FILES drive before downloading.' >&2; exit 1;
    }
fi
mkdir -p "$downloads"
archive="$downloads/buildroot-$BUILDROOT_VERSION.tar.xz"
if [[ ! -f $archive ]]; then
    curl -fL --retry 3 "https://buildroot.org/downloads/buildroot-$BUILDROOT_VERSION.tar.xz" -o "$archive.partial"
    mv "$archive.partial" "$archive"
fi
printf '%s  %s\n' "$BUILDROOT_SHA256" "$archive" | sha256sum -c -
if [[ ! -d buildroot ]]; then
    tar -xJf "$archive"
    mv "buildroot-$BUILDROOT_VERSION" buildroot
fi
[[ $(cat buildroot/.surface-source-sha256 2>/dev/null || true) == "$BUILDROOT_SHA256" ]] || {
    # Re-extract only into a temporary directory to verify the existing tree.
    verify=$(mktemp -d "$downloads/.verify.XXXXXX")
    trap 'rm -rf "$verify"' EXIT
    tar -xJf "$archive" -C "$verify"
    diff -qr --no-dereference --exclude=.surface-source-sha256 "$verify/buildroot-$BUILDROOT_VERSION" buildroot >/dev/null || {
        echo 'Existing Buildroot differs from pinned source; refusing to overwrite it.' >&2; exit 1;
    }
    printf '%s\n' "$BUILDROOT_SHA256" > buildroot/.surface-source-sha256
}
echo "Verified Buildroot $BUILDROOT_VERSION"
