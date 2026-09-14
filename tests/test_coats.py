"""Coat variants: one geometry, several colour ramps.

A coat changes what the palette roles resolve to and nothing else. These tests
pin that claim from both ends — the colours really do change, and the geometry
really does not, because the second half is the one that rots silently.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from pixelasset.config import load_palette
from pixelasset.paths import ProjectPaths

ROOT = Path(__file__).resolve().parent.parent
PATHS = ProjectPaths(ROOT)
PALETTE = yaml.safe_load((ROOT / "config/palettes/palette_v1.yaml").read_text())


def _rgb_set(roles):
    return {tuple(v) for v in roles.values()}


def test_palette_declares_both_coats():
    assert set(PALETTE["coats"]) >= {"bay", "fox"}


def test_default_roles_still_resolve_to_bay():
    """`roles` stays the bay ramp, so every existing asset is unaffected."""
    assert load_palette(PATHS, "palette_v1")["roles"] == PALETTE["coats"]["bay"]


def test_named_coat_replaces_roles():
    fox = load_palette(PATHS, "palette_v1", "fox")
    assert fox["roles"] == PALETTE["coats"]["fox"]
    assert fox["coat"] == "fox"


def test_unknown_coat_is_a_clear_error():
    with pytest.raises(ValueError, match="no coat 'zebra'"):
        load_palette(PATHS, "palette_v1", "zebra")


def test_every_coat_defines_every_role():
    """A ramp missing a role would silently fall back to nothing and crash the
    renderer mid-build rather than at config load."""
    roles = set(PALETTE["roles"])
    for name, ramp in PALETTE["coats"].items():
        assert set(ramp) == roles, f"coat {name} role mismatch"


def test_fox_has_no_black_points():
    """SHADOW_DARK is the mane, tail and lower legs. A bay has black points and
    a fox has none — that single role is the whole difference between them, so
    a fox whose points match bay's is not a fox."""
    bay = PALETTE["coats"]["bay"]["SHADOW_DARK"]
    fox = PALETTE["coats"]["fox"]["SHADOW_DARK"]
    assert fox != bay
    def lum(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    assert lum(fox) > lum(bay) + 8, "fox points must read chestnut, not near-black"
    assert fox[0] > fox[2] + 40, "fox points must be warm/red, not neutral"


def test_fox_spec_shares_the_bay_template():
    """Copying the template per coat lets variants drift, which §23 forbids."""
    spec = yaml.safe_load((ROOT / "assets/source/horse_fox/spec.yaml").read_text())
    assert spec["template_from"] == "horse_bay"
    assert spec["palette"]["coat"] == "fox"
    assert not (ROOT / "assets/source/horse_fox/template.txt").exists()


# --- built output ----------------------------------------------------------

def _sheet(asset_id: str) -> np.ndarray:
    path = ROOT / f"assets/production/{asset_id}/spritesheet.png"
    if not path.is_file():
        pytest.skip(f"{asset_id} not built; run `pixelasset build {asset_id}`")
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def _opaque_colours(sheet: np.ndarray) -> set[tuple[int, int, int]]:
    return {tuple(int(v) for v in c) for c in sheet[sheet[:, :, 3] == 255][:, :3]}


def test_coats_are_pixel_identical_in_shape():
    """The §23 guarantee, enforced rather than asserted: two coats of one asset
    differ in colour and are identical in silhouette, because they are rendered
    from the same template."""
    bay, fox = _sheet("horse_bay"), _sheet("horse_fox")
    assert bay.shape == fox.shape
    assert np.array_equal(bay[:, :, 3], fox[:, :, 3])


def test_built_fox_uses_only_the_fox_ramp():
    used = _opaque_colours(_sheet("horse_fox"))
    allowed = _rgb_set(PALETTE["coats"]["fox"])
    assert used <= allowed, f"colours outside the fox ramp: {used - allowed}"


def test_built_fox_contains_no_bay_colour():
    """Proves the ramp swap actually happened rather than the default leaking
    through. The two ramps share no RGB, so any overlap is a real failure."""
    used = _opaque_colours(_sheet("horse_fox"))
    bay_only = _rgb_set(PALETTE["coats"]["bay"]) - _rgb_set(PALETTE["coats"]["fox"])
    assert not (used & bay_only)
