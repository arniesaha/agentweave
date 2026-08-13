#!/bin/sh
set -eu

repo="${AGENTWEAVE_REPOSITORY:-Arniesaha/agentweave}"
version="${AGENTWEAVE_VERSION:-latest}"
install_dir="${AGENTWEAVE_INSTALL_DIR:-/usr/local/bin}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset="linux-x86_64" ;;
  Darwin-x86_64) asset="macos-x86_64" ;;
  Darwin-arm64) asset="macos-arm64" ;;
  *) echo "Unsupported platform: $(uname -s) $(uname -m)" >&2; exit 1 ;;
esac

if [ "$version" = "latest" ]; then
  base="https://github.com/${repo}/releases/latest/download"
else
  base="https://github.com/${repo}/releases/download/${version}"
fi

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/agentweave-install.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT INT TERM
archive="agentweave-${asset}.tar.gz"

curl -fsSL "${base}/${archive}" -o "${tmp_dir}/${archive}"
curl -fsSL "${base}/SHA256SUMS" -o "${tmp_dir}/SHA256SUMS"
(
  cd "$tmp_dir"
  expected=$(grep "  ${archive}$" SHA256SUMS || true)
  [ -n "$expected" ] || { echo "Checksum missing for ${archive}" >&2; exit 1; }
  printf '%s\n' "$expected" | shasum -a 256 -c -
  tar -xzf "$archive"
)

mkdir -p "$install_dir" 2>/dev/null || true
if [ -w "$install_dir" ]; then
  install -m 0755 "${tmp_dir}/agentweave" "${install_dir}/agentweave"
else
  sudo install -m 0755 "${tmp_dir}/agentweave" "${install_dir}/agentweave"
fi

echo "Installed agentweave to ${install_dir}/agentweave"
"${install_dir}/agentweave" version
