"""Clips that are not gaits: a two-row gesture, and a turn out of profile.

Most of a leading sheet is what you would guess — a row per action per facing,
each looping. Two things on it are not, and both were sitting unused because
nothing in the frames says what they are:

- `pet` is half a gesture. The row before it is the other half, and the two meet
  end to end in both directions, so together they are one cycle.
- `turn_walk` and `turn_trot` are transitions, not gaits. The horse is held
  byte-identically on frame 0 of the gait above them while only the handler is
  redrawn, and the handler is already stepped toward the camera — 5px below the
  ground every other clip of that facing stands on.

The second fact is the one with teeth: the baseline is measured as the lowest
row across a facing's clips, so admitting a transition that reaches lower moves
the ground and floats everything else. `defines_baseline: false` is the opt-out
and this pins that it is still needed.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest

from pixelasset import detect, ingest
from pixelasset.coat import load_sheet

ROOT = Path(__file__).resolve().parent.parent
PACK_FILE = ROOT / "packs/full_pack.yaml"

ADDED = ("nuzzle", "turn_walk", "turn_trot")
TURNS = ("turn_walk", "turn_trot")


def _pack():
    pack = ingest.load_pack(PACK_FILE)
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip("gitignored third-party pack not present")
    return pack, root


def _lead_sheet(pack, root, src="lead"):
    return load_sheet(root / pack["sources"][src]["dir"]
                      / pack["coats"]["bay"]["files"][src])


# --- what the rows are -----------------------------------------------------

@pytest.mark.parametrize("facing", ["east", "west", "south", "north"])
def test_nuzzle_and_pet_meet_end_to_end(facing):
    """Why they are one gesture rather than two. Both joins have to hold: only
    the forward one would make it a sequence, and it is a cycle."""
    pack, root = _pack()
    sheet = _lead_sheet(pack, root)
    blk = pack["modes"]["lead"]["clips"][facing]
    cw, ch = 100, 96
    a = detect.alpha(sheet)

    def frames(action):
        return len(detect.row_frames(a, cw, ch, blk[action]["row"]))

    def cell(action, col):
        r = blk[action]["row"]
        return sheet[r * ch:(r + 1) * ch, col * cw:(col + 1) * cw]

    def differ(x, y):
        return int(np.any(x != y, axis=2).sum())

    forward = differ(cell("nuzzle", frames("nuzzle") - 1), cell("pet", 0))
    back = differ(cell("nuzzle", 0), cell("pet", frames("pet") - 1))
    assert forward <= 20, f"{facing}: nuzzle does not run into pet ({forward} px)"
    assert back <= 20, f"{facing}: pet does not run back into nuzzle ({back} px)"

    # Not a tautology: the same comparison against idle is two orders out.
    apart = differ(cell("nuzzle", 0), cell("idle", 0))
    assert apart > 20 * 10, f"{facing}: nuzzle should not just be the idle pose"


def _longest_identical_column_run(a: np.ndarray, b: np.ndarray) -> int:
    """Longest contiguous run of byte-identical columns between two cells.

    Column-wise rather than region-wise because the handler is on the right
    facing east and on the left facing west, and hard-coding either side would
    pass one facing and quietly measure the wrong half of the other.
    """
    same = [bool(np.array_equal(a[:, c], b[:, c])) for c in range(a.shape[1])]
    best = run = 0
    for m in same:
        run = run + 1 if m else 0
        best = max(best, run)
    return best


@pytest.mark.parametrize("facing,turn,gait",
                         [(f, t, g) for f in ("east", "west")
                          for t, g in (("turn_walk", "walk"), ("turn_trot", "trot"))])
def test_a_turn_holds_the_horse_and_moves_only_the_handler(facing, turn, gait):
    """What makes these transitions rather than gaits, and it is exact: three
    quarters of the cell is byte-identical to frame 0 of the gait the row sits
    under, and the quarter that differs is the one holding the handler."""
    pack, root = _pack()
    sheet = _lead_sheet(pack, root)
    blk = pack["modes"]["lead"]["clips"][facing]
    cw, ch = 100, 96

    def cell(row, col):
        return sheet[row * ch:(row + 1) * ch, col * cw:(col + 1) * cw]

    held = cell(blk[gait]["row"], 0)
    run = _longest_identical_column_run(cell(blk[turn]["row"], 0), held)
    assert run >= 70, (
        f"{facing}/{turn}: only {run} of {cw} columns hold {gait} frame 0, so "
        f"the horse is not being held and this is a gait rather than a "
        f"transition"
    )

    # Not a tautology: two frames of the same gait share far less than this.
    moving = _longest_identical_column_run(cell(blk[gait]["row"], 1), held)
    assert moving < run, (
        f"{facing}/{gait}: consecutive gait frames share {moving} columns, "
        f"which would make the held-horse measurement meaningless"
    )


@pytest.mark.parametrize("facing", ["east", "west"])
def test_a_turn_reaches_below_the_ground_its_facing_stands_on(facing):
    """The measurement `defines_baseline: false` exists for. The handler has
    stepped toward the camera on its way to the front-facing stance, whose own
    ground is lower still."""
    pack, root = _pack()
    sheet = _lead_sheet(pack, root)
    a = detect.alpha(sheet)
    blk = pack["modes"]["lead"]["clips"][facing]

    def base(action):
        return detect.baseline(detect.row_frames(a, 100, 96, blk[action]["row"]))

    ground = base("walk")
    for turn in TURNS:
        assert base(turn) == ground + 5, f"{facing}/{turn}"
    south = pack["modes"]["lead"]["clips"]["south"]
    south_ground = detect.baseline(
        detect.row_frames(a, 100, 96, south["walk"]["row"]))
    assert south_ground > base(TURNS[0]), (
        "a turn should sit between profile ground and the front-facing ground"
    )


@pytest.mark.parametrize("facing", ["east", "west"])
def test_turns_are_excluded_from_the_baseline(facing):
    """Config, not code: drop the flag and the ground moves."""
    pack, _ = _pack()
    blk = pack["modes"]["lead"]["clips"][facing]
    for turn in TURNS:
        assert blk[turn]["defines_baseline"] is False
    for action, spec in blk.items():
        if action not in TURNS:
            assert spec.get("defines_baseline", True) is True, action


# --- what the new rows must not have done ----------------------------------

_BUILT: dict[tuple, tuple] = {}


def _build(pack, mode):
    key = (id(pack), mode)
    if key not in _BUILT:
        _, root = _pack()
        asset = ingest.build_asset(pack, root, "bay", mode)
        _BUILT[key] = (asset,
                       np.array(ingest.render_sheet(asset).convert("RGBA")))
    return _BUILT[key]


def _without_the_new_clips(pack):
    stripped = copy.deepcopy(pack)
    for mode in ("lead", "lead_saddled"):
        for blk in stripped["modes"][mode]["clips"].values():
            for name in ADDED:
                blk.pop(name, None)
            if "pet" in blk:
                blk["pet"] = {k: v for k, v in blk["pet"].items()
                              if k not in ("loop", "next")}
    return stripped


@pytest.mark.parametrize("mode", ["lead", "lead_saddled"])
def test_adding_clips_did_not_move_the_existing_ones(mode):
    """The real invariant behind the re-frozen hashes in test_coat_render.py.
    A hash says "something changed"; this says what may not have."""
    pack, _ = _pack()
    old_asset, old_sheet = _build(_without_the_new_clips(pack), mode)
    new_asset, new_sheet = _build(pack, mode)

    assert old_asset.origin == new_asset.origin, "the feet anchor must not move"
    assert old_asset.cell[0] == new_asset.cell[0], "cell width must not move"
    old_h, new_h = old_asset.cell[1], new_asset.cell[1]
    assert new_h >= old_h

    old_rows = {(c.facing, c.action): c.row_index for c in old_asset.clips}
    new_rows = {(c.facing, c.action): c.row_index for c in new_asset.clips}
    for key, r_old in old_rows.items():
        block_old = old_sheet[r_old * old_h:(r_old + 1) * old_h]
        r_new = new_rows[key]
        block_new = new_sheet[r_new * new_h:(r_new + 1) * new_h]
        assert np.array_equal(block_old, block_new[:old_h]), f"{key} moved"
        assert not (block_new[old_h:][:, :, 3] > 0).any(), \
            f"{key} spilled into the rows the turns needed"


@pytest.mark.parametrize("mode", ["lead", "lead_saddled"])
def test_the_new_clips_are_actually_in_the_asset(mode):
    pack, _ = _pack()
    asset, _sheet = _build(pack, mode)
    have = {(c.facing, c.action) for c in asset.clips}
    for facing in ("east", "west", "south", "north"):
        assert (facing, "nuzzle") in have
    for facing in ("east", "west"):
        for turn in TURNS:
            assert (facing, turn) in have
    assert not any(t in a for _f, a in have for t in ("turn",)
                   if _f in ("south", "north")), \
        "the artwork draws no turn on the front-facing blocks"


# --- the contract extension ------------------------------------------------

def test_metadata_chains_the_two_halves_of_the_gesture():
    pack, _ = _pack()
    asset, _sheet = _build(pack, "lead")
    meta = ingest.metadata(asset)
    by = {(a["facing"], a["name"]): a for a in meta["animations"]}
    for facing in ("east", "west", "south", "north"):
        nuzzle, pet = by[(facing, "nuzzle")], by[(facing, "pet")]
        assert nuzzle["loop"] is False and pet["loop"] is False
        assert nuzzle["next"] == "pet" and pet["next"] == "nuzzle"
    # A turn ends and hands back to whatever the player is doing, so it says
    # nothing about what follows.
    assert "next" not in by[("east", "turn_walk")]


def test_the_schema_accepts_next():
    import jsonschema

    from pixelasset.config import load_schema, validate_schema
    from pixelasset.paths import ProjectPaths

    paths = ProjectPaths(ROOT)
    schema = load_schema(paths, "metadata")
    assert "next" in schema["properties"]["animations"]["items"]["properties"]
    pack, _ = _pack()
    asset, _sheet = _build(pack, "lead")
    ingest.render_sheet(asset)
    validate_schema(ingest.metadata(asset), schema, source="lead metadata")
    jsonschema.validate("pet",
                        schema["properties"]["animations"]["items"]
                        ["properties"]["next"])


# --- grazing: one row, three clips -----------------------------------------
#
# The graze row is an arc, not a cycle: the muzzle sits high in frame 0, reaches
# its lowest across frames 2-6 and starts back up in frame 7. Looping the whole
# row played the arc end to end, so the horse nodded at 7fps and never ate. Cut
# at the plateau with `first:`/`frames:` it becomes lower -> hold -> lift, and
# `next:` chains the three.
#
# The risk in cutting a row up is losing a frame at a seam, so that is what
# these check — against the whole row, not against a hash.

GRAZE_PARTS = ("graze_down", "graze", "graze_up")


def _whole_graze(pack):
    """The pack as it was: one looping eight-frame graze clip per side-on
    block. The oracle the split has to reproduce frame for frame."""
    joined = copy.deepcopy(pack)
    for mode in ("ride", "ride_saddled", "lead", "lead_saddled"):
        for blk in joined["modes"][mode]["clips"].values():
            if "graze" not in blk:
                continue
            src, row = blk["graze"]["src"], blk["graze"]["row"]
            for name in GRAZE_PARTS:
                blk.pop(name, None)
            blk["graze"] = {"src": src, "row": row, "fps": 7}
    return joined


@pytest.mark.parametrize("mode", ["ride", "lead"])
def test_the_three_graze_clips_are_the_whole_row_in_order(mode):
    """No frame lost at either seam, and none drawn twice."""
    pack, _ = _pack()
    whole, _ = _build(_whole_graze(pack), mode)
    split, _ = _build(pack, mode)
    by_whole = {(c.facing, c.action): c for c in whole.clips}
    by_split = {(c.facing, c.action): c for c in split.clips}

    for facing in ("east", "west"):
        want = [(f.row, f.col) for f in by_whole[(facing, "graze")].frames]
        got = [(f.row, f.col) for part in GRAZE_PARTS
               for f in by_split[(facing, part)].frames]
        assert got == want, f"{facing}: {got} is not the graze row in order"
        assert len(want) == 8


@pytest.mark.parametrize("mode", ["ride", "lead"])
def test_only_the_middle_graze_clip_repeats(mode):
    """The hold is the point. The lower and the lift are one-shots that hand on
    through `next`, so a consumer plays the arc once and dwells in the middle
    for as long as the horse is actually eating."""
    pack, _ = _pack()
    asset, _sheet = _build(pack, mode)
    by = {(c.facing, c.action): c for c in asset.clips}
    for facing in ("east", "west"):
        down, hold, up = (by[(facing, p)] for p in GRAZE_PARTS)
        assert (down.loop, hold.loop, up.loop) == (False, True, False)
        assert down.next == "graze"
        assert up.next == "idle"
        assert hold.next is None


def _ground_mass(sheet, cell, clip, i):
    """Opaque pixels in the bottom fourteen rows of one frame.

    How far the muzzle has come down, measured without having to know which end
    of the sprite the head is on or that the leading sheet has a handler beside
    it: a head on the ground puts a lot of extra horse into the bottom of the
    cell. Reading `bbox.y1` instead just measures the hooves, which never move.
    """
    cw, ch = cell
    frame = sheet[clip.row_index * ch:(clip.row_index + 1) * ch,
                  i * cw:(i + 1) * cw]
    return int((frame[ch - 14:, :, 3] > 0).sum())


@pytest.mark.parametrize("mode", ["ride", "lead"])
def test_the_hold_is_where_the_head_is_down(mode):
    """Which three frames the middle clip takes is measured, not chosen. Across
    the whole row the ground mass steps 264/285/297/430/430/430/297/285 riding
    and 198/216/230/307/307/307/230/216 leading — a flat top on frames 3-5 and
    nothing else flat. Cutting one frame wider on either side, which is what
    this file's first attempt did, puts a frame where the head is still moving
    into the clip that is supposed to hold still."""
    pack, _ = _pack()
    asset, sheet = _build(pack, mode)
    by = {(c.facing, c.action): c for c in asset.clips}
    for facing in ("east", "west"):
        mass = {p: [_ground_mass(sheet, asset.cell, by[(facing, p)], i)
                    for i in range(len(by[(facing, p)].frames))]
                for p in GRAZE_PARTS}
        assert len(set(mass["graze"])) == 1, \
            f"{facing}: the hold is not flat: {mass['graze']}"
        floor = mass["graze"][0]
        assert max(mass["graze_down"]) < floor, \
            f"{facing}: graze_down already has the head down: {mass}"
        assert max(mass["graze_up"]) < floor, \
            f"{facing}: graze_up has not lifted yet: {mass}"
        # And the arc really is an arc: it goes down and it comes back.
        assert mass["graze_down"] == sorted(mass["graze_down"]), mass
        assert mass["graze_up"] == sorted(mass["graze_up"], reverse=True), mass


@pytest.mark.parametrize("mode", ["ride", "lead"])
def test_splitting_graze_left_every_other_clip_alone(mode):
    """The same invariant `test_adding_clips_did_not_move_the_existing_ones`
    pins for the leading sheet's new rows, for this change."""
    pack, _ = _pack()
    whole, _ = _build(_whole_graze(pack), mode)
    split, _ = _build(pack, mode)
    assert whole.origin == split.origin, "the feet anchor must not move"
    assert whole.cell == split.cell, "the cell must not move"
    by_split = {(c.facing, c.action): c for c in split.clips}
    for clip in whole.clips:
        if clip.action == "graze":
            continue
        other = by_split[(clip.facing, clip.action)]
        assert [(f.row, f.col) for f in clip.frames] \
            == [(f.row, f.col) for f in other.frames], \
            f"{clip.facing}/{clip.action} moved"


