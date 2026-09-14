"""Rendering a coat by palette substitution.

The premise: a coat is a palette, and running the pipeline with a different one
renders every sheet in that coat. The test that makes it trustworthy is the
round trip — extract a coat's own palette, render through it, and get the
source back byte for byte. If that holds, a substituted palette is changing
exactly what it claims to and nothing else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import yaml

from pixelasset import ingest
from pixelasset.coat import (
    coat_ramp,
    decompose,
    from_hex,
    load_sheet,
    mapping_between,
    render,
    substitute,
)

ROOT = Path(__file__).resolve().parent.parent
PACK_FILE = ROOT / "packs/full_pack.yaml"
HORSES = ROOT / "samples/Full_Pack/Horse_Sprite_Asset/Horses_no_equipment"

SHEETS = {
    "horse": "Horse_Sprite_Asset/Horses_no_equipment/Horse_fullcolor_brown.png",
    "ride": "Horse_Sprite_Riding_Asset/Horses_barebackriding/Horse_fullcolor_brown_barebackriding.png",
    "lead": "Horse_Sprite_Leading_Asset/Horses_halter/Horse_fullcolor_brown_leading_blacknhalter.png",
    "jump": "Horse_Sprite_Jump_Asset/Horse_and_Rider/Horses_barebackriding/Horse_fullcolor_brown_barebackriding_jump.png",
    "semi_alpha": "Horse_Sprite_Asset/Horses_no_equipment/Horse_socks_black.png",
}


def _pack():
    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip("gitignored third-party pack not present")
    return pack, root


def _sheet(rel: str) -> np.ndarray:
    path = ROOT / "samples/Full_Pack" / rel
    if not path.is_file():
        pytest.skip("gitignored third-party pack not present")
    return load_sheet(path)


def _palette(name: str) -> dict:
    return yaml.safe_load(
        (ROOT / f"config/palettes/{name}.yaml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("kind", sorted(SHEETS))
def test_round_trip_is_byte_identical(kind):
    src = _sheet(SHEETS[kind])
    assert np.array_equal(src, render(decompose(src)))


def test_colour_under_transparent_pixels_is_preserved():
    """The pack's PNGs carry RGB beneath alpha=0 — an exporter erased alpha
    without clearing colour. Invisible, and still part of the bytes, so
    dropping it would make 'byte-identical' mean 'identical where you look'."""
    src = _sheet(SHEETS["horse"])
    indexed = decompose(src)
    assert indexed.hidden_palette, "expected hidden colours in this pack"
    hidden = src[src[:, :, 3] == 0][:, :3]
    assert len({tuple(int(v) for v in c) for c in hidden}) > 1
    assert np.array_equal(src, render(indexed))


def test_invariants_are_derived_not_guessed():
    """A colour every coat shares cannot be what distinguishes them. For this
    pack that finds the eye and nothing else."""
    names = ["Horse_fullcolor_brown.png", "Horse_fullcolor_black.png",
             "Horse_fullcolor_White.png", "Horse_paint_brown.png",
             "Horse_socks_brown.png"]
    if not HORSES.is_dir():
        pytest.skip("pack not present")
    pals = [decompose(load_sheet(HORSES / n)).palette for n in names]
    ramp, invariant = coat_ramp(pals)
    assert set(invariant) == {from_hex(h) for h in
                              ("#ffffff", "#507a00", "#334d00", "#0b0b0b")}
    assert len(ramp) == 9


def test_extracted_bay_palette_matches_the_art():
    bay = _palette("coat_bay")
    ramp = {from_hex(h) for h in bay["ramp"]} | {from_hex(h) for h in bay["invariant"]}
    assert set(decompose(_sheet(SHEETS["horse"])).palette) == ramp


def test_substitute_leaves_unmapped_colours_alone():
    """Why the eye and the rider survive: they are absent from the mapping, not
    excluded by a rule someone has to remember."""
    indexed = decompose(_sheet(SHEETS["ride"]))
    mapping = mapping_between(_palette("coat_bay"), _palette("coat_fox"))
    after = substitute(indexed, mapping)
    for before, now in zip(indexed.palette, after):
        assert now == mapping.get(before, before)
    untouched = [c for c in indexed.palette if c not in mapping]
    assert untouched, "the ride sheet has rider and eye colours outside the ramp"


def test_mapping_rejects_a_ramp_length_mismatch():
    bay = _palette("coat_bay")
    short = {"ramp": bay["ramp"][:-1]}
    with pytest.raises(ValueError, match="ramp length mismatch"):
        mapping_between(bay, short)


# --- through the pipeline --------------------------------------------------

# Building an asset re-detects grids over full sheets, which is slow enough
# that repeating it per test dominated the suite. Each distinct build is done
# once and shared.
_BUILDS: dict[tuple[str, str, str], object] = {}


def _built(coat: str, mode: str, recolour_name: str | None = None):
    key = (coat, mode, recolour_name or "")
    if key not in _BUILDS:
        pack, root = _pack()
        recolour = None
        if recolour_name:
            recolour = mapping_between(_palette("coat_bay"), _palette(recolour_name))
        asset = ingest.build_asset(pack, root, coat, mode, recolour=recolour)
        _BUILDS[key] = (asset, np.array(ingest.render_sheet(asset).convert("RGBA")))
    return _BUILDS[key]


def _sheet_hash(pair) -> str:
    return hashlib.sha256(pair[1].tobytes()).hexdigest()


@pytest.mark.parametrize("mode", ["ride", "lead"])
def test_rendering_bay_through_its_own_palette_reproduces_bay(mode):
    """The acid test. Extract bay's palette, run the pipeline through it, and
    the output is byte-identical to building bay directly."""
    direct = _sheet_hash(_built("bay", mode))
    through = _sheet_hash(_built("bay", mode, "coat_bay"))
    assert direct == through


def test_fox_actually_differs_from_bay():
    assert _sheet_hash(_built("bay", "ride")) != _sheet_hash(
        _built("bay", "ride", "coat_fox")
    ), "a coat that changed nothing would be a silent no-op"


def test_rendered_coat_keeps_the_rider_and_the_eye():
    """The whole point of separating ramp from invariant."""
    _, sheet = _built("bay", "ride", "coat_fox")
    rgb = {tuple(int(v) for v in c) for c in sheet[sheet[:, :, 3] == 255][:, :3]}
    for keep in ("#ffd6c2", "#e2a182", "#507a00"):   # rider skin, rider skin, eye
        assert from_hex(keep) in rgb, f"{keep} should survive a coat swap"
    assert from_hex("#80472c") not in rgb, "bay's base body colour should be gone"


def test_rendered_coat_is_geometrically_identical_to_its_source():
    _, bay = _built("bay", "ride")
    _, fox = _built("bay", "ride", "coat_fox")
    assert np.array_equal(bay[:, :, 3], fox[:, :, 3])
