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
    AmbiguousCoat,
    correspond,
    decompose,
    extract_coat,
    from_hex,
    load_sheet,
    mapping_between,
    mapping_for,
    render,
    substitute,
    to_hex,
    verify_source,
)
from pixelasset.config import load_coat_palette
from pixelasset.paths import ProjectPaths

ROOT = Path(__file__).resolve().parent.parent
PATHS = ProjectPaths(ROOT)
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
    """Through the validating loader, so every test here exercises the schema,
    the key-distinctness check and role resolution rather than the happy path."""
    return load_coat_palette(PATHS, name)


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


def test_the_eye_is_not_invariant_across_the_family():
    """Why the eye is an explicit identity row rather than an omission.

    The old model derived "invariant" by intersecting sibling coats' palettes,
    which found the four eye colours and nothing else. That held for the three
    coats it was run on and is not a property of the colour: on socks_brown the
    iris itself splits. A row states the intent; an omission cannot be told
    apart from a forgotten one.
    """
    if not HORSES.is_dir():
        pytest.skip("pack not present")
    bay = load_sheet(HORSES / "Horse_fullcolor_brown.png")
    socks = load_sheet(HORSES / "Horse_socks_brown.png")
    rows, _ = correspond(bay, socks)
    iris = next(r for r in rows if r.source == from_hex("#507a00"))
    assert len(iris.branches) > 1, "the iris does change on socks_brown"
    assert iris.minority == 9 and iris.total == 120

    for name in ("coat_bay",) + COATS:
        coat = _palette(name)
        for eye in ("#ffffff", "#507a00", "#334d00", "#0b0b0b"):
            row = next(r for r in coat["map"] if r["from"] == eye)
            assert row["to"] == eye, f"{name}: {eye} must be an identity row"


def test_extracted_bay_palette_matches_the_art():
    """coat_bay is the key space, so it must be exactly the art's palette."""
    bay = _palette("coat_bay")
    keys = {from_hex(r["from"]) for r in bay["map"]}
    assert keys == set(decompose(_sheet(SHEETS["horse"])).palette)
    assert len(bay["map"]) == 13
    assert all(r["from"] == r["to"] for r in bay["map"]), "bay is the identity"


def test_substitute_leaves_unmapped_colours_alone():
    """Passthrough now means one specific thing: outside the key space.

    The rider, halter, rope and tack are not among bay's thirteen colours, so
    they cannot be recoloured by accident. The eye *is* in the key space and is
    protected by an identity row instead — the two cases are distinguished by a
    fact about the artwork rather than by a convention.
    """
    indexed = decompose(_sheet(SHEETS["ride"]))
    mapping = mapping_between(_palette("coat_bay"), _palette("coat_fox"))
    after = substitute(indexed, mapping)
    for before, now in zip(indexed.palette, after):
        assert now == mapping.get(before, before)
    untouched = [c for c in indexed.palette if c not in mapping]
    assert untouched, "the ride sheet has rider colours outside the key space"
    assert from_hex("#507a00") in mapping, "the eye is a row, not an omission"


def test_mapping_rejects_a_key_space_mismatch():
    bay = _palette("coat_bay")
    short = dict(_palette("coat_fox"))
    short["map"] = short["map"][:-1]
    with pytest.raises(ValueError, match="key space mismatch"):
        mapping_between(bay, short)


def test_mapping_rejects_duplicate_keys():
    """`from` is the key space; a repeat would collapse the map and lose a step.

    This is also why the file stores rows in a list: a YAML mapping would let
    yaml.safe_load keep the last of two duplicate keys without telling anyone.
    """
    bay = _palette("coat_bay")
    dupe = dict(_palette("coat_fox"))
    dupe["map"] = dupe["map"] + [dict(dupe["map"][0])]
    with pytest.raises(ValueError, match="duplicate `from`"):
        mapping_between(bay, dupe)


def test_mapping_rejects_a_foreign_base():
    bay = _palette("coat_bay")
    foreign = dict(_palette("coat_fox"))
    foreign["base"] = "coat_somewhere_else"
    with pytest.raises(ValueError, match="declares base"):
        mapping_between(bay, foreign)


