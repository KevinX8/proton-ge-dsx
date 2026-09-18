#!/usr/bin/env bash
# Stage the DSX patches into a proton-ge-custom checkout and make sure
# protonprep applies them. Idempotent: safe to re-run on a fresh clone.
#
# This repo is the single source of truth for these patches. The patch
# sets apply from different working directories, which is why they are
# staged separately:
#
#   patches/dsx/        -> <checkout>/patches/dsx        applied inside `pushd wine`
#   patches/dsx-proton/ -> <checkout>/patches/dsx-proton applied after `popd`,
#                          at the checkout root, where the `proton` script lives
#
# Getting this wrong is silent: a root-relative patch applied from wine/ just
# fails to find its target.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pgc="${1:-$repo_root}"
protonprep="$pgc/patches/protonprep-valve-staging.sh"
wine_marker='apply_all_in_dir "../patches/dsx"'
proton_marker='apply_all_in_dir "patches/dsx-proton"'

if [ ! -f "$protonprep" ]; then
    echo "error: no protonprep script at $protonprep" >&2
    echo "       pass the path to a proton-ge-custom checkout" >&2
    exit 1
fi

mkdir -p "$pgc/patches/dsx" "$pgc/patches/dsx-proton"
if ! [ "$repo_root" -ef "$pgc" ]; then
    cp -f "$repo_root"/patches/dsx/*.patch "$pgc/patches/dsx/"
    cp -f "$repo_root"/patches/dsx-proton/*.patch "$pgc/patches/dsx-proton/"
fi
echo "Staged $(ls -1 "$repo_root"/patches/dsx/*.patch | wc -l) wine patches and" \
     "$(ls -1 "$repo_root"/patches/dsx-proton/*.patch | wc -l) proton patches into $pgc/patches"

python3 - "$protonprep" "$wine_marker" "$proton_marker" <<'PY'
import sys

path, wine_marker, proton_marker = sys.argv[1:4]
source = open(path).read()
changed = []

# Wine patches: inside the `pushd wine` block, just before autoreconf.
if wine_marker not in source:
    anchor = '    echo "WINE: RUN AUTOCONF TOOLS/MAKE_REQUESTS"\n'
    if source.count(anchor) != 1:
        raise SystemExit("error: expected exactly one autoreconf anchor in %s" % path)
    block = (
        "#WINE CUSTOM PATCHES\n"
        '    echo "WINE: -DSX- deterministic DualSense container IDs"\n'
        '    apply_all_in_dir "../patches/dsx"\n'
        "\n"
    )
    source = source.replace(anchor, block + anchor)
    changed.append("wine")

# Proton-script patches: after `popd`, back at the checkout root.
if proton_marker not in source:
    anchor = "### END PROTON-GE ADDITIONAL CUSTOM PATCHES ###\n"
    if source.count(anchor) != 1:
        raise SystemExit("error: expected exactly one end-of-custom-patches anchor in %s" % path)
    # protonprep hard-resets the wine submodule but never the root `proton`
    # file, so without this checkout a second run double-applies the patch.
    block = (
        "    git checkout proton\n"
        '    echo "PROTON: -DSX- automatically enable proxy mode for launched games"\n'
        '    apply_all_in_dir "patches/dsx-proton"\n'
        "\n"
    )
    source = source.replace(anchor, block + anchor)
    changed.append("proton")

if changed:
    open(path, "w").write(source)
    print("Wired %s patches into %s" % (" and ".join(changed), path))
else:
    print("protonprep already applies both DSX patch sets")
PY
