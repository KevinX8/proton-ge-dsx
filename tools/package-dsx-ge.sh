#!/usr/bin/env bash
# Package a built DSX-GE redist tarball for download by the DSX GUI.
#
# Usage: tools/package-dsx-ge.sh [<built tarball>] [<output dir>]
#
# Defaults to build/DSX-GE.tar.gz and dist/. Set DSX_GE_BASE_URL to the
# directory the archive will be served from; the manifest's "url" is that
# base plus the archive filename.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source_archive="${1:-$repo_root/build/DSX-GE.tar.gz}"
output_dir="${2:-$repo_root/dist}"
base_url="${DSX_GE_BASE_URL:-https://github.com/KevinX8/proton-ge-dsx/releases/latest/download}"

if [ ! -f "$source_archive" ]; then
    echo "error: no built tarball at $source_archive" >&2
    echo "       build the redist tarball in the build directory first" >&2
    exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The version file is one line: "<epoch> <version string>".
tar xzf "$source_archive" -C "$work" DSX-GE/version DSX-GE/proton \
    DSX-GE/compatibilitytool.vdf DSX-GE/toolmanifest.vdf
version=$(awk '{print $2}' "$work/DSX-GE/version")

if [ -z "$version" ]; then
    echo "error: could not read a version from DSX-GE/version" >&2
    exit 1
fi

# Refuse to ship a build without the proxy hook - that is the whole point of
# this Proton build, and a silent miss would look identical to a working
# build until someone noticed the haptics were gone.
if ! grep -q 'BEGIN DSX PROXY HOOK' "$work/DSX-GE/proton"; then
    echo "error: built proton is missing the DSX proxy hook" >&2
    echo "       re-run tools/stage-dsx-patches.sh, then protonprep, then rebuild" >&2
    exit 1
fi

mkdir -p "$output_dir"
# The version is the dotted GE number (e.g. 11.5); prefix with plain "DSX-".
filename="DSX-$version.tar.gz"
cp -f "$source_archive" "$output_dir/$filename"

( cd "$output_dir" && sha256sum "$filename" > "$filename.sha256" )
digest=$(awk '{print $1}' "$output_dir/$filename.sha256")
size=$(stat -c %s "$output_dir/$filename")
released=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat > "$output_dir/dsx-ge-latest.json" <<EOF
{
  "version": "$version",
  "filename": "$filename",
  "url": "$base_url/$filename",
  "sha256": "$digest",
  "size_bytes": $size,
  "released": "$released",
  "install_dir_name": "DSX-GE"
}
EOF

echo "Packaged $output_dir/$filename"
echo "  version  $version"
echo "  sha256   $digest"
echo "  size     $(numfmt --to=iec "$size")"
echo "  manifest $output_dir/dsx-ge-latest.json"