def test_duplicate_targets_are_allowed_and_black_needs_them():
    """The asymmetry, on the one coat that proves it is not hypothetical.

    Black's art uses eleven colours where bay uses thirteen, so two of bay's
    steps collapse onto a shared black step. `to` is not a key precisely so
    that this is expressible.
    """
    black = _palette("coat_black")
    targets = [r["to"] for r in black["map"]]
    assert len(black["map"]) == 13
    assert len(set(targets)) == 10
    repeated = sorted({t for t in targets if targets.count(t) > 1})
    assert repeated == ["#000000", "#262626", "#444444"]
    mapping_for(black)  # distinct keys, so this must not raise


COATS = ("coat_black", "coat_white", "coat_fox", "coat_dun", "coat_flaxen")


def test_every_coat_covers_the_base_key_space():
    bay = _palette("coat_bay")
    keys = {r["from"] for r in bay["map"]}
    for name in COATS:
        coat = _palette(name)
        assert coat["base"] == "coat_bay"
        assert {r["from"] for r in coat["map"]} == keys, f"{name} key space"


def test_colour_addressed_coats_keep_the_base_row_order():
    """Extracted coats diff row for row against bay and each other, so a
    reordering hand-edit is caught.

    Role-addressed coats are exempt on purpose: they are written by intent and
    grouped by feature, which is the more useful reading order for something a
    human authored. The key space is still checked above, so exempting the order
    gives up nothing but the diff alignment.
    """
    order = [r["from"] for r in _palette("coat_bay")["map"]]
    for name in COATS:
        coat = _palette(name)
        raw = yaml.safe_load(
            (ROOT / f"config/palettes/{name}.yaml").read_text(encoding="utf-8"))
        if all("from" in r for r in raw["map"]):
            assert [r["from"] for r in coat["map"]] == order, f"{name} order"


def test_every_row_of_every_coat_carries_its_role():
    """Roles are what make a coat readable and authorable. A row without one is
    a row nobody can address by intent."""
    for name in ("coat_bay",) + COATS:
        coat = _palette(name)
        missing = [r["from"] for r in coat["map"] if "role" not in r]
        assert not missing, f"{name}: rows without a role: {missing}"


def test_roles_pair_with_the_same_colour_in_every_coat():
    """The role vocabulary is declared once, in the base coat. A derived coat
    that paired MANE_LIGHT with a different bay colour would silently mean
    something else by it — the loader rejects that, and this pins the rejection
    rather than trusting it."""
    bay = _palette("coat_bay")
    expected = {r["role"]: r["from"] for r in bay["map"]}
    for name in COATS:
        for row in _palette(name)["map"]:
            assert row["from"] == expected[row["role"]], f"{name}/{row['role']}"


def test_a_derived_coat_may_be_addressed_by_role():
    """Authoring by intent, resolved to the key space by the loader.

    coat_flaxen is written entirely in roles and never names a bay colour, which
    is the point: the file says what it changes, not which hex it changes.
    """
    raw = yaml.safe_load(
        (ROOT / "config/palettes/coat_flaxen.yaml").read_text(encoding="utf-8"))
    assert all("from" not in r for r in raw["map"]), "flaxen is role-addressed"
    assert all("role" in r for r in raw["map"])
    resolved = _palette("coat_flaxen")
    assert all("from" in r for r in resolved["map"]), "the loader resolves them"


def test_role_addressing_rejects_an_unknown_role():
    from pixelasset.config import _resolve_roles

    bay = _palette("coat_bay")
    coat = {"name": "coat_t", "base": "coat_bay",
            "map": [{"role": "WITHERS", "to": "#000000"}]}
    with pytest.raises(ValueError, match="unknown role"):
        _resolve_roles(PATHS, ROOT / "x.yaml", coat)
    assert bay["name"] == "coat_bay"


def test_role_addressing_rejects_a_disagreeing_pair():
    """Both `role` and `from` is allowed — an extracted coat carries both so it
    reads as more than hex — but it is checked, not trusted."""
    from pixelasset.config import _resolve_roles

    coat = {"name": "coat_t", "base": "coat_bay",
            "map": [{"role": "BODY_BASE", "from": "#000000", "to": "#123456"}]}
    with pytest.raises(ValueError, match="pairs that role with"):
        _resolve_roles(PATHS, ROOT / "x.yaml", coat)


