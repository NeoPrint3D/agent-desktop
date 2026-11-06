#!/usr/bin/env bash
# Build an explicitly selected Voxl source checkout, not the crates.io release.
set -euo pipefail
repo=${VOXL_SOURCE_DIR:-}
version=${VOXL_RUST_VERSION:-1.99.1}
if [[ ${1:-} != --apply || $# != 1 ]]; then
  echo "Plan: install Rust $version with rustup; build VOXL_SOURCE_DIR with Cargo.lock;"
  echo 'then install target/release/voxl into ~/.local/bin.'
  echo 'Set VOXL_SOURCE_DIR=/absolute/path/to/voxl and use --apply in the guest.'
  exit 0
fi
if [[ $(id -u) == 0 || ${REPROVISION_FRESH_GUEST:-} != YES ]]; then
  echo 'Run as the target user with REPROVISION_FRESH_GUEST=YES inside the fresh guest.' >&2
  exit 1
fi
[[ $repo == /* && -f $repo/Cargo.toml && -f $repo/Cargo.lock && -f $repo/src/lib.rs ]] || {
  echo 'Set VOXL_SOURCE_DIR to the absolute path of your reviewed Voxl source checkout.' >&2; exit 1;
}
export PATH="$HOME/.cargo/bin:$PATH"
command -v esbuild >/dev/null
command -v rustup >/dev/null
rustup toolchain install "$version" --profile minimal
# Some Debian/Kali libyuv-dev builds have the library/header but no .pc file.
if ! pkg-config --exists libyuv; then
  [[ -f /usr/include/libyuv.h || -d /usr/include/libyuv ]] || { echo 'Install libyuv-dev first.' >&2; exit 1; }
  pc="$HOME/.cache/voxl-build/pkgconfig"
  mkdir -p "$pc"
  printf 'Name: libyuv\nDescription: system libyuv\nVersion: 0.0.0\nLibs: -lyuv\nCflags: -I/usr/include\n' > "$pc/libyuv.pc"
  export PKG_CONFIG_PATH="$pc${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi
cd "$repo"
cargo "+$version" build --locked --release
install -d "$HOME/.local/bin"
install -m 755 target/release/voxl "$HOME/.local/bin/voxl"
echo 'Installed the custom source build. No running service was restarted.'
