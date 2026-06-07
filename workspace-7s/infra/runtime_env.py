from __future__ import annotations

import os
from pathlib import Path

from infra.runtime_paths import DISABLED_MODES


def build_env(manifest_path: str | None, manifest_mode: str, runtime_root: Path | None = None) -> dict:
    env = os.environ.copy()
    mode = (manifest_mode or "disabled").strip().lower()

    if mode in DISABLED_MODES:
        env.pop("SEVENS_TEMP_ASSET_MODE", None)
        env.pop("SEVENS_TEMP_ASSET_MANIFEST", None)
        env.pop("SEVENS_RUNTIME_ROOT", None)
        return env

    env["SEVENS_TEMP_ASSET_MODE"] = mode
    if manifest_path:
        env["SEVENS_TEMP_ASSET_MANIFEST"] = str(Path(manifest_path).resolve())
    if runtime_root is not None:
        runtime_root.mkdir(parents=True, exist_ok=True)
        env["SEVENS_RUNTIME_ROOT"] = str(runtime_root.resolve())
    return env
