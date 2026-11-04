#!/usr/bin/env bash
# tools/check-patchlog.sh passes protonprep noise and catches real patch failures.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
check="$repo_root/tools/check-patchlog.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Lines from real GE-Proton11-12 protonprep runs that all applied cleanly.
cat > "$work/clean.txt" <<'EOF'
Hunk #1 succeeded at 860 (offset 125 lines).
Hunk #3 succeeded at 133 (offset 1 line).
Hunk #1 succeeded at 171 (offset -5 lines).
gitapply.sh -d . < advapi32-LsaLookupPrivilegeName/0001-advapi32-Fix-error-code-when-calling-LsaOpenPolicy-f.patch
gitapply.sh -d . < ws2_32-af_unix/0004-server-Introduce-error-when-attempting-to-create-a-S.patch
WINE: -STAGING- d3drm-starwars cleanup -Werror
0 failed
EOF
bash "$check" "$work/clean.txt" > /dev/null || { echo "FAIL: clean log flagged"; exit 1; }

while IFS= read -r line; do
    printf '%s\n' "$line" > "$work/bad.txt"
    if bash "$check" "$work/bad.txt" > /dev/null; then
        echo "FAIL: missed: $line"
        exit 1
    fi
done <<'EOF'
Hunk #2 FAILED at 133.
1 out of 3 hunks FAILED -- saving rejects to file dlls/xinput1_3/main.c.rej
error: patch failed: dlls/xinput1_3/main.c:99
error: dlls/winebus.sys/bus_udev.c: patch does not apply
fatal: unable to read
can't find file to patch at input line 5
Reversed (or previously applied) patch detected!  Assume -R? [n]
ERROR: Failed to apply patch, aborting!
EOF
echo "PASS: check-patchlog.sh"
