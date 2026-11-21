# Surface Go Gaming OS

Source specification: `/run/media/system/Files/Downloads/Surface_Go_Gaming_OS_Full_Project.pdf`
(8 pages), read on November 19, 2026. The PDF is product input, not a report of
implemented capabilities. Its reference artwork is concept art.

Delivery preference (latest owner instruction, November 19, 2026): erase ALL
partitions and files on the reviewed external USB formerly containing
UDFVOLUME / Windows H:, then flash the validated live image to that whole USB.
This explicitly supersedes the earlier preserve-H/old-boot-area-only plan.
Exact target model, stable serial, capacity and by-id path are stored privately
under ignored `artifacts/usb-execution/`; do not identify the target by a device
letter. Root alone performs the physical operation after matching final-image
VM boot checks, with `installer/flash-usb.py` identity/hash guards and full
image readback. Normal flashing is not full-media secure erasure. No Surface
internal-disk erase has been authorized.

Public source destination: https://github.com/HelloSpaghettiBot/403OS . Keep
build outputs, private device plans, saves, clients, accounts and keys out of
GitHub.
Build an original controller-first native C++/SDL3 shell and a dedicated
Buildroot Linux image for Surface Go Gen 1. Preserve the specified HOME
carousel, system status strip and app dock without bundling Nintendo artwork.
First deliver a compiled foundation, then validate UEFI boot and installation
inside disposable QEMU disks before any Surface installation recommendation.

Later milestones include real hardware enablement, library and emulator
integration, WiFi/Bluetooth UI, WebKit browser, Moonlight and Tailscale with
persistent identity, PokeMMO official-client setup, and the exact Digimon World
Online product after identification. No games, BIOS, accounts or keys ship.

The first image is a development foundation, not a complete gaming OS or
release candidate. Weston kiosk mode provides DRM/KMS and Wayland without a
desktop. SDL3 initially uses software rendering. Intel Mesa acceleration is a
separate required hardware milestone, not inferred from software VM success.
