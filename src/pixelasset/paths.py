"""Project root and asset directory layout (PIPELINE.md §16)."""

from __future__ import annotations

import os
from pathlib import Path

ASSET_DIRS = (
    "source",
    "concepts",
    "working",
    "production",
    "previews",
    "metadata",
    "review",
)


class ProjectPaths:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.config_dir = self.root / "config"
        self.schemas_dir = self.root / "schemas"
        self.assets_dir = self.root / "assets"
        self.project_config = self.config_dir / "project.yaml"
        self.style_bible = self.config_dir / "style_bible.yaml"
        self.palettes_dir = self.config_dir / "palettes"

    def palette_path(self, name: str) -> Path:
        return self.palettes_dir / f"{name}.yaml"

    def source_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "source" / asset_id

    def spec_path(self, asset_id: str) -> Path:
        return self.source_dir(asset_id) / "spec.yaml"

    def template_path(self, asset_id: str) -> Path:
        return self.source_dir(asset_id) / "template.txt"

    def concepts_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "concepts" / asset_id

    def working_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "working" / asset_id

    def production_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "production" / asset_id

    def previews_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "previews" / asset_id

    def metadata_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "metadata" / asset_id

    def review_dir(self, asset_id: str) -> Path:
        return self.assets_dir / "review" / asset_id

    def ensure_asset_dirs(self, asset_id: str) -> None:
        for name in ASSET_DIRS:
            (self.assets_dir / name / asset_id).mkdir(parents=True, exist_ok=True)
        for name in ASSET_DIRS:
            (self.assets_dir / name).mkdir(parents=True, exist_ok=True)

    def list_asset_ids(self) -> list[str]:
        source = self.assets_dir / "source"
        if not source.is_dir():
            return []
        ids = []
        for child in sorted(source.iterdir()):
            if child.is_dir() and (child / "spec.yaml").is_file():
                ids.append(child.name)
        return ids


def find_project_root(start: Path | None = None) -> Path:
    env = os.environ.get("PIXELASSET_ROOT")
    if env:
        root = Path(env).resolve()
        if (root / "config" / "project.yaml").is_file():
            return root
        raise FileNotFoundError(
            f"PIXELASSET_ROOT={root} does not contain config/project.yaml"
        )
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config" / "project.yaml").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find project root (config/project.yaml). "
        "Run from the repo or set PIXELASSET_ROOT."
    )
