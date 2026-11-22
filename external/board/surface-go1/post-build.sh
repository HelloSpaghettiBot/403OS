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
if ! test -x "$TARGET_DIR/sbin/debugfs"; then
    echo 'Missing updater filesystem inspection tool: /sbin/debugfs; run e2fsprogs-reconfigure' >&2
    exit 1
fi
python3 "$board/../../../scripts/check-hardware-config.py" \
    --config "$BUILD_DIR/linux-6.12.118/.config" --target "$TARGET_DIR" \
    --buildroot-config "$BINARIES_DIR/../.config" \
    --bluez-config-log "$BUILD_DIR/bluez5_utils-5.86/config.log"
if ! test -f "$TARGET_DIR/usr/lib/weston/surface-rotation.so"; then
    echo 'Missing compositor rotation module; run surface-rotation-rebuild' >&2
    exit 1
fi
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
install -D -m 0755 "$board/../../../services/update-device.py" "$TARGET_DIR/usr/sbin/surface-update-device"
install -D -m 0755 "$board/../../../installer/seed-data.py" "$TARGET_DIR/usr/sbin/surface-seed-data"
install -D -m 0755 "$board/../../../scripts/library-scan.py" "$TARGET_DIR/usr/bin/surface-library-scan"
install -D -m 0755 "$board/../../../services/dw3/dw3-setup" "$TARGET_DIR/usr/bin/dw3-setup"
install -D -m 0755 "$board/../../../services/dw3/dw3-launch" "$TARGET_DIR/usr/bin/dw3-launch"
install -D -m 0644 "$board/../../../services/dw3/dw3-setup.py" "$TARGET_DIR/usr/libexec/surface/dw3-setup.py"
install -D -m 0755 "$board/../../../services/library-volumes.py" "$TARGET_DIR/usr/bin/surface-library-volumes"
install -D -m 0755 "$board/../../../services/windows-runner.py" "$TARGET_DIR/usr/bin/surface-windows-runner"
install -D -m 0755 "$board/../../../services/grow-data.py" "$TARGET_DIR/usr/sbin/surface-grow-data"
install -D -m 0755 "$board/../../../services/display.py" "$TARGET_DIR/usr/sbin/surface-display"
install -D -m 0644 "$board/../../../services/orientation.py" "$TARGET_DIR/usr/sbin/orientation.py"
install -D -m 0755 "$board/../../../services/profiles.py" "$TARGET_DIR/usr/sbin/surface-profiles"
install -D -m 0755 "$board/../../../services/library-discovery.py" "$TARGET_DIR/usr/sbin/surface-library-discovery"
install -D -m 0755 "$board/../../../services/bluetooth.py" "$TARGET_DIR/usr/sbin/surface-bluetooth"
install -D -m 0755 "$board/../../../services/bluetooth-pair.py" "$TARGET_DIR/usr/sbin/surface-bluetooth-pair"
install -D -m 0755 "$board/../../../services/power.py" "$TARGET_DIR/usr/sbin/surface-power"
install -D -m 0644 "$board/../../../services/bluetooth-crypto.py" "$TARGET_DIR/usr/libexec/surface/bluetooth-crypto.py"
install -D -m 0644 "$board/../../../services/bluetooth-sockets.py" "$TARGET_DIR/usr/libexec/surface/bluetooth-sockets.py"
install -D -m 0755 "$board/../../../services/mount-data.py" "$TARGET_DIR/usr/sbin/surface-mount-data"
# Remove the previous global PARTUUID entry during incremental builds too.
# S20data selects partition 3 from the actual mounted root disk instead.
sed -i '\|^[^#].*[[:space:]]/data[[:space:]]|d; /# SURFACE_DATA/d' "$TARGET_DIR/etc/fstab"
printf '# SURFACE_DATA: mounted by S20data from the running root disk\n' >> "$TARGET_DIR/etc/fstab"
