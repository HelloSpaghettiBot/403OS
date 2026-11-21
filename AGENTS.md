# 403 OS project instructions

Follow PROJECT.md for scope. Build real C++/SDL3 code and a Buildroot
br2-external image. Maintain STATUS, ROADMAP and evidence-based VM reports.

- Never infer physical Surface support from VM results or source fixtures.
- Keep copyrighted game data, proprietary clients and credentials out of Git.
- Real-device writes require explicit authorization for the exact target.
  Verify stable model/serial/capacity, checksums, mount/swap state, exclusive
  access and complete readback. USB authorization never authorizes erasing
  an internal disk or another device. No unattended hardware installation.
- Keep real device identities and execution receipts in ignored artifacts;
  checked-in examples must use synthetic identities. Never publish saves,
  accounts, keys, application state or personal storage information.
- Heavy compilation uses scripts/resource-budget.sh and the capped container:
  ten workers, one heavy workload, 24 GiB throttle, 32 GiB hard/container caps,
  1 GiB swap. No uncapped fallback. Preserve the separate VM limits.
- Use the configured download cache and respect session storage preferences.
- Publish source to https://github.com/HelloSpaghettiBot/403OS only within
  the user's authorized scope. Source publication is distinct from a release.
