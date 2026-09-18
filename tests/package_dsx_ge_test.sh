#!/usr/bin/env bash
# Verifies tools/package-dsx-ge.sh against a synthetic build tarball.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Build a fake DSX-GE.tar.gz with the pieces the packager checks for.
mkdir -p "$work/build" "$work/stage/DSX-GE/files/bin"
printf '%s\n' '# --- BEGIN DSX PROXY HOOK (tools/dsx_proton_hook.py) ---' \
    > "$work/stage/DSX-GE/proton"
printf '1786461528 GE-Proton11-5-2-gtest\n' > "$work/stage/DSX-GE/version"
printf '"compatibilitytools" {}\n' > "$work/stage/DSX-GE/compatibilitytool.vdf"
printf '"manifest" {}\n' > "$work/stage/DSX-GE/toolmanifest.vdf"
tar czf "$work/build/DSX-GE.tar.gz" -C "$work/stage" DSX-GE

DSX_GE_BASE_URL="https://example.invalid/dsx-ge" \
    "$repo_root/tools/package-dsx-ge.sh" "$work/build/DSX-GE.tar.gz" "$work/dist"

version="GE-Proton11-5-2-gtest"
archive="$work/dist/DSX-$version.tar.gz"
manifest="$work/dist/dsx-ge-latest.json"

[ -f "$archive" ] || { echo "FAIL: missing $archive"; exit 1; }
[ -f "$archive.sha256" ] || { echo "FAIL: missing checksum"; exit 1; }
[ -f "$manifest" ] || { echo "FAIL: missing manifest"; exit 1; }

( cd "$work/dist" && sha256sum -c "DSX-$version.tar.gz.sha256" >/dev/null ) \
    || { echo "FAIL: checksum does not verify"; exit 1; }

python3 - "$manifest" "$archive" "$version" <<'PY' || exit 1
import hashlib, json, os, sys
manifest_path, archive_path, version = sys.argv[1:4]
manifest = json.load(open(manifest_path))
digest = hashlib.sha256(open(archive_path, "rb").read()).hexdigest()
assert manifest["version"] == version, manifest["version"]
assert manifest["filename"] == os.path.basename(archive_path), manifest["filename"]
assert manifest["sha256"] == digest, "manifest checksum mismatch"
assert manifest["size_bytes"] == os.path.getsize(archive_path), "size mismatch"
assert manifest["install_dir_name"] == "DSX-GE", manifest["install_dir_name"]
assert manifest["url"] == "https://example.invalid/dsx-ge/" + manifest["filename"]
assert manifest["released"], "released must not be empty"
print("manifest OK")
PY

# A build whose proton lacks the hook must be refused, not shipped.
mkdir -p "$work/stage2/DSX-GE"
printf '#!/usr/bin/env python3\n' > "$work/stage2/DSX-GE/proton"
printf '1786461528 GE-Proton11-5-2-gtest\n' > "$work/stage2/DSX-GE/version"
printf '"compatibilitytools" {}\n' > "$work/stage2/DSX-GE/compatibilitytool.vdf"
printf '"manifest" {}\n' > "$work/stage2/DSX-GE/toolmanifest.vdf"
tar czf "$work/build/hookless.tar.gz" -C "$work/stage2" DSX-GE

if "$repo_root/tools/package-dsx-ge.sh" "$work/build/hookless.tar.gz" "$work/dist2" \
        > "$work/hookless.out" 2>&1; then
    echo "FAIL: packager accepted a build with no DSX proxy hook"
    exit 1
fi
grep -q 'missing the DSX proxy hook' "$work/hookless.out" \
    || { echo "FAIL: wrong error for a hookless build"; cat "$work/hookless.out"; exit 1; }

echo "PASS: package-dsx-ge.sh"
