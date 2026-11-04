#!/usr/bin/env bash
# Fail when a protonprep log shows a patch that did not apply.
#
# Usage: tools/check-patchlog.sh <patchlog.txt>
#
# Matches only what patch, git apply and wine-staging print on a failure. Bare
# words are not enough: patch names contain "error" (0001-...-Fix-error-code...),
# staging prints "cleanup -Werror", and a hunk that applied at an offset reads
# "Hunk #1 succeeded at 860 (offset 125 lines)".
set -euo pipefail

log="${1:?usage: check-patchlog.sh <patchlog.txt>}"
failure='Hunk #[0-9]+ FAILED|hunks? FAILED|^(error|fatal):|^ERROR|can'"'"'t find file to patch|Reversed \(or previously applied\) patch detected|patch does not apply|Failed to apply'

if grep -E "$failure" "$log" | head -10 | grep .; then
    echo "patch failures detected"
    exit 1
fi
echo "protonprep clean"