FOX_MAPPING = {
    # Captured from the pre-refactor code, where the nine body rows came from
    # zipping two ramps and the four eye rows were pass-through. This is the
    # oracle for "the format migration changed no pixels".
    "#80472c": "#b44921", "#190d06": "#290f05", "#5e3420": "#863619",
    "#442516": "#622812", "#150a05": "#240d05", "#000000": "#090302",
    "#2a160d": "#3f1a0c", "#734e3d": "#b84a22", "#482f23": "#732f15",
    "#ffffff": "#ffffff", "#507a00": "#507a00", "#334d00": "#334d00",
    "#0b0b0b": "#0b0b0b",
}


def test_fox_mapping_is_unchanged_by_the_refactor():
    got = mapping_between(_palette("coat_bay"), _palette("coat_fox"))
    assert {to_hex(k): to_hex(v) for k, v in got.items()} == FOX_MAPPING


def test_coat_files_all_validate():
    """Closes the debt these files carried: they were never schema-validated."""
    found = sorted(p.stem for p in (ROOT / "config/palettes").glob("coat_*.yaml"))
    assert found == ["coat_bay", "coat_black", "coat_dun", "coat_flaxen",
                     "coat_fox", "coat_white"]
    for name in found:
        _palette(name)


def test_coat_source_hashes_match():
    """The §19 record, checked. Not called from ingest — see verify_source."""
    if not (ROOT / "samples/Full_Pack").is_dir():
        pytest.skip("gitignored third-party pack not present")
    for name in ("coat_bay",) + COATS:
        verify_source(_palette(name), ROOT)


# --- extraction ------------------------------------------------------------

def _extract(target_file: str, name: str) -> dict:
    """As the CLI does it, roles included — an extracted file should read as
    more than a wall of hex."""
    base = _palette("coat_bay")
    return extract_coat(
        _sheet(SHEETS["horse"]),
        _sheet(f"Horse_Sprite_Asset/Horses_no_equipment/{target_file}"),
        name=name, base_name="coat_bay",
        source={"sheet": "x", "sha256": "0" * 64},
        roles={from_hex(r["from"]): r["role"] for r in base["map"]},
    )


def test_extracting_bay_from_bay_is_the_identity():
    """Makes coat_bay mechanical rather than hand-authored, and is the acid
    test's premise stated as a fact about the file."""
    got = _extract("Horse_fullcolor_brown.png", "coat_bay")
    assert got["map"] == _palette("coat_bay")["map"]


@pytest.mark.parametrize("target,name,minority", [
    ("Horse_fullcolor_black.png", "coat_black", 205),
    ("Horse_fullcolor_White.png", "coat_white", 207),
])
def test_extraction_reproduces_the_checked_in_file(target, name, minority):
    """Extraction is mechanical, not a judgement call about which colours
    collapse — the checked-in values are the measured ones."""
    got = _extract(target, name)
    assert got["map"] == _palette(name)["map"]
    assert got["_stats"]["minority_pixels"] == minority


@pytest.mark.parametrize("target,name,splits,worst", [
    ("Horse_paint_brown.png", "coat_paint", 4, "#80472c"),
    ("Horse_socks_brown.png", "coat_socks", 5, "#190d06"),
])
def test_extraction_refuses_a_marking_coat(target, name, splits, worst):
    """A colour that becomes white in one place and stays brown in another is a
    region of the artwork. Picking its dominant branch would render a coat that
    is quietly wrong on tens of thousands of pixels, so this fails instead.

    The split count is asserted so a threshold change cannot quietly re-admit a
    marking coat.
    """
    with pytest.raises(AmbiguousCoat) as exc:
        _extract(target, name)
    message = str(exc.value)
    assert f"{splits} source colour(s) split" in message
    assert worst in message
    assert "mask" in message


def test_the_minority_gap_that_sets_the_threshold():
    """Freezes the evidence for MAX_MINORITY_FRACTION rather than the constant.

    Legitimate coats top out near 1.25%; genuine one-to-many bottoms out near
    8.6%. If those ever converge, 5% stops being a safe place to draw the line
    and this says so before a marking coat slips through as a palette.
    """
    bay = _sheet(SHEETS["horse"])

    def worst(target):
        rows, _ = correspond(bay, _sheet(
            f"Horse_Sprite_Asset/Horses_no_equipment/{target}"))
        return max(r.minority_fraction for r in rows
                   if r.minority >= 16)

    assert worst("Horse_fullcolor_black.png") < 0.02
    assert worst("Horse_fullcolor_White.png") < 0.02
    assert worst("Horse_paint_brown.png") > 0.08
    assert worst("Horse_socks_brown.png") > 0.08


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
    """Passthrough protects the rider; an identity row protects the eye."""
    _, sheet = _built("bay", "ride", "coat_fox")
    rgb = {tuple(int(v) for v in c) for c in sheet[sheet[:, :, 3] == 255][:, :3]}
    for keep in ("#ffd6c2", "#e2a182", "#507a00"):   # rider skin, rider skin, eye
        assert from_hex(keep) in rgb, f"{keep} should survive a coat swap"
    assert from_hex("#80472c") not in rgb, "bay's base body colour should be gone"


