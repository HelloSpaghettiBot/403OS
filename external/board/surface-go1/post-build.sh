#!/bin/sh
set -eu
board=$(dirname "$0")
# A newly selected alsa-utils subtool needs package reinstallation in an
# incremental tree. Refuse an image missing its playback/settings tools.
for binary in usr/bin/amixer usr/bin/aplay usr/sbin/alsactl; do
    if ! test -x "$TARGET_DIR/$binary"; then
        echo "Missing required audio executable: /$binary; run alsa-utils-reinstall" >&2
        exit 1
    fi
done
mkdir -p "$BINARIES_DIR/efi-part/EFI/BOOT"
cat > "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg" <<'EOF'
set default=0
set timeout=0
menuentry "Surface Go Gaming OS - development" {
    linux /bzImage root=PARTUUID=8b4a530e-d6d4-4e69-9307-310000000001 rootwait rw console=tty0 console=ttyS0,115200 quiet loglevel=3
}
EOF
install -D -m 0755 "$board/../../../installer/install-vm.sh" "$TARGET_DIR/usr/sbin/surface-install-vm"
printf 'surface-go-foundation-0.1.0\n' > "$TARGET_DIR/etc/surface-image-id"
mkdir -p "$TARGET_DIR/data"
python3 "$board/../../../tests/make-homebrew.py" "$TARGET_DIR/usr/share/surface-tests/input.nes"
install -D -m 0755 "$board/../../../services/pokemmo-setup/pokemmo-setup" "$TARGET_DIR/usr/bin/pokemmo-setup"
install -D -m 0755 "$board/../../../services/pokemmo-setup/pokemmo-launch" "$TARGET_DIR/usr/bin/pokemmo-launch"
install -D -m 0644 "$board/../../../services/pokemmo-setup/pokemmo-setup.py" "$TARGET_DIR/usr/libexec/surface/pokemmo-setup.py"
install -D -m 0755 "$board/../../../services/install-device.py" "$TARGET_DIR/usr/sbin/surface-install-device"
install -D -m 0755 "$board/../../../scripts/library-scan.py" "$TARGET_DIR/usr/bin/surface-library-scan"
install -D -m 0755 "$board/../../../services/dw3/dw3-setup" "$TARGET_DIR/usr/bin/dw3-setup"
install -D -m 0755 "$board/../../../services/dw3/dw3-launch" "$TARGET_DIR/usr/bin/dw3-launch"
install -D -m 0644 "$board/../../../services/dw3/dw3-setup.py" "$TARGET_DIR/usr/libexec/surface/dw3-setup.py"
install -D -m 0755 "$board/../../../services/library-volumes.py" "$TARGET_DIR/usr/bin/surface-library-volumes"
install -D -m 0755 "$board/../../../services/windows-runner.py" "$TARGET_DIR/usr/bin/surface-windows-runner"
install -D -m 0755 "$board/../../../services/grow-data.py" "$TARGET_DIR/usr/sbin/surface-grow-data"
install -D -m 0755 "$board/../../../services/display.py" "$TARGET_DIR/usr/sbin/surface-display"
grep -q SURFACE_DATA "$TARGET_DIR/etc/fstab" || printf '/dev/disk/by-partuuid/8b4a530e-d6d4-4e69-9307-310000000002 /data ext4 defaults,noauto 0 2 # SURFACE_DATA\n' >> "$TARGET_DIR/etc/fstab"
