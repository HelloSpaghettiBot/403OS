# Surface Go Gaming OS

The owner asked to read and start Surface_Go_Gaming_OS_Full_Project.pdf on
2026-11-19. This is a separate project from PokemonPrivate. Follow PROJECT.md
and the PDF as the product specification; do not copy the unrelated Pokémon
MMO instructions inherited from the drive root into this project's scope.

- Build real C++/SDL3 code and a Buildroot br2-external image, incrementally.
- Maintain docs/STATUS.md, docs/ROADMAP.md and evidence-based VM reports.
- Never claim Surface hardware support from a VM result.
- The latest owner instruction authorizes erasing ALL partitions/files on the
  specifically reviewed external H USB for a normal whole-device image flash.
  Its exact model/serial/capacity belong only in ignored artifacts/usb-execution/
  private plans. Root alone executes after final matching VM boot validation,
  using installer/flash-usb.py identity, checksum, exclusive-open and readback
  guards. This supersedes the old preserve-H/boot-area-only authorization.
- No other host disk writes, unattended real-device installation, proprietary
  game data, embedded credentials, or changes to the immutable host OS.
  No Surface internal-disk erase has been authorized.
- Public source destination: https://github.com/HelloSpaghettiBot/403OS .
  Never publish private USB plans, device identities, application data or keys.
- Heavy compilation must use scripts/resource-budget.sh and a container with
  a 32 GiB cap (owner update, November 19, 2026), 24 GiB throttle and 1 GiB swap.
  Ten workers (owner approved), one heavy build at a time. No uncapped retry.
- New downloads belong on the mounted FILES drive under Downloads.
- Do not delegate to subagents unless the owner explicitly requests it.