@pytest.mark.parametrize(
    "coat", ["coat_fox", "coat_black", "coat_white", "coat_dun"])
def test_rendered_coat_is_geometrically_identical_to_its_source(coat):
    _, bay = _built("bay", "ride")
    _, other = _built("bay", "ride", coat)
    assert np.array_equal(bay[:, :, 3], other[:, :, 3])


@pytest.mark.parametrize("coat", ["coat_black", "coat_white"])
def test_rendered_coat_uses_only_its_own_targets(coat):
    """Every opaque colour is either a target of this coat or outside the key
    space entirely (rider, tack). Nothing else can get in."""
    _, sheet = _built("bay", "ride", coat)
    used = {tuple(int(v) for v in c) for c in sheet[sheet[:, :, 3] == 255][:, :3]}
    targets = {from_hex(r["to"]) for r in _palette(coat)["map"]}
    _, bay = _built("bay", "ride")
    bay_used = {tuple(int(v) for v in c) for c in bay[bay[:, :, 3] == 255][:, :3]}
    outside = bay_used - {from_hex(r["from"]) for r in _palette("coat_bay")["map"]}
    assert used <= targets | outside


FROZEN_SHEETS = {
    # Captured before the format migration. The production PNGs are gitignored,
    # so this and FOX_MAPPING are the only oracles for "no pixel moved".
    ("bay", "ride"): "84fa627c2799061fb2bfba102ee6a1d91d31e8059c0a779669ee2182f8b378a4",
    ("bay", "lead"): "73a441d6c3cfc63ad6d2f41ac30b951694acb18bf09fd5a26366928a51a30ee9",
}
FROZEN_RENDERED = {
    ("bay", "ride", "coat_fox"): "e157bc7faaddaab7258dcb43efc2e9f9a650baae215d0474e019ad0257d6f6de",
    ("bay", "lead", "coat_fox"): "600d36579622d36b68b0d425e8e3aff9182bdc17b81e6672e390e90e0e5ae6f4",
}


@pytest.mark.parametrize("key,want", sorted(FROZEN_SHEETS.items()))
def test_shipped_bay_spritesheets_are_frozen(key, want):
    assert _sheet_hash(_built(*key)) == want


@pytest.mark.parametrize("key,want", sorted(FROZEN_RENDERED.items()))
def test_fox_spritesheets_are_frozen(key, want):
    """The format migration must not move a fox pixel."""
    assert _sheet_hash(_built(*key)) == want


@pytest.mark.parametrize("coat,mode,expected", [
    ("black", "ride", 209), ("black", "lead", 1903),
    ("white", "ride", 339), ("white", "lead", 5757),
])
def test_rendered_coat_is_close_to_but_not_the_packs_own(coat, mode, expected):
    """The honest cost of "a coat is a palette", per sheet family.

    Small on the body sheets — these are the minority branches. Not small on
    the jump sheets, which the pack shades inconsistently with its own body
    sheets; see test_the_pack_disagrees_with_itself and the coat descriptions.
    """
    pack, root = _pack()
    src = pack["sources"][{"ride": "ride", "lead": "lead"}[mode]]
    folder = root / src["dir"]
    bay = load_sheet(folder / pack["coats"]["bay"]["files"][mode])
    shipped = load_sheet(folder / pack["coats"][coat]["files"][mode])
    mapping = mapping_between(_palette("coat_bay"), _palette(f"coat_{coat}"))
    indexed = decompose(bay)
    out = render(indexed, substitute(indexed, mapping))
    both = (bay[:, :, 3] == 255) & (shipped[:, :, 3] == 255)
    mismatch = int((~np.all(out[:, :, :3] == shipped[:, :, :3], axis=2) & both).sum())
    assert mismatch == expected


