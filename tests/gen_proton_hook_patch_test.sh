#!/usr/bin/env bash
# The committed 0014 patch must carry the same payload the generator produces
# from tools/dsx_proton_hook.py, and must apply to a pristine `proton` to
# produce valid Python with the restore call wired in. Plain upstream
# line-number drift is not a failure: `patch` relocates the hunks itself.
#
# Usage: tests/gen_proton_hook_patch_test.sh [<proton-ge-custom checkout>]
# Defaults to this repo, which is itself a proton-ge-custom fork.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pgc="${1:-$repo_root}"
committed="$repo_root/patches/dsx-proton/0014-proton-auto-enable-dsx-proxy.patch"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

python3 "$repo_root/tools/gen_proton_hook_patch.py" "$pgc" "$work/regenerated.patch"
# Compare the payload, not the raw text: a hunk header ("@@ -233,6 +233,216
# @@") carries the upstream line number, which shifts whenever anything above
# the anchor changes. Comparing text therefore fails on every GE release that
# touches `proton` at all - even though `patch` relocates the hunk by itself,
# which the apply check below proves. Only the added/removed lines matter.
if ! diff -u <(grep -v '^@@' "$committed") <(grep -v '^@@' "$work/regenerated.patch"); then
    echo "FAIL: the committed 0014 patch no longer matches tools/dsx_proton_hook.py." >&2
    echo "      Regenerate and commit the result in this repo:" >&2
    echo "        python3 tools/gen_proton_hook_patch.py $pgc \\" >&2
    echo "            patches/dsx-proton/0014-proton-auto-enable-dsx-proxy.patch" >&2
    exit 1
fi

mkdir -p "$work/tree"
git -C "$pgc" show HEAD:proton > "$work/tree/proton"
patch -d "$work/tree" -p1 --silent < "$committed"
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' \
    "$work/tree/proton"

# The hook is worthless if launch never restores the previous proxy state -
# exactly what broke when GE-Proton 11-7 restructured Session.run.
grep -q 'BEGIN DSX PROXY HOOK' "$work/tree/proton" \
    || { echo "FAIL: hook markers missing after apply" >&2; exit 1; }
grep -q 'dsx_restore_proxy(dsx_snapshot)' "$work/tree/proton" \
    || { echo "FAIL: Session.run not wired for restore" >&2; exit 1; }

echo "PASS: gen_proton_hook_patch.py"
