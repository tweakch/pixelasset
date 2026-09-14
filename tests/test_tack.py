"""Tack: the second axis of a mode.

A coat used to come in two modes — riding and on foot. It now comes in four,
because the pack draws each of those twice, once with the horse bare and once
with it saddled, on the same grid and the same rows. So `mode` says how the
subject is configured and `tack` says what it is wearing, and a consumer can
offer a saddle toggle without parsing asset ids for a suffix.

Two claims carry the whole feature and both are measured here rather than
assumed: that a saddled mode is the bareback mode drawn on other sheets (same
clips, same frames, same jump windows), and that the saddle survives a coat
swap — it is drawn outside bay's key space, so palette substitution cannot
touch it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from pixelasset import detect, ingest
from pixelasset.coat import (
    derive_marking,
    from_hex,
    load_sheet,
    mapping_between,
    to_hex,
)
from pixelasset.config import load_coat_palette
from pixelasset.paths import ProjectPaths

ROOT = Path(__file__).resolve().parent.parent
PATHS = ProjectPaths(ROOT)
PACK_FILE = ROOT / "packs/full_pack.yaml"

SADDLED_MODES = {"ride_saddled": "ride", "lead_saddled": "lead",
                 "loose_saddled": "loose"}

# Rider skin, measured in tests/test_coat_render.py as colours outside bay's key
# space that survive a coat swap. A riderless sheet must not contain them.
RIDER_SKIN = ("#ffd6c2", "#e2a182")


# --- mode inheritance, no artwork needed -----------------------------------
#
# `like` exists so a tack variant is not a second copy of a thirty-four line
# clip block. Everything it can get wrong is a silent duplicate or a clip
# reading the wrong sheet, so each is an error by name.

def _modes(**extra):
    base = {
        "ride": {
            "label": "Riding",
            "tack": "bareback",
            "clips": {
                "east": {"walk": {"src": "ride", "row": 2, "fps": 9},
                         "jump": {"src": "ride_jump", "row": 0, "fps": 14}},
            },
        },
    }
    base.update(extra)
    return {"modes": base}


def test_an_inheriting_mode_copies_the_clips_and_remaps_the_sheets():
    pack = ingest.resolve_modes(_modes(ride_saddled={
        "label": "Riding, saddled", "tack": "saddled", "like": "ride",
        "sheets": {"ride": "ride_saddled", "ride_jump": "ride_saddled_jump"},
    }))
    bare = pack["modes"]["ride"]["clips"]["east"]
    saddled = pack["modes"]["ride_saddled"]["clips"]["east"]

    assert saddled.keys() == bare.keys()
    for action in bare:
        assert {k: v for k, v in saddled[action].items() if k != "src"} == \
               {k: v for k, v in bare[action].items() if k != "src"}
    assert saddled["walk"]["src"] == "ride_saddled"
    assert saddled["jump"]["src"] == "ride_saddled_jump"
    # The parent is not mutated by its own child's remap.
    assert bare["walk"]["src"] == "ride"


def test_an_inheriting_mode_reports_its_parents_configuration():
    """`ride_saddled` is still riding. The id separates the two builds; `mode`
    is what a consumer keys movement and gaits on, and it must not split."""
    pack = ingest.resolve_modes(_modes(ride_saddled={
        "tack": "saddled", "like": "ride", "sheets": {"ride": "ride_saddled"},
    }))
    assert pack["modes"]["ride_saddled"]["mode"] == "ride"
    assert pack["modes"]["ride"]["mode"] == "ride"
    # No label of its own, so it borrows one rather than crashing on lookup.
    assert pack["modes"]["ride_saddled"]["label"] == "Riding"


def test_inheriting_from_an_undeclared_mode_is_an_error():
    with pytest.raises(ValueError, match="not a declared mode"):
        ingest.resolve_modes(_modes(ride_saddled={
            "like": "rdie", "sheets": {"ride": "ride_saddled"}}))


def test_a_chain_of_inheritance_is_refused():
    with pytest.raises(ValueError, match="One level only"):
        ingest.resolve_modes(_modes(
            ride_saddled={"like": "ride", "sheets": {"ride": "ride_saddled"}},
            ride_saddled_again={"like": "ride_saddled",
                                "sheets": {"ride_saddled": "x"}},
        ))


def test_inheriting_without_remapping_anything_is_an_error():
    """It would build byte-identical artwork under a second asset id, which is
    indistinguishable from a real variant until someone looks at the pixels."""
    with pytest.raises(ValueError, match="remaps no sheets"):
        ingest.resolve_modes(_modes(ride_saddled={"like": "ride"}))


def test_remapping_a_sheet_the_parent_does_not_draw_from_is_an_error():
    """A typo here is the quiet failure: the remap matches nothing, every clip
    keeps the parent's sheet and the variant is a duplicate."""
    with pytest.raises(ValueError, match="does not draw from"):
        ingest.resolve_modes(_modes(ride_saddled={
            "like": "ride", "sheets": {"lead": "lead_saddled"}}))


