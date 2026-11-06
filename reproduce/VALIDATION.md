# Validation record

These checks were run on the source workstation while developing the single-script
installer. They did **not** provision the source, change its desktop configuration,
restart Chrome, or create a new Kali VM.

## Passed

- `python3 reproduce/check.py`: Python/shell syntax, reviewed template hygiene,
  JSON parsing, fake-home desktop installation/idempotence, new-secret permissions
  and overwrite refusal, and the available i3 config validator.
- `python3 reproduce/test_workstation.py`: nine offline tests, including read-only
  plan behavior, manifest/lock consistency, seven-extension policy generation,
  fake-home profile/MCP setup and idempotence, symlink/conflict refusal, mocked
  native/headless provisioning orchestration, fingerprint parsing, and local Git
  fixture patch application/reapply/dirty-check refusal.
- An isolated temporary-home `npm ci --ignore-scripts`, followed by the explicitly
  approved Bun/OpenCode rebuild, successfully installed and executed:
  - Pi 1.5.0
  - Bitwarden CLI 2026.10.1
  - OpenCode 2.2.5
  - Bun 1.4.4
- The first isolated Node install caught a blocked OpenCode postinstall script.
  The pinned `allowScripts` entries fixed it; the entire isolated install was rerun
  successfully. Transitive install scripts remain disabled.
- A temporary Python venv installed the SHA-256-pinned Linux amd64 uv wheel with
  `--only-binary=:all: --require-hashes --no-deps`; uv and uvx both reported 0.13.7.
- Pinned standalone binary downloads were tested in isolated temporary directories:
  - Lightpanda 1.0.2 direct binary matched its release SHA-256 and reported `version 1.0.2`.
  - Bitwarden Agent Access CLI (`aac`) 0.11.0 archive and extracted member matched
    their release SHA-256 hashes and reported its pinned version string.
- All five vendor keys (Google Chrome, VSCodium, Cloudflare, Bruno, GitHub CLI)
  were fetched through HTTPS and their primary fingerprints inspected. The installer
  pins those fingerprints.
- A complete isolated APT update and package resolution was performed using a private
  temporary status database (`reproduce/audit_sources.py`). All requested packages,
  metapackages and third-party packages resolved cleanly without depending on the
  source machine's already installed state.
- `python3 -B reproduce/smoke_desktop.py --run`: started a real isolated Xvfb server,
  D-Bus session, i3 instance and three test clients. Confirmed:
  - Terminal windows route to `1:term`.
  - File managers and editors route to `3:files`.
  - Initial Files split is approximately 60/40.
  - Test applications move into their designated containers.
  - Manual container resizing persists across subsequent layout watchdog runs.
  - Temporary placeholder marks are completely cleaned up.
  - Both generated systemd user service units validate with `systemd-analyze verify`.
- Public Git remotes advertised both recorded commits. Clean temporary clones
  checked out the pinned Voxl and public-browser revisions; the Voxl patch applied
  and passed reapply/idempotence checks. public-browser built successfully from its
  fresh clone with `npm ci --ignore-scripts && npm run build`. Chrome was not started.
- The complete requested APT set passed **simulation on the existing source** with
  its current package indexes, with zero removals. Installed source packages can
  mask missing target repositories/dependencies; this is not a fresh-guest install.

## Not yet tested / release gates

- A clean Kali server setup from clone to completion, including package postinst and
  debconf behavior, Rust download/build, key rotations and clean-guest dependencies.
- Both desktop modes across an actual guest reboot, lingering user services and
  display/audio startup.
- Fresh Chrome profile loading, policy application, Store availability and extension
  functionality, current Chrome sandbox compatibility, and provider authentication.
- Voxl authenticated access/denial tests, trackpad/audio/large-upload acceptance and
  any optional public TLS deployment.
- Artifact mirror retention, base-image lock, full secret scan and exportable image.
- Publishing this standalone kit to a Git remote.

A successful setup receipt records the actual installed APT versions, selected
source commits and recipe input hashes, but keeps `integration_tested: false`.
No byte-identical or fully tested workstation clone is claimed.