# --- the lead rope ---------------------------------------------------------
#
# A led horse is one 8-connected blob: the rope joins the two bodies, which is
# also why `holds_multiple_sprites` does not split a leading cell. So a frame
# that splits in two is a frame with no rope drawn, and that is worth a census
# rather than a spot check — the pack has fifteen of them per halter sheet and
# fourteen are on purpose.

LEAD_CELL = (100, 96)
PETTING_ROWS = {2, 3, 10, 11}        # nuzzle and pet, east then west
ROPELESS = (8, 3)                    # the one that is not on purpose
PATCH_BOX = (6, 37, 25, 33)          # x, y, w, h — halter, rope, gripping hand
EYE = (slice(50, 55), slice(3, 5))   # the handler's eye, outside the box


def _rope_census(sheet):
    """(row, col) of every frame whose silhouette is more than one blob."""
    a = detect.alpha(sheet)
    cw, ch = LEAD_CELL
    out = []
    for r in range(a.shape[0] // ch):
        for c in range(a.shape[1] // cw):
            cell = a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            if (cell > 0).sum() < 200:
                continue
            if len(detect.blob_sizes(cell > 0, min_size=15)) > 1:
                out.append((r, c))
    return out


def _lead_path(pack, root, coat, src="lead"):
    return root / pack["sources"][src]["dir"] / coat["files"][src]


def test_the_raw_sheets_are_missing_a_rope_on_exactly_one_frame():
    """The census on the artwork as shipped, which is what the patch is for.
    Fourteen splits are the gesture — the handler drops the rope to reach out,
    and the horse wears no halter in those rows either. The fifteenth is row 8
    column 3, one frame of the west idle where the rope is simply not drawn. If
    this ever finds a sixteenth, some sheet has lost a rope and the pack needs
    another entry, not a shrug."""
    pack, root = _pack()
    for coat_id, coat in sorted(pack["coats"].items()):
        if "lead" not in coat["files"]:
            continue
        split = _rope_census(load_sheet(_lead_path(pack, root, coat)))
        petting = [rc for rc in split if rc[0] in PETTING_ROWS]
        other = [rc for rc in split if rc[0] not in PETTING_ROWS]
        assert len(petting) == 14, f"{coat_id}: {len(petting)} petting splits"
        assert other == [ROPELESS], f"{coat_id}: unexpected rope-less frames {other}"


def test_the_patch_puts_the_rope_back_on_every_coat():
    """And the check is the same one that found it missing: after loading
    through the pack's own loader, nothing splits except the petting rows."""
    pack, root = _pack()
    for coat_id, coat in sorted(pack["coats"].items()):
        if "lead" not in coat["files"]:
            continue
        sheet = ingest.load_source_sheet(_lead_path(pack, root, coat),
                                         pack["sources"]["lead"])
        other = [rc for rc in _rope_census(sheet) if rc[0] not in PETTING_ROWS]
        assert other == [], f"{coat_id}: still rope-less at {other}"


def test_the_patch_is_a_copy_and_touches_nothing_else():
    """What makes this a correction rather than a repaint. The equipment
    rectangle is byte-identical in nine of the row's eleven frames, so there is
    no sway to interpolate: the patched frame comes out equal to its donor
    everywhere except the handler's eye, which is a real animation difference
    and sits outside the box."""
    pack, root = _pack()
    coat = pack["coats"]["bay"]
    path = _lead_path(pack, root, coat)
    raw = load_sheet(path)
    fixed = ingest.load_source_sheet(path, pack["sources"]["lead"])
    cw, ch = LEAD_CELL

    def cell(sheet, r, c):
        return sheet[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]

    x, y, bw, bh = PATCH_BOX
    equip = (slice(y, y + bh), slice(x, x + bw))

    # The donor's claim: nine frames of eleven agree exactly on the equipment.
    same = [c for c in range(11)
            if np.array_equal(cell(raw, 8, c)[equip], cell(raw, 8, 2)[equip])]
    assert same == [1, 2, 4, 5, 6, 7, 8, 9, 10], same

    # Nothing outside the one cell moved.
    diff = np.any(raw != fixed, axis=2)
    ys, xs = np.nonzero(diff)
    assert set(ys // ch) == {8} and set(xs // cw) == {3}, "the patch leaked"
    assert diff.sum() == 63

    # And inside it, the result is the donor plus this frame's own eye.
    got, donor = cell(fixed, 8, 3), cell(fixed, 8, 2)
    d = np.any(got != donor, axis=2)
    dy, dx = np.nonzero(d)
    assert (dx.min(), dx.max(), dy.min(), dy.max()) == (3, 4, 50, 54), \
        "the patched frame should differ from its donor only at the eye"
    assert np.array_equal(got[EYE], cell(raw, 8, 3)[EYE]), "the eye was overwritten"
    assert detect.blob_sizes(got[:, :, 3] > 0, min_size=15) == [1505]


def test_the_saddled_leading_sheets_need_no_patch():
    """Why the correction is declared on the source and not on the clip. These
    sheets carry the same rows and the same clip block by inheritance, and they
    do not have the defect — a clip-level fix would have followed the
    inheritance and edited artwork that was never wrong."""
    pack, root = _pack()
    assert "patch" not in pack["sources"]["lead_saddled"]
    for coat_id, coat in sorted(pack["coats"].items()):
        if "lead_saddled" not in coat["files"]:
            continue
        split = _rope_census(load_sheet(_lead_path(pack, root, coat, "lead_saddled")))
        other = [rc for rc in split if rc[0] not in PETTING_ROWS]
        assert other == [], f"{coat_id}: saddled sheet missing a rope at {other}"


def test_the_metadata_says_which_frame_was_corrected():
    """The output no longer matches the source cell it names, and a consumer
    comparing the two has to be able to tell that apart from corruption."""
    pack, _ = _pack()
    for mode, want in (("lead", {("west", "idle"): [3]}), ("lead_saddled", {})):
        asset, _sheet = _build(pack, mode)
        meta = ingest.metadata(asset)
        got = {(a["facing"], a["name"]): a["source"]["patched"]
               for a in meta["animations"] if "patched" in a["source"]}
        assert got == want, f"{mode}: {got}"


@pytest.mark.parametrize("bad,why", [
    ({"to": [99, 3], "from": [8, 2], "box": [6, 37, 25, 33]}, "outside"),
    ({"to": [8, 3], "from": [21, 5], "box": [6, 37, 25, 33]}, "empty cell"),
    ({"to": [8, 3], "from": [8, 2], "box": [6, 37, 400, 33]}, "does not fit"),
    ({"to": [8, 2], "from": [8, 1], "box": [6, 37, 25, 33]}, "changes nothing"),
])
def test_a_patch_that_has_stopped_meaning_anything_is_refused(bad, why):
    """Every way of being stale is an error. The one that matters most is the
    last: a patch whose box already matches its donor is either aimed at the
    wrong cell or aimed at a sheet that has since been fixed upstream, and
    silently doing nothing is how a correction outlives the defect."""
    pack, root = _pack()
    broken = copy.deepcopy(pack)
    broken["sources"]["lead"]["patch"] = [bad]
    with pytest.raises(ValueError, match=why):
        ingest.load_source_sheet(_lead_path(broken, root, broken["coats"]["bay"]),
                                 broken["sources"]["lead"])


def test_a_patch_requires_a_pinned_cell():
    pack, root = _pack()
    broken = copy.deepcopy(pack)
    broken["sources"]["lead"].pop("cell")
    with pytest.raises(ValueError, match="must pin `cell:`"):
        ingest.load_source_sheet(_lead_path(broken, root, broken["coats"]["bay"]),
                                 broken["sources"]["lead"])
