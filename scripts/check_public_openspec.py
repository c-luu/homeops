from __future__ import annotations

import subprocess
import sys

ALLOWED = {
    "openspec/.gitkeep",
    "openspec/specs/.gitkeep",
    "openspec/changes/.gitkeep",
    "openspec/changes/archive/.gitkeep",
}

tracked = set(
    subprocess.check_output(["git", "ls-files", "openspec/"], text=True).splitlines()
)
unexpected = sorted(tracked - ALLOWED)
if unexpected:
    print("Private or unexpected OpenSpec files are tracked:", file=sys.stderr)
    print("\n".join(unexpected), file=sys.stderr)
    raise SystemExit(1)
