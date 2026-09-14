"""Markings: the part of a coat a palette cannot express.

A coat is a palette right up to the point where the same source colour has two
destinations. Bay's base body #80472c is 63% white patch and 36% still brown on
paint_brown, and which one a pixel gets depends on where it is — so no
colour-keyed map expresses it at any size, and a marking needs a mask.

The mask is derived rather than authored, because the pack ships the same
geometry with and without each marking. The test that makes that trustworthy is
the same one that made palette substitution trustworthy: render bay through its
own palette plus the derived override and get the shipped marking sheet back,
with zero mismatched pixels wherever both sheets draw. If that holds, applying
the override to a *different* body is changing exactly what it claims to.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from pixelasset import ingest
from pixelasset.coat import (
    decompose,
    derive_marking,
    from_hex,
    load_sheet,
    mapping_between,
    render,
    substitute,
    substitute_marking,
    to_hex,
)
from pixelasset.config import load_coat_palette, load_marking
from pixelasset.paths import ProjectPaths

ROOT = Path(__file__).resolve().parent.parent
PACK_FILE = ROOT / "packs/full_pack.yaml"
PATHS = ProjectPaths(ROOT)
FAMILIES = ("ride", "ride_jump", "lead")
PAIRS = [(m, f) for m in ("socks", "paint") for f in FAMILIES]


def _pack():
    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip("gitignored third-party pack not present")
    return pack, root


def _palette(name: str) -> dict:
    return load_coat_palette(PATHS, name)


# Deriving a marking reads two full sheets and decomposes one of them, which is
# slow enough that repeating it per test dominated the module.
_DERIVED: dict[tuple[str, str], tuple] = {}


def _derived(marking: str, family: str):
    """(base sheet, shipped marked sheet, Marking) for one pair."""
    key = (marking, family)
    if key not in _DERIVED:
        pack, root = _pack()
        descriptor = load_marking(PATHS, marking)
        folder = root / pack["sources"][family]["dir"]
        base = load_sheet(
            folder / pack["coats"][descriptor["against"]]["files"][family])
        marked = load_sheet(
            folder / pack["coats"][descriptor["derive_from"]]["files"][family])
        _DERIVED[key] = (base, marked, derive_marking(base, marked))
    return _DERIVED[key]


# --- the acid test ---------------------------------------------------------

@pytest.mark.parametrize("marking,family", PAIRS)
def test_derived_marking_reproduces_the_shipped_sheet(marking, family):
    """Bay's geometry + bay's own palette + the derived override == the shipped
    marking sheet, on every pixel where both sheets are fully opaque.

    Zero, not "close". This is the marking analogue of rendering bay through
    coat_bay, and it is the only reason applying the override to another body
    coat can be trusted.

    Scoped to pixels opaque in both on purpose: the shipped marking bleeds a
    pixel outside bay's silhouette in a few frames, and the marking coats carry
    anti-aliased edge pixels bay does not. Those are excluded by derivation and
    counted in test_semi_alpha_is_excluded_by_construction.
    """
    base, marked, derived = _derived(marking, family)
    indexed = decompose(base)
    out = render(indexed, None, overlay=(derived.index, derived.colours))
    both = (base[:, :, 3] == 255) & (marked[:, :, 3] == 255)
    differs = np.any(out[:, :, :3] != marked[:, :, :3], axis=2)
    assert int((differs & both).sum()) == 0


@pytest.mark.parametrize("marking,family", PAIRS)
def test_a_marking_is_mostly_ink_not_reshading(marking, family):
    """The tripwire for a mis-wired `against:`.

    Classification relies on the base coat not already containing the marking's
    colours. Derived against bay this is 0-3.3% re-shading; derived against
    black, whose palette already holds the marking's greys, it collapses to
    22-32%. A single assertion catches the whole failure.
    """
    _, _, derived = _derived(marking, family)
    assert derived.pixels > 0, "a marking that changed nothing is a no-op"
    assert derived.reshade_pixels / derived.pixels < 0.05


def test_deriving_against_the_wrong_coat_collapses_the_classification():
    """Why `against` must be the warm coat, measured rather than asserted."""
    pack, root = _pack()
    folder = root / pack["sources"]["ride"]["dir"]
    black = load_sheet(folder / pack["coats"]["black"]["files"]["ride"])
    socks_black = load_sheet(
        folder / pack["coats"]["socks_black"]["files"]["ride"])
    derived = derive_marking(black, socks_black)
    assert derived.reshade_pixels / derived.pixels > 0.05, (
        "if this ever drops below the gate, the gate has stopped discriminating"
    )


# --- composition -----------------------------------------------------------

@pytest.mark.parametrize("coat", ["coat_fox", "coat_dun", "coat_black"])
def test_ink_can_never_be_remapped_by_a_body_coat(coat):
    """The property that makes composition free.

    An ink colour is by derivation one the base coat does not have, so it can
    never be a key of a mapping whose keys are the base coat's palette. That is
    why `substitute_marking` is the same one-liner as `substitute` and needs no
    tag, flag or second code path — and it is worth pinning, because it is a
    property of the artwork rather than of the code.
    """
    _, _, derived = _derived("socks", "ride")
    mapping = mapping_between(_palette("coat_bay"), _palette(coat))
    assert not (set(derived.ink) & set(mapping))
    resolved = substitute_marking(derived, mapping)
    for before, now in zip(derived.colours, resolved):
        assert now == mapping.get(before, before)
    for colour in derived.ink:
        assert resolved[derived.colours.index(colour)] == colour


def test_the_body_mapping_never_leaves_the_base_palette():
    """The invariant markings depend on, stated where it can break.

    Composition needs every mapping key to be a colour the base sheet actually
    contains — that is what guarantees ink is not a key. It holds trivially for
    the current coat format, and this says so, so a future change to that
    format cannot quietly break markings instead.
    """
    pack, root = _pack()
    folder = root / pack["sources"]["ride"]["dir"]
    base = decompose(load_sheet(folder / pack["coats"]["bay"]["files"]["ride"]))
    for coat in ("coat_fox", "coat_dun", "coat_black", "coat_white"):
        mapping = mapping_between(_palette("coat_bay"), _palette(coat))
        assert set(mapping) <= set(base.palette), coat


def test_a_marking_composes_over_an_arbitrary_body_coat():
    pack, root = _pack()
    folder = root / pack["sources"]["ride"]["dir"]
    bay = load_sheet(folder / pack["coats"]["bay"]["files"]["ride"])
    _, _, derived = _derived("socks", "ride")
    mapping = mapping_between(_palette("coat_bay"), _palette("coat_fox"))
    indexed = decompose(bay)
    out = render(
        indexed, substitute(indexed, mapping),
        overlay=(derived.index, substitute_marking(derived, mapping)),
    )
    used = {tuple(int(v) for v in c) for c in out[out[:, :, 3] == 255][:, :3]}
    assert from_hex("#eeeeee") in used, "the marking ink survives"
    assert from_hex("#b44921") in used, "the fox body is there"
    assert from_hex("#80472c") not in used, "bay's body colour is gone"
    assert from_hex("#507a00") in used, "the eye survives"
    assert from_hex("#ffd6c2") in used, "the rider survives"
    assert np.array_equal(out[:, :, 3], bay[:, :, 3]), "alpha is untouched"


# --- alpha -----------------------------------------------------------------

@pytest.mark.parametrize("marking,family", PAIRS)
def test_semi_alpha_is_excluded_by_construction(marking, family):
    """A marked rendered coat carries bay's alpha exactly.

    Bay has no semi-transparent pixels at all, and the marking coats' anti-
    aliased edge pixels are not opaque in the marking sheet, so they can never
    enter the mask. §5's no-anti-aliasing rule therefore comes out better
    satisfied by the rendered marked coat than by the shipped one.
    """
    from pixelasset import detect

    base, marked, derived = _derived(marking, family)
    out = render(decompose(base), None, overlay=(derived.index, derived.colours))
    assert np.array_equal(out[:, :, 3], base[:, :, 3])
    assert detect.semi_transparent(base)[0] == 0
    assert detect.semi_transparent(out) == (0, [])
    marked_semi = detect.semi_transparent(marked)[0]
    overridden = derived.index > 0
    assert not (overridden & (marked[:, :, 3] != 255)).any(), (
        f"{marked_semi} semi-alpha px in the shipped sheet, none in the mask"
    )


# --- the overlay contract --------------------------------------------------

def test_an_empty_overlay_is_byte_identical_to_no_overlay():
    """`overlay=None` must not be a special case that happens to work."""
    base, _, _ = _derived("socks", "ride")
    indexed = decompose(base)
    plain = render(indexed)
    zeros = np.zeros(indexed.shape, dtype=np.uint16)
    assert np.array_equal(plain, render(indexed, None, overlay=(zeros, [])))
    assert np.array_equal(plain, base), "and the round trip still holds"


def test_overlay_shape_mismatch_is_a_clear_error():
    base, _, _ = _derived("socks", "ride")
    indexed = decompose(base)
    wrong = np.zeros((4, 4), dtype=np.uint16)
    with pytest.raises(ValueError, match="overlay is "):
        render(indexed, None, overlay=(wrong, []))


def test_overlay_cannot_index_past_its_colours():
    base, _, _ = _derived("socks", "ride")
    indexed = decompose(base)
    index = np.zeros(indexed.shape, dtype=np.uint16)
    index[0, 0] = 3
    with pytest.raises(ValueError, match="indexes up to"):
        render(indexed, None, overlay=(index, [(1, 2, 3)]))


def test_an_overlay_cannot_resurrect_a_transparent_pixel():
    """Overrides are clipped to pixels the sheet already draws, so a mask
    derived from other artwork cannot add to the silhouette."""
    base, _, _ = _derived("socks", "ride")
    indexed = decompose(base)
    index = np.ones(indexed.shape, dtype=np.uint16)   # override everything
    out = render(indexed, None, overlay=(index, [(255, 0, 255)]))
    transparent = base[:, :, 3] == 0
    assert np.array_equal(out[:, :, 3], base[:, :, 3])
    assert not (out[:, :, :3][transparent] == (255, 0, 255)).all(axis=-1).any()


# --- the descriptor is a record and a tripwire -----------------------------

@pytest.mark.parametrize("marking,family", PAIRS)
def test_descriptor_matches_what_the_derivation_finds(marking, family):
    """The §19 record, enforced. Nothing here is read to build the mask, so
    without this the file could drift from the artwork unnoticed."""
    import hashlib

    pack, root = _pack()
    descriptor = load_marking(PATHS, marking)
    recorded = descriptor["sources"][family]
    _, _, derived = _derived(marking, family)

    assert recorded["override_pixels"] == derived.pixels
    assert recorded["ink_pixels"] == derived.ink_pixels
    assert recorded["reshade_pixels"] == derived.reshade_pixels
    assert recorded["ink"] == [to_hex(c) for c in derived.ink_ladder()]

    folder = root / pack["sources"][family]["dir"]
    for key, coat in (("base", descriptor["against"]),
                      ("marking", descriptor["derive_from"])):
        path = folder / pack["coats"][coat]["files"][family]
        assert recorded[f"{key}_sheet"] == path.name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert recorded[f"{key}_sha256"] == digest


def test_paint_and_socks_share_their_ink_ladder():
    """Evidence that ink is a property of the marking, not of the body: two
    different markings, drawn independently, reach for the same greys."""
    socks = set(load_marking(PATHS, "socks")["sources"]["ride"]["ink"])
    paint = set(load_marking(PATHS, "paint")["sources"]["ride"]["ink"])
    shared = socks & paint
    assert {"#eeeeee", "#aeaeae", "#6a6a6a", "#262626", "#151515"} <= shared


def test_every_declared_marking_has_a_descriptor():
    pack, _ = _pack()
    for name, spec in (pack.get("markings") or {}).items():
        load_marking(PATHS, spec.get("descriptor", name))


# --- through the pipeline --------------------------------------------------

def test_rendered_marked_coat_reproduces_the_shipped_one():
    """End-to-end, through ingest rather than on raw sheets: bay's palette plus
    the socks override is the pack's socks_brown.

    The alpha difference is the documented cost — in a few frames the shipped
    marking bleeds a pixel outside bay's silhouette — and it is asserted as a
    small number rather than zero so a regression that widened it would fail.
    """
    pack, root = _pack()
    shipped = ingest.build_asset(pack, root, "socks_brown", "ride")
    spec = pack["rendered_coats"]["bay_socks"]
    rendered = ingest.build_asset(
        pack, root, spec["from"], "ride",
        recolour=ingest._recolour_for(PATHS, spec),
        marking=ingest._marking_for(pack, root, PATHS, spec),
    )
    a = np.array(ingest.render_sheet(shipped).convert("RGBA"))
    b = np.array(ingest.render_sheet(rendered).convert("RGBA"))
    assert a.shape == b.shape
    both = (a[:, :, 3] == 255) & (b[:, :, 3] == 255)
    assert int((np.any(a != b, axis=2) & both).sum()) == 0
    assert int((a[:, :, 3] != b[:, :, 3]).sum()) < 200


def test_ingest_rejects_a_marking_the_pack_does_not_declare():
    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    pack["rendered_coats"] = {
        "bogus": {"label": "B", "from": "bay", "base_palette": "coat_bay",
                  "palette": "coat_fox", "marking": "stripes"}
    }
    path = ROOT / "packs" / "_marking_unknown_test.yaml"
    path.write_text(yaml.safe_dump(pack), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="does not declare"):
            ingest.ingest_pack(path, PATHS, only=["nothing"])
    finally:
        path.unlink()


def test_ingest_rejects_a_marking_applied_to_the_wrong_base():
    """`against` and `from` must agree, or the classification is meaningless."""
    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    pack["rendered_coats"] = {
        "wrong": {"label": "W", "from": "black", "base_palette": "coat_bay",
                  "palette": "coat_fox", "marking": "socks"}
    }
    path = ROOT / "packs" / "_marking_base_test.yaml"
    path.write_text(yaml.safe_dump(pack), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="derived against"):
            ingest.ingest_pack(path, PATHS, only=["nothing"])
    finally:
        path.unlink()