# --- the real pack ---------------------------------------------------------

def _pack():
    pack = ingest.load_pack(PACK_FILE)
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip("gitignored third-party pack not present")
    return pack, root


def test_every_coat_declares_a_file_for_every_source():
    """`_marking_for` derives a mask for every declared source regardless of
    which mode wants it, so one missing filename fails a coat that never asks
    for the sheet."""
    pack, root = _pack()
    for coat_id, coat in pack["coats"].items():
        for src, spec in pack["sources"].items():
            assert src in coat["files"], f"{coat_id} has no {src} sheet"
            path = root / spec["dir"] / coat["files"][src]
            assert path.is_file(), f"{coat_id}/{src}: {path}"


@pytest.mark.parametrize("saddled,bare", sorted(SADDLED_MODES.items()))
def test_a_saddled_mode_declares_the_same_clips_as_its_bareback_twin(saddled, bare):
    pack, _ = _pack()
    a, b = pack["modes"][saddled]["clips"], pack["modes"][bare]["clips"]
    assert a.keys() == b.keys()
    for facing in b:
        assert a[facing].keys() == b[facing].keys()
        for action in b[facing]:
            for key in ("row", "fps", "loop", "first", "frames"):
                assert a[facing][action].get(key) == b[facing][action].get(key), \
                    f"{saddled}/{facing}/{action}: {key} drifted"


_BUILDS: dict[tuple[str, str, str], np.ndarray] = {}


def _built(coat: str, mode: str, palette: str | None = None) -> np.ndarray:
    key = (coat, mode, palette or "")
    if key not in _BUILDS:
        pack, root = _pack()
        recolour = None
        if palette:
            recolour = mapping_between(load_coat_palette(PATHS, "coat_bay"),
                                       load_coat_palette(PATHS, palette))
        asset = ingest.build_asset(pack, root, coat, mode, recolour=recolour)
        _BUILDS[key] = np.array(ingest.render_sheet(asset).convert("RGBA"))
    return _BUILDS[key]


def _saddle_colours():
    """What the brown saddle is drawn in: the override that turns the bareback
    riding sheet into the saddled one."""
    pack, root = _pack()
    bay = pack["coats"]["bay"]["files"]
    bare = load_sheet(root / pack["sources"]["ride"]["dir"] / bay["ride"])
    sad = load_sheet(
        root / pack["sources"]["ride_saddled"]["dir"] / bay["ride_saddled"])
    return derive_marking(bare, sad)


def test_the_brown_saddle_is_drawn_outside_bays_key_space():
    """The reason the saddled sources name the brown saddle and not the black
    one. A rendered coat substitutes bay's thirteen colours and passes the rest
    through, so tack drawn outside that key space survives any coat by
    construction — no mask, no marking, nothing to maintain.

    The black saddle does not have that property, and the second half of this
    test is the tripwire for switching to it: it draws thousands of pixels in
    bay's own #000000, so on fox or dun it would be recoloured with the horse.
    """
    keys = {from_hex(r["from"])
            for r in load_coat_palette(PATHS, "coat_bay")["map"]}
    marking = _saddle_colours()
    inside = sum(n for c, n in zip(marking.colours, marking.counts) if c in keys)
    assert marking.pixels > 10000, "the saddle should be substantial artwork"
    assert inside < 100, (
        f"{inside} px of the brown saddle fall inside bay's key space; a coat "
        f"swap would recolour them"
    )

    pack, root = _pack()
    black = (root / pack["sources"]["ride_saddled"]["dir"]
             / "Horse_fullcolor_brown_blacksaddled_riding.png")
    if not black.is_file():
        pytest.skip("black saddle sheet not present")
    bare = load_sheet(root / pack["sources"]["ride"]["dir"]
                      / pack["coats"]["bay"]["files"]["ride"])
    other = derive_marking(bare, load_sheet(black))
    inside_black = sum(n for c, n in zip(other.colours, other.counts)
                       if c in keys)
    assert inside_black > 1000, (
        "the black saddle is expected to collide with bay's dark end; if it no "
        "longer does, the argument for pinning the saddled sources to the "
        "brown saddle has changed"
    )


