from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "schemas", tmp_path / "schemas")
    shutil.copytree(REPO_ROOT / "assets" / "source", tmp_path / "assets" / "source")
    for name in (
        "concepts",
        "working",
        "production",
        "previews",
        "metadata",
        "review",
    ):
        (tmp_path / "assets" / name).mkdir(parents=True, exist_ok=True)
    return tmp_path
