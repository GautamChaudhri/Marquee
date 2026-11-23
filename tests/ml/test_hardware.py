from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from marquee.config.app import REPO_ROOT


def _paddlex_cache_home(data_dir: Path, **extra_env: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PADDLE_PDX_CACHE_HOME"}
    env.update(MARQUEE_ENV_FILE="", DATA_DIR=str(data_dir), **extra_env)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, marquee.ml.hardware; print(os.environ['PADDLE_PDX_CACHE_HOME'])",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def test_paddlex_cache_points_at_the_data_directory(tmp_path: Path) -> None:
    assert _paddlex_cache_home(tmp_path) == str(tmp_path / "models" / "paddlex")


def test_paddlex_cache_respects_an_explicit_setting(tmp_path: Path) -> None:
    explicit = str(tmp_path / "elsewhere")
    assert _paddlex_cache_home(tmp_path, PADDLE_PDX_CACHE_HOME=explicit) == explicit
