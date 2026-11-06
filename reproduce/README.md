# One-command workstation provisioning

## Fresh Kali server → this workstation's desktop/workflow

Use an official **Kali Rolling amd64** installer/VM, verify its checksum/signature,
and create a normal desktop user with sudo. A minimal/server installation is fine;
the script adds Xorg/i3/XFCE and applications. It requires Python 3, Git, sudo, a
booted systemd guest, working DNS and Internet access to Kali and vendor archives.
Install Git/Python first if your minimal base omitted them. Do not run as root.

The source is a Proxmox LXC, not a bootable disk image; a fresh VM provides its own
kernel, bootloader, storage and host keys. The recipe does not configure hypervisor
networking, disks, drivers, SSH access or target-specific guest integration.
Allow **at least 8 GB RAM and 100 GB disk**, preferably more for the full security
selection and Rust build. Start with a disposable VM and a snapshot.

From the independently cloned `workstation-rebuild` repository:

```sh
./setup.sh                          # offline/read-only plan
./setup.sh --headless               # offline plan for the server mode
./setup.sh --apply --headless       # ONE provisioning command for a server
```

For a graphical VM console instead:

```sh
./setup.sh --apply
# Reboot when ready, select i3 in the display manager and sign in.
```

Only run apply on a **fresh target**, never this existing workstation. It requests
sudo, asks you to type `SETUP FRESH KALI`, and uses APT with `--no-remove`. APT changes
are otherwise unattended (`-y`); debconf may still ask package-specific questions.
`--yes` skips only the fresh-target confirmation, not sudo or package questions.
There is no automatic reboot, disk formatting, source export, or destructive reset.

Do not combine native and headless desktops for one user. Headless mode requires a
non-graphical login with a working systemd user manager, enables lingering for that
user, masks LightDM, chooses `multi-user.target`, and starts the authenticated
Xvfb/i3 desktop as `workstation-headless.service`. Native mode enables LightDM for
the next graphical boot. The headless X server uses fresh Xauthority, no `-ac`, and
no TCP listener. Its default screen is 1920×1080×24.

## Inputs and installed state

| Input | Purpose |
| --- | --- |
| `workstation.json` | Application repositories/key fingerprints, Git commits, profiles, extension IDs, known gaps |
| `packages.list`, `applications.list`, `security-tools.list` | Reviewed APT application/metapackage selection |
| `node-tools/package.json`, `package-lock.json` | Pi, Bitwarden CLI, OpenCode and Bun with transitive versions/integrity |
| `python-tools.requirements.txt` | uv/uvx version and Linux amd64 wheel hash |
| `desktop/` | Reviewed portable desktop/editor configuration and helpers |
| `patches/voxl-workstation.patch` | Uncommitted source runtime/UI fixes, on top of the pinned Voxl commit |
| `network/nginx/` | Optional HTTPS proxy example; NOT automatically enabled |

The installer downloads vendor signing keys over HTTPS and checks their **primary
fingerprints** against the manifest. A rotation fails closed until reviewed.
Each repository uses a dedicated `signed-by` keyring. Bruno's documented repository
uses HTTP, but its index/packages must authenticate through the pinned signing key;
unsigned archives and TLS verification bypasses are not enabled. Kali's existing
signed repositories are retained. Version-locked APT packages still require an
archived mirror; current Kali/vendor repositories can change or remove packages.

Node tools live under `~/.local/share/workstation-rebuild/node-tools/`; only named
CLI symlinks go into `~/.local/bin`. Dependency lifecycle scripts are disabled during
`npm ci`; only the pinned Bun and OpenCode packages' binary setup scripts are then
approved/rebuilt. The Python tools use a separate venv and a hash-checked wheel,
not system pip. Tested in an isolated temporary home on the source: all four Node
CLI versions and uv/uvx versions matched. This does not replace a guest test.

