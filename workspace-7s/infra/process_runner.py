from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence


def run_step(cmd: Sequence[str], env: dict, workspace_root: Path) -> int:
    pretty = " ".join(cmd)
    print(f"\n{'=' * 80}\n▶ {pretty}\n{'=' * 80}")

    result = subprocess.run(
        list(cmd),
        cwd=str(workspace_root),
        env=env,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    return result.returncode