def test_the_key_space_covers_every_sheet_family():
    """What licenses passthrough.

    Bay's thirteen colours were measured on the no-equipment sheet, but the map
    is applied to ride, lead and jump. Every colour on those sheets outside the
    key space — rider skin, halter, rope, tack — is identical between bay and
    black, so passing them through is measured rather than hoped for.
    """
    pack, root = _pack()
    keys = {from_hex(r["from"]) for r in _palette("coat_bay")["map"]}
    for fam in ("ride", "ride_jump", "lead"):
        folder = root / pack["sources"][fam]["dir"]
        bay = load_sheet(folder / pack["coats"]["bay"]["files"][fam])
        black = load_sheet(folder / pack["coats"]["black"]["files"][fam])
        both = (bay[:, :, 3] == 255) & (black[:, :, 3] == 255)
        outside = both & ~np.isin(
            (bay[:, :, 0].astype(np.uint32) << 16)
            | (bay[:, :, 1].astype(np.uint32) << 8) | bay[:, :, 2],
            np.array([(r << 16) | (g << 8) | b for r, g, b in keys], dtype=np.uint32),
        )
        differs = ~np.all(bay[:, :, :3] == black[:, :, :3], axis=2)
        changed = int((differs & outside).sum())
        assert changed == 0, f"{fam}: {changed} out-of-keyspace px differ"


def test_the_pack_disagrees_with_itself():
    """Why extraction is pinned to one named sheet and agreement is advisory.

    The pack's jump sheets are shaded inconsistently with its own body sheets,
    so a coat that matched every shipped sheet cannot exist. These are the three
    known disagreements; a fourth appearing should be looked at, not absorbed.
    """
    from pixelasset.coat import agreement

    pack, root = _pack()
    for coat, expected in [
        ("black", ["#000000: this sheet says #101010 (3457 px), "
                   "coat_black says #000000"]),
        ("white", ["#5e3420: this sheet says #808080 (8441 px), "
                   "coat_white says #aeaeae",
                   "#442516: this sheet says #555555 (6442 px), "
                   "coat_white says #6a6a6a"]),
    ]:
        pairs = []
        for fam in ("ride", "ride_jump", "lead"):
            folder = root / pack["sources"][fam]["dir"]
            pairs.append((
                load_sheet(folder / pack["coats"]["bay"]["files"][fam]),
                load_sheet(folder / pack["coats"][coat]["files"][fam]),
            ))
        assert agreement(_palette(f"coat_{coat}"), pairs) == expected


def test_ingest_rejects_a_rendered_coat_that_shadows_a_shipped_one():
    """Asset ids, manifest entries and the game's coat cycle are keyed on the
    coat id, so a clash would silently drop a coat rather than fail."""
    import yaml as _yaml

    from pixelasset.paths import ProjectPaths as _PP

    pack = _yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    pack["rendered_coats"] = {
        "black": {"label": "Clash", "from": "bay",
                  "base_palette": "coat_bay", "palette": "coat_black"}
    }
    clash = ROOT / "packs" / "_clash_test.yaml"
    clash.write_text(_yaml.safe_dump(pack), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="declared twice"):
            ingest.ingest_pack(clash, _PP(ROOT), only=["nothing"])
    finally:
        clash.unlink()


def test_the_hand_authored_dun_holds_bays_shading_ladder():
    """What authoring a coat has to get right, since there is no ground truth.

    A coat is only a palette if the ladder survives: the rows must keep bay's
    luminance ordering, or shading that reads as form on one coat reads as noise
    on another. The dun holds every body row to within 0.5 of bay's luminance
    except the highlight, which is lifted deliberately — bay separates highlight
    from body by hue, not value, and desaturating collapses that.
    """
    bay, dun = _palette("coat_bay"), _palette("coat_dun")
    eye = {"#ffffff", "#507a00", "#334d00", "#0b0b0b"}

    def lum(h):
        c = from_hex(h)
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    rows = [r for r in dun["map"] if r["from"] not in eye]
    before = [lum(r["from"]) for r in rows]
    after = [lum(r["to"]) for r in rows]
    assert sorted(range(len(rows)), key=lambda i: -before[i]) == \
        sorted(range(len(rows)), key=lambda i: -after[i]), "ladder order"

    deviations = [r["from"] for r, b, a in zip(rows, before, after)
                  if abs(b - a) > 0.5]
    assert deviations == ["#734e3d"], "only the highlight may deviate"
    assert max(after) - min(after) > max(before) - min(before), \
        "the lifted highlight should widen the range, not narrow it"