def test_a_rendered_coat_leaves_the_saddle_alone():
    """The claim the choice of saddle buys: fox changes the horse and nothing
    else. Every colour the saddle is drawn in is still on the sheet, and bay's
    body colour is gone from it."""
    keys = {from_hex(r["from"])
            for r in load_coat_palette(PATHS, "coat_bay")["map"]}
    leather = [c for c in _saddle_colours().colours if c not in keys]
    assert len(leather) >= 8, "expected a saddle with its own ramp"

    sheet = _built("bay", "ride_saddled", "coat_fox")
    used = {tuple(int(v) for v in c) for c in sheet[sheet[:, :, 3] == 255][:, :3]}
    missing = sorted(to_hex(c) for c in leather if c not in used)
    assert missing == [], f"the coat swap ate saddle colours: {missing}"
    assert from_hex("#80472c") not in used, "bay's body colour should be gone"


@pytest.mark.parametrize("saddled,bare", sorted(SADDLED_MODES.items()))
def test_a_saddled_sheet_is_not_its_bareback_twin(saddled, bare):
    """A tack variant that rendered identically would be a silent duplicate,
    which is exactly what the `sheets` remap checks exist to prevent."""
    assert not np.array_equal(_built("bay", bare), _built("bay", saddled))


def test_saddling_introduces_no_new_disagreement_with_the_checked_in_coats():
    """The pack is not self-consistent across its own sheet families, and the
    known disagreements are recorded in tests/test_coat_render.py. What matters
    here is that the saddled families add none of their own: the colours where a
    shipped sheet argues with its coat file are the same set with tack on.
    """
    from pixelasset.coat import correspond

    pack, root = _pack()

    def disagreeing(coat_name: str, families: tuple[str, ...]) -> set[str]:
        expected = {from_hex(r["from"]): from_hex(r["to"])
                    for r in load_coat_palette(PATHS, f"coat_{coat_name}")["map"]}
        out = set()
        for fam in families:
            folder = root / pack["sources"][fam]["dir"]
            rows, _ = correspond(
                load_sheet(folder / pack["coats"]["bay"]["files"][fam]),
                load_sheet(folder / pack["coats"][coat_name]["files"][fam]),
            )
            out |= {to_hex(r.source) for r in rows
                    if expected.get(r.source) not in (None, r.dominant)}
        return out

    for coat_name in ("black", "white"):
        assert disagreeing(coat_name, ("ride_saddled", "ride_saddled_jump",
                                       "lead_saddled")) == \
               disagreeing(coat_name, ("ride", "ride_jump", "lead"))


# --- what the metadata has to carry ----------------------------------------

def test_metadata_and_manifest_carry_the_tack():
    """Without it a consumer keying on `mode` alone keeps whichever of the two
    assets it saw last, and one of them silently disappears."""
    from pixelasset.manifest import entry_for

    pack, root = _pack()
    built = {}
    for mode in ("ride", "ride_saddled"):
        asset = ingest.build_asset(pack, root, "bay", mode)
        ingest.render_sheet(asset)          # assigns row indices
        built[mode] = ingest.metadata(asset)

    assert built["ride"]["mode"] == built["ride_saddled"]["mode"] == "ride"
    assert built["ride"]["tack"] == "bareback"
    assert built["ride_saddled"]["tack"] == "saddled"
    assert built["ride"]["id"] != built["ride_saddled"]["id"]
    assert entry_for(built["ride_saddled"])["tack"] == "saddled"


def test_the_metadata_schema_accepts_the_tack():
    import jsonschema

    from pixelasset.config import load_schema

    schema = load_schema(PATHS, "metadata")
    props = schema["properties"]
    assert "tack" in props, "the schema is the interface; tack has to be in it"

    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    declared = {m.get("tack") for m in pack["modes"].values()}
    # `none` is a value, not a gap: a loose horse demonstrably wears nothing,
    # which is different from a family that has no tack axis at all and omits
    # the field.
    assert declared == {"bareback", "halter", "saddled", "none"}
    for tack in declared:
        jsonschema.validate(tack, props["tack"])


# --- the loose horse -------------------------------------------------------
#
# `loose` is the other thing `like:` buys. A tack variant inherits `mode`
# because it is the same configuration on different sheets; a horse with nobody
# on it is a different configuration and declares its own. Both are one keyword.

def test_loose_declares_its_own_configuration_rather_than_inheriting_ride():
    pack, _ = _pack()
    assert pack["modes"]["loose"]["mode"] == "loose"
    assert pack["modes"]["loose"]["like"] == "ride"
    # The tack variants go the other way, and that contrast is the point.
    assert pack["modes"]["ride_saddled"]["mode"] == "ride"