Sources live under `~/.local/share/workstation-rebuild/sources/`. Commits are fixed;
Voxl's patch is applied only if absent. Unexpected revisions, unknown modifications,
untracked files and conflicting CLI destinations fail rather than wiping work.
Don't edit managed checkouts if you expect unattended reruns. Network/build/package
failures stop setup immediately; correct the reported issue and rerun the same
command. Completed installations are not rolled back automatically.

Desktop templates back up changed files under
`~/.local/state/voxl-reproduce-backups/`. Generated user configuration backs up
changes under `~/.local/state/workstation-rebuild/backups/`. A successful provisioning
receipt is written to `~/.local/state/workstation-rebuild/receipt.json`, along with
`remaining-gaps.json`. A prior receipt's timestamp is not evidence that a later
failed attempt completed. Existing authentication and browser databases are retained
on rerun; a fresh browser root is seeded only if it does not already exist.

## Chrome profiles and extensions

The dedicated root is `~/.public-browser/harness/default/chrome-profile`, separate
from Chrome's usual default data directory. Seeded profile names:

- `Default`: Your Chrome
- `Profile 1`: Agent, selected by the harness and browser launcher

Only name/directory definitions are generated. Chrome creates its own remaining
state on first launch. Built-in Chrome components are not treated as extensions
to reinstall. The seven third-party IDs were read from this source's active profile:
Hack-Tools, uBlock Origin Lite, Dark Reader, d3coder, Wappalyzer, Mod Header and
Proxy SwitchyOmega 3 (ZeroOmega).

Linux Chrome's machine policy at
`/etc/opt/chrome/policies/managed/workstation-extensions.json` uses `normal_installed`:
it automatically installs these extensions but allows disabling them. **This applies
to every Chrome profile on the guest**, not only Agent. It causes Chrome to report
that it is managed. Review requested permissions and policies at `chrome://policy`,
then check `chrome://extensions` after the initial network download. The historical
versions are recorded for audit only; Store removal, compatibility changes, enterprise
policy restrictions or vendor updates can prevent an exact match. Extension settings,
proxy passwords, extension storage and licenses are not exported. Removing the policy
after installation changes management, not necessarily already installed extensions.

The public-browser harness is configured through its supported ignored
`profiles.local.json`, with the new UID/home, explicit Agent pin, loopback CDP and
fresh download directory. A new Pi MCP entry uses its locally built server and
codemode exposure; unrelated/existing MCP entries are preserved. No `.pi` account,
authentication, session, model-store or provider files are copied. The browser
launcher is `~/.local/bin/pb-harness` and starts with the i3 session. CDP at
**127.0.0.1:9222 is unauthenticated**; local processes can control signed-in Chrome.
Never expose it through a network listener or reverse proxy.

## Access the headless desktop

Voxl listens on **127.0.0.1:6901** with a newly generated password in
`~/.config/voxl/stream.env` (mode 600). Read it privately on the guest; do not commit
or paste it into logs. The password is not printed by provisioning. Voxl shares only
the target user's home, not `/`. The service starts after X/display credentials are
imported by the i3 session. Give first startup/browser extension installation time.

If SSH is configured on the guest, use a tunnel from your viewing computer:

```sh
ssh -L 6901:127.0.0.1:6901 <guest-user>@<guest-address>
# Open http://localhost:6901 and authenticate with the NEW target password.
```

Useful target-side checks:

```sh
systemctl --user status workstation-headless.service voxl-stream.service
journalctl --user -u workstation-headless.service -u voxl-stream.service -n 80
~/.local/bin/pb-harness status --json
ss -ltn                    # confirm Voxl and CDP are loopback-only
```

Nginx is masked before package installation to avoid publishing its default site.
For public HTTPS, deliberately configure a new hostname, target certificate/key,
firewall, access controls and the example under `network/nginx/`. Only then unmask
Nginx, validate with `nginx -t` and enable/start it. The template's upload cap is
unlimited and buffering is off; choose finite limits and monitor disk space if
publicly reachable. The kit never copies source certificates or tunnel credentials.

## What “exactly like this one” still requires

This is now a one-entry-point **functional rebuild recipe**, not a verified exact
clone. Do not call it complete until a clean guest passes the checklist below.

