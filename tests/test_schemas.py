from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from pixelasset.config import (
    SCHEMA_FILES,
    load_asset_spec,
    load_palette,
    load_project_config,
    load_schema,
    load_style_bible,
    load_yaml,
    validate_schema,
)
from pixelasset.paths import ProjectPaths

FROZEN_PALETTE_V0 = {
    "OUTLINE": [58, 47, 36],
    "SHADOW_DARK": [107, 78, 42],
    "SHADOW": [143, 107, 52],
    "BASE": [196, 160, 74],
    "LIGHT": [220, 200, 106],
    "HIGHLIGHT": [240, 230, 168],
    "ACCENT": [166, 124, 82],
}


def test_all_schema_files_present_and_loadable(repo_root: Path) -> None:
    paths = ProjectPaths(repo_root)
    for name, filename in SCHEMA_FILES.items():
        schema = load_schema(paths, name)
        assert schema["title"]
        assert (paths.schemas_dir / filename).is_file()


def test_project_style_palette_and_hay_bale_spec_validate(repo_root: Path) -> None:
    paths = ProjectPaths(repo_root)
    project = load_project_config(paths)
    style = load_style_bible(paths)
    palette = load_palette(paths, "palette_v0")
    spec = load_asset_spec(paths, "hay_bale")
    assert project["project"]["tile_unit"] == 16
    assert project["versions"]["palette"] == "palette_v0"
    assert palette["tile_unit"] == 16
    assert palette["version"] == "palette_v0"
    assert palette["roles"] == FROZEN_PALETTE_V0
    assert spec["id"] == "hay_bale"
    assert spec["construction_path"] == "C"
    assert spec["canvas"] == {"width": 24, "height": 16}
    assert spec["anchors"]["origin"] == "bottom_center"
    assert spec["anchors"]["points"]["feet"] == [12, 15]
    assert spec["animation"]["enabled"] is False
    assert style["name"] == "Hof"
    assert style["theme"] == "Soft Equine Charm"
    assert style["rendering"]["anti_aliasing"] is False
    assert style["rendering"]["texture"]["cluster_min_px"] == 2


def test_invalid_spec_rejected(repo_root: Path) -> None:
    paths = ProjectPaths(repo_root)
    spec = load_yaml(paths.spec_path("hay_bale"))
    spec.pop("canvas")
    with pytest.raises(jsonschema.ValidationError):
        validate_schema(spec, load_schema(paths, "asset"), source="test")
