# Build from source

Use a Linux host with rootless Podman, a systemd user session, Git, curl, tar,
Python 3 and a C++20 compiler. VM tests also need QEMU/KVM and OVMF firmware.

```sh
git clone https://github.com/HelloSpaghettiBot/403OS.git
cd 403OS
export SURFACE_DOWNLOADS="${XDG_CACHE_HOME:-$HOME/.cache}/403os/downloads"
scripts/test.sh
scripts/build.sh
scripts/vm-create.sh --visible
```

For the disposable GUI acceptance run, including account setup, profile
switching, live rotation and installation into a new virtual SATA disk:

```sh
scripts/vm-create.sh --test-profiles --test-rotation --test-install-ui
```

The host driver supplies synthetic test passwords through the normal UI.
Images contain no enrolled account password or authentication bypass.
These checks use virtual devices and do not validate physical controllers,
touch input or accelerometers.

Builds use ten workers, a 24 GiB memory throttle, 32 GiB hard/container caps
and 1 GiB swap. Only one heavy workload runs at a time. VM host memory has a
separate 6 GiB cap. The first build downloads several GB; incremental builds
reuse `output/` and the configured download cache.

After UI changes, run `scripts/build.sh surface-shell-rebuild all`.
Use `scripts/build.sh --configure` after configuration changes.
Pass `--ovmf /path/to/OVMF_CODE.fd` to VM commands if needed.

| Output under `output/images/` | Purpose |
| --- | --- |
| `surface-go.img` | Bootable USB with its embedded installation payload |
| `surface-go-base.img` | Pristine internal-storage installation image |
| `SHA256SUMS` | Separate checksums for both images |
| `installer-media.json` | Packaging and embedded-payload verification |

```sh
(cd output/images && sha256sum -c SHA256SUMS)
```

See [installation](INSTALL_RECOVERY.md) for disk-writing and recovery steps.
Generate Buildroot `legal-info` before distributing a compiled image.
