#!/usr/bin/env python3
"""Generate the DSX proxy hook patch for the GE-Proton `proton` script.

The hook's single source of truth is tools/dsx_proton_hook.py. This script
splices that module into a pristine copy of `proton` and emits a unified
diff. Run it whenever the module changes:

    python3 tools/gen_proton_hook_patch.py . \
        patches/dsx-proton/0014-proton-auto-enable-dsx-proxy.patch
"""

import difflib
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "tools" / "dsx_proton_hook.py"

BEGIN = "# --- BEGIN DSX PROXY HOOK (tools/dsx_proton_hook.py) ---"
END = "# --- END DSX PROXY HOOK ---"
BODY_MARKER = "DSX_LAUNCH_CLIENT = "

# Insert the hook block immediately before this definition, which follows
# proton's own `log` function.
HOOK_ANCHOR = "def file_is_wine_builtin_dll(path):\n"

RUN_ANCHOR = "    def run(self):\n"
RUN_REPLACEMENT = (
    "    def run(self):\n"
    "        dsx_should_launch, dsx_snapshot = dsx_enable_proxy()\n"
    "        if not dsx_should_launch:\n"
    '            dsx_log("launch cancelled at user request")\n'
    "            return 1\n"
    "        try:\n"
    "            return self._dsx_run()\n"
    "        finally:\n"
    "            dsx_restore_proxy(dsx_snapshot)\n"
    "\n"
    "    def _dsx_run(self):\n"
)


def module_body():
    """The module minus its docstring and imports.

    proton already imports json, os, shutil, subprocess and sys, so the
    embedded block starts at the first constant.
    """
    source = MODULE_PATH.read_text()
    try:
        start = source.index(BODY_MARKER)
    except ValueError:
        raise SystemExit("error: %s no longer defines %s" % (MODULE_PATH, BODY_MARKER))
    return source[start:].rstrip() + "\n"


def substitute_once(text, anchor, replacement, description):
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(
            "error: expected exactly 1 occurrence of the %s anchor, found %d.\n"
            "       Upstream GE-Proton likely moved this code; update the anchor."
            % (description, count))
    return text.replace(anchor, replacement)


def build_patched(original):
    block = BEGIN + "\n" + module_body() + END + "\n\n\n"
    patched = substitute_once(original, HOOK_ANCHOR, block + HOOK_ANCHOR, "hook insertion")
    patched = substitute_once(patched, RUN_ANCHOR, RUN_REPLACEMENT, "Session.run")
    return patched


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    proton_ge_dir, output_path = argv[1], argv[2]
    original = subprocess.run(
        ["git", "-C", proton_ge_dir, "show", "HEAD:proton"],
        capture_output=True, text=True, check=True).stdout
    patched = build_patched(original)
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile="a/proton", tofile="b/proton", n=3)
    pathlib.Path(output_path).write_text("".join(diff))
    print("wrote %s" % output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