1. **Remaining software:** Lightpanda, `aac`, and licensed Burp Pro still need reviewed,
   versioned, checksummed installer sources and any required licenses. The normal
   Kali Burp package is included; no proprietary activation data is copied.
2. **Application state/settings:** the reviewed desktop and VSCodium appearance are
   included; no blanket `.config` copy. Shell plugins, Neovim configuration, bookmarks,
   user projects, downloads, documents, API collections and browser/extension storage
   still require separate, explicitly reviewed inputs. Those can contain secrets.
3. **Credentials:** sign into browser sites, Pi providers, GitHub and Bitwarden afresh;
   generate new SSH/GPG keys and configure tunnels/VPNs/Wi-Fi locally. Licenses are
   supplied legitimately by the user, not embedded in this repo.
4. **Bit-for-bit/version matching:** retain a verified Kali base-image checksum, signed
   APT snapshot and third-party installers, Rust/dependency artifacts and extension
   artifacts where lawful/supported. Pinning names/versions alone does not preserve
   downloadable packages. Keep a security-update strategy; stale exact versions are
   not automatically desirable.
5. **Publication:** the source directory is still local; publish this standalone kit
   to a Git remote before a fresh guest can clone it. Do not publish `inventory/` or
   `private/` without review. No remote/push is configured by these scripts.

## Validation

Local, non-provisioning checks:

```sh
python3 reproduce/check.py
python3 reproduce/test_workstation.py
./setup.sh --headless
```

On a fresh disposable Kali guest:

- Run the single setup command, record package/build failures and the final receipt.
- Reboot; verify the chosen desktop mode, i3 layouts/split resizing, dropdown terminal,
  Rofi, Polybar and theme. Ensure no watchdog resets manually adjusted widths.
- Confirm Agent is selected, all seven extensions installed, two VSCodium extensions
  work, and `pb-harness status` shows healthy loopback CDP after a reboot.
- Verify Pi, Bun, uv/uvx, Bitwarden and OpenCode versions and fresh sign-in behavior.
- Verify unauthenticated Voxl file/list/upload requests are denied. Authenticate with
  the new password; test pointer input, mobile trackpad, audio and a >100 MiB upload.
- Confirm Voxl/CDP bind only loopback and Nginx is masked until explicitly configured.
- Confirm no source cookies, sessions, credentials, private keys or fixed source IP/UID
  have entered the repo or any image export. Run a maintained secret scanner plus
  manual review; an allowlist/test suite is risk reduction, not proof of no secrets.

**Status:** offline tests and isolated tool installation tests passed on the source;
no full fresh-Kali VM rebuild, service/boot acceptance test or image export has yet
been performed. No ISO or published VM image is claimed.

## Lower-level tools and private audit inventory

The original individually reviewed scripts remain available for troubleshooting:
`packages.sh`, `install-desktop.py`, `build-voxl.sh`, `prepare-stream.py` and
`headless-desktop.sh`. Their defaults remain dry-run/opt-in. `build-voxl.sh` accepts
`VOXL_SOURCE_DIR` for a separate reviewed source checkout. Do not point these at the
existing source machine with apply enabled.

To refresh the private metadata-only source inventory into a NEW directory:

```sh
python3 reproduce/capture.py reproduce/inventory-new --voxl-source /path/to/voxl
```

It records all installed APT versions, manual selection and tool metadata with
private permissions, not configurations or repository credentials. `apt-exact.list`
is an audit lock, not an automatic installer list; it includes dependencies and
container/base packages that should not be imposed on a new VM.

Official references: [Kali VM images](https://www.kali.org/get-kali/),
[VSCodium](https://vscodium.com/), [Google Linux repositories](https://www.google.com/linuxrepositories/),
[Cloudflare packages](https://pkg.cloudflare.com/), [Bruno](https://docs.usebruno.com/get-started/install-bruno),
[Chrome extension policy](https://chromeenterprise.google/policies/#ExtensionSettings),
[uv installation](https://docs.astral.sh/uv/getting-started/installation/).