def test_every_collapsed_role_pair_is_signed_off():
    """§27: validation reports, and an intended finding is accepted WITH the
    reason attached rather than silently dropped.

    A collapse is not automatically a defect — a black horse's mane really is
    not lighter than its body, and extraction measured that off the art rather
    than deciding it. But it costs whatever the two roles were distinguishing,
    usually the mane's hatched strands against the neck, so it is signed off
    per coat. Both directions are checked: an unsigned collapse fails, and so
    does an acceptance that no longer corresponds to one, because a stale
    sign-off is how a real regression gets waved through.
    """
    from pixelasset.coat import collapses

    bay = _palette("coat_bay")
    for name in ("coat_bay",) + COATS:
        coat = _palette(name)
        found = {f"{a}/{b}" for a, b, _, _ in collapses(bay, coat)}
        accepted = set(coat.get("accept_collapse") or {})
        assert found - accepted == set(), (
            f"{name}: unsigned collapse(s) {sorted(found - accepted)} — either "
            f"fix the colours or accept them with a reason"
        )
        assert accepted - found == set(), (
            f"{name}: accept_collapse names {sorted(accepted - found)}, which "
            f"no longer collapse. Remove the stale sign-off."
        )
        for pair, reason in (coat.get("accept_collapse") or {}).items():
            assert len(reason.strip()) > 20, f"{name}/{pair} needs a real reason"


def test_the_mane_needs_headroom_not_a_direction():
    """The structural constraint, stated precisely enough to be true.

    An earlier version of this test claimed MANE_LIGHT must stay above
    BODY_BASE. coat_white disproves it: its mane is 198 against a 238 body and
    reads perfectly, because at that brightness there is room below the body and
    above the outline. What actually binds is headroom — bay's dark end is
    already three values of outline, so a body at bay's own luminance leaves
    almost nowhere to darken a mane into. Hence the dun's lifted mane and
    white's lowered one are both correct.

    The real rule is just "stay separated", which `collapses` already enforces
    for every coat. This pins the headroom fact that explains why.
    """
    from pixelasset.coat import _luminance, role_colours

    bay = role_colours(_palette("coat_bay"))
    line_top = max(_luminance(bay[r]) for r in _palette("coat_bay")["roles"]["line"])
    body_base = _luminance(bay["BODY_BASE"])
    assert line_top < 20 and body_base < 90, (
        "bay is a dark palette: the outline tops out at 15 and the body base "
        "sits at 81, which is the whole reason a darkened mane has nowhere to go"
    )
    white = role_colours(_palette("coat_white"))
    assert _luminance(white["MANE_LIGHT"]) < _luminance(white["BODY_BASE"]), (
        "white's mane is darker than its body — a direction rule would be wrong"
    )


def test_propose_catches_the_collapse_that_was_made_by_hand():
    """The regression this check exists for.

    An earlier dun desaturated the body and the mane together, dropping
    MANE_LIGHT to 6 RGB units from BODY_BASE against bay's 23. It was found by
    eye, late. This is the same intent, and it must warn.
    """
    from pixelasset.coat import propose_coat

    _, warnings = propose_coat(
        _palette("coat_bay"),
        {"body": "#5a5138", "mane": "#5e553a"},
        name="coat_regression",
    )
    assert any("BODY_BASE" in w and "MANE_LIGHT" in w for w in warnings)


def test_propose_holds_the_ladder_and_keeps_the_eye():
    from pixelasset.coat import _luminance, from_hex, propose_coat, role_colours

    bay = _palette("coat_bay")
    coat, _ = propose_coat(bay, {"body": "#c89a4e"}, name="coat_t")
    before, after = role_colours(bay), role_colours(coat)

    body = bay["roles"]["body"]
    ratios = [_luminance(after[r]) / _luminance(before[r]) for r in body]
    assert max(ratios) - min(ratios) < 0.02, "the body ladder scales uniformly"
    for role in bay["roles"]["eye"] + bay["roles"]["mane"]:
        assert after[role] == before[role], "groups left out are kept"
    assert from_hex("#c89a4e") in after.values()


def test_propose_rejects_an_unknown_group():
    from pixelasset.coat import propose_coat

    with pytest.raises(ValueError, match="unknown role group"):
        propose_coat(_palette("coat_bay"), {"withers": "#000000"}, name="t")