def test_the_saddled_loose_mode_does_not_chain_its_inheritance():
    """Inheritance is one level, so the saddled loose horse borrows `ride`'s
    rows directly rather than borrowing `loose`'s borrowing of them. Saying it
    twice is the price of refusing to chain."""
    pack, _ = _pack()
    saddled = pack["modes"]["loose_saddled"]
    assert saddled["like"] == "ride", "must not be `like: loose`"
    assert saddled["mode"] == "loose" and saddled["tack"] == "saddled"
    with pytest.raises(ValueError, match="One level only"):
        ingest.resolve_modes({
            "modes": {
                "ride": {"label": "R", "clips": {"east": {
                    "walk": {"src": "ride", "row": 0}}}},
                "loose": {"like": "ride", "sheets": {"ride": "loose"}},
                "chained": {"like": "loose", "sheets": {"loose": "x"}},
            }
        })


def test_every_mode_that_can_be_saddled_is():
    """The game's saddle toggle asks whether a coat has a saddled sheet for
    every mode it has. One mode without one turns the toggle off entirely,
    which is how the loose horse silently broke it."""
    pack, _ = _pack()
    by_mode = {}
    for name, mode in pack["modes"].items():
        by_mode.setdefault(mode["mode"], set()).add(mode.get("tack"))
    assert set(by_mode) == {"ride", "lead", "loose"}
    for mode, tacks in by_mode.items():
        assert "saddled" in tacks, f"{mode} has no saddled variant"
        assert len(tacks) == 2, f"{mode} should be exactly bare and saddled"


def test_the_riderless_sheet_carries_the_same_rows_as_the_ridden_one():
    """Why `loose` inherits `ride`'s clip block instead of repeating it. Every
    row holds the same action; only the three idles differ, where the ridden
    version has extra frames because the rider shifts in the saddle."""
    pack, root = _pack()

    def counts(src):
        path = (root / pack["sources"][src]["dir"]
                / pack["coats"]["bay"]["files"][src])
        a = detect.alpha(load_sheet(path))
        cw, ch = detect.detect_cell(a)
        return [len(detect.row_frames(a, cw, ch, r))
                for r in range(a.shape[0] // ch)]

    ridden, alone = counts("ride"), counts("loose")
    assert len(ridden) == len(alone) == 18
    idle_rows = {0, 5, 10, 14}          # the rows `ride` maps to idle
    for row, (r, l) in enumerate(zip(ridden, alone)):
        if row in idle_rows:
            assert l <= r, f"row {row}: riderless should not gain frames"
        else:
            assert r == l, f"row {row}: {r} ridden vs {l} riderless"
    assert ridden != alone, "the two sheets should not be identical"


def test_the_loose_horse_has_no_rider_on_it():
    """The claim the mode makes, checked against the pixels rather than the
    filename: none of the rider's skin colours appear anywhere on the sheet."""
    sheet = _built("bay", "loose")
    used = {to_hex(tuple(int(v) for v in c))
            for c in sheet[sheet[:, :, 3] == 255][:, :3]}
    assert not (set(RIDER_SKIN) & used), \
        f"a rider is still on the loose sheet: {sorted(set(RIDER_SKIN) & used)}"
    # The same colours are present on the ridden sheet, so this is a real test.
    ridden = _built("bay", "ride")
    ridden_used = {to_hex(tuple(int(v) for v in c))
                   for c in ridden[ridden[:, :, 3] == 255][:, :3]}
    assert set(RIDER_SKIN) <= ridden_used


def test_the_loose_horse_keeps_all_four_facings_and_its_gaits():
    pack, root = _pack()
    asset = ingest.build_asset(pack, root, "bay", "loose")
    have = {(c.facing, c.action) for c in asset.clips}
    for facing in ("east", "west", "south", "north"):
        for action in ("idle", "walk", "trot", "gallop", "jump"):
            assert (facing, action) in have, f"{facing}/{action}"
    assert ("east", "graze") in have and ("north", "graze") not in have


def test_a_loose_coat_renders_like_any_other():
    """A rendered coat is the same code path on this mode too — no rider means
    nothing to pass through except the eye, so this is the strictest version of
    the substitution claim."""
    direct = _built("bay", "loose")
    through = _built("bay", "loose", "coat_bay")
    assert np.array_equal(direct, through)
    fox = _built("bay", "loose", "coat_fox")
    assert not np.array_equal(direct, fox)
    assert np.array_equal(direct[:, :, 3], fox[:, :, 3])
