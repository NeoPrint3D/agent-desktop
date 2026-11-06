# Rebuild this workstation from a fresh Kali install

**Goal: install Kali → clone this repository → run one setup script.**

The entry point now exists. On a **fresh Kali Rolling amd64 guest**, logged in as
your normal user with sudo:

```sh
git clone <THIS_REPOSITORY_URL> workstation-rebuild
cd workstation-rebuild
./setup.sh --apply --headless
```

`--headless` is the server/streaming mode: a private Xvfb + i3 desktop runs under
your user, starts now and after boot, and serves authenticated Voxl on loopback.
For a VM with a normal graphical console, use `./setup.sh --apply` instead, then
reboot and log into **i3**. Do not run both desktop modes for the same user.

Running `./setup.sh` without `--apply` prints an offline plan and changes nothing.
Setup asks for sudo and a fresh-machine confirmation (`--yes` skips the latter).
It never reinstalls Kali, partitions disks, copies accounts, or reboots automatically.

## Included in the single script

- i3 desktop, XFCE utilities, Polybar, Rofi, terminal/file-manager helpers and layout.
- Reviewed application selection and the source Kali security metapackages.
- Google Chrome, VSCodium, Bruno and cloudflared through signed vendor repositories
  with pinned signing-key fingerprints.
- Fresh **Your Chrome** and **Agent** profile definitions, selecting `Profile 1` for
  automation. Seven observed extensions install automatically through a Chrome
  machine policy when Chrome launches. No cookies/history/passwords copied.
- VSCodium theme settings and the two observed editor extensions.
- Pi **1.5.0**, Bitwarden CLI **2026.10.1**, OpenCode **2.2.5**, Bun **1.4.4**:
  version/integrity-locked npm dependencies installed without root.
- uv/uvx **0.13.7**, installed from a SHA-256-pinned wheel in a separate environment.
- Pinned public-browser source/build, new local MCP configuration, browser launcher.
- Pinned Voxl source **plus the source workstation's uncommitted runtime/UI fixes**,
  built with its lockfile; new authentication generated on the target.
- Pinned Lightpanda **1.0.2** and Agent Access CLI (`aac`) **0.11.0** binary
  artifacts, verified with cryptographic SHA-256 hashes against immutable releases.

## Not yet an exact clone

**A clean Kali end-to-end rebuild has not yet been tested.** Offline tests, isolated
CLI-install tests and a real isolated Xvfb/i3 desktop layout smoke test pass, but
this is not a claim that every app and service already works in a new VM. Remaining
work is recorded in [`reproduce/workstation.json`](reproduce/workstation.json),
including licensed Burp Pro, application-specific settings, and an archived
APT/base image for exact OS/package versions. Chrome installs current Store
extension versions, not the recorded historical versions. Managed extensions apply
to all Chrome profiles on this guest, not only Agent.

You must sign in again to browser sites, AI providers, GitHub and Bitwarden, and
supply new SSH keys/tunnel credentials/licenses. No secrets or source user state
are placed in the repository. Nginx stays masked until remote HTTPS is explicitly
configured; no source certificates or network identity are cloned.

The local kit still needs to be published to a Git remote before the placeholder
clone URL above can be used from another machine.

See [setup details, access and validation](reproduce/README.md).
