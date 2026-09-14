"""The character sheet: a second family through the same producer.

`Character_base.png` is the one sheet in the project whose grid cannot be
detected — `detect_cell` searches from a 24px minimum because that is what the
horse sheets need, and this cell is 16 wide, so the true grid is never a
candidate. It is pinned in config and confirmed instead.

Everything else about it is measured, and the measurement that matters is the
row map. The author's page lists five animations in four directions, which is
twenty rows; nineteen ship. Which row is which is read off the iris, because
the character is drawn with eyes and eyes are on the front of a head:

    two eye blobs   facing the camera
    none            facing away
    exactly one     in profile

That is what these tests check — not that the config says `south`, but that the
pixels under the row the config calls south have two eyes in them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pixelasset import detect, ingest
from pixelasset.coat import load_sheet

ROOT = Path(__file__).resolve().parent.parent
PACK_FILE = ROOT / "packs/character.yaml"

IRIS = (0x86, 0xB2, 0xFF)
EYES_FOR = {"south": 2, "north": 0, "west": 1, "east": 1}
ACTIONS = ("idle", "walk", "run", "lead_walk", "lead_run")
FACINGS = ("east", "north", "south", "west")


def _pack():
    pack = ingest.load_pack(PACK_FILE)
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip("gitignored third-party pack not present")
    return pack, root


_BUILT = {}


def _built():
    if not _BUILT:
        pack, root = _pack()
        asset = ingest.build_asset(pack, root, "base", "afoot")
        sheet = np.array(ingest.render_sheet(asset).convert("RGBA"))
        _BUILT["asset"] = asset
        _BUILT["sheet"] = sheet
        _BUILT["meta"] = ingest.metadata(asset)
    return _BUILT["asset"], _BUILT["sheet"], _BUILT["meta"]


def _eye_blobs(sheet, w, h, row, col):
    """Distinct iris blobs in one frame — 2 facing you, 1 in profile, 0 away."""
    eye = (np.all(sheet[:, :, :3] == IRIS, axis=2) & (sheet[:, :, 3] > 0))
    xs = sorted(set(np.where(eye[row * h:(row + 1) * h,
                                 col * w:(col + 1) * w])[1].tolist()))
    if not xs:
        return 0
    return 1 + sum(1 for i in range(1, len(xs)) if xs[i] - xs[i - 1] > 1)


# --- the pinned grid -------------------------------------------------------

def test_the_grid_has_to_be_pinned_because_detection_cannot_find_it():
    """The reason `cell:` is not `auto` here. If this ever starts passing,
    `confirm_cell` has stopped being load-bearing for this sheet."""
    _pack()
    a = detect.alpha(load_sheet(ROOT / "samples/Full_Pack/Character_base.png"))
    with pytest.raises(ValueError, match="no plausible grid"):
        detect.detect_cell(a)


def test_the_pinned_grid_is_confirmed_by_the_artwork():
    pack, root = _pack()
    a = detect.alpha(load_sheet(root / "Character_base.png"))
    cell = tuple(pack["sources"]["base"]["cell"])
    assert cell == (16, 34)
    assert detect.confirm_cell(a, cell, source="Character_base.png") == cell
    # Every row bottoms out on the same line.
    baselines = {detect.baseline(detect.row_frames(a, *cell, r))
                 for r in range(a.shape[0] // cell[1])}
    assert baselines == {33}
    # And — the check that a shared baseline does not make — no cell of it
    # reaches both of its own side edges, so no frame carries a slice of its
    # neighbour. This is the evidence the pitch is right rather than merely a
    # divisor; the baseline above holds for a wrong pitch too.
    assert detect.straddling_cells(a, *cell) == (0, 132)


@pytest.mark.parametrize("bad", [(18, 34), (18, 38), (36, 34), (17, 34)])
def test_a_wrong_pin_is_refused(bad):
    """(18, 34) is the pin this sheet actually shipped with. It divides 144 as
    evenly as 16 does, finds frames in every row and measures the same baseline
    of 33 — it is simply two pixels per column out of phase, so every frame
    after the first carried a slice of the next sprite and the game drew a row
    of farmhands walking abreast. Nothing here caught it until `confirm_cell`
    learned to ask about phase rather than only about divisibility."""
    pack, root = _pack()
    a = detect.alpha(load_sheet(root / "Character_base.png"))
    why = ("does not divide" if a.shape[0] % bad[1] or a.shape[1] % bad[0]
           else "cuts sprites")
    with pytest.raises(ValueError, match=why):
        detect.confirm_cell(a, bad, source="x")


# --- the row map -----------------------------------------------------------

@pytest.mark.parametrize("facing", FACINGS)
def test_every_clip_carries_the_facing_the_config_claims(facing):
    """The whole row map in one assertion, checked against the artwork's own
    iris rather than against the config that produced it."""
    _asset, sheet, meta = _built()
    w, h = meta["frame_size"]
    want = EYES_FOR[facing]
    for anim in meta["animations"]:
        if anim["facing"] != facing:
            continue
        counts = [_eye_blobs(sheet, w, h, anim["row"], c)
                  for c in range(anim["frames"])]
        seen = [c for c in counts if c] or [0]
        typical = max(set(seen), key=seen.count)
        assert typical == want, (
            f"{facing}/{anim['name']} row {anim['row']} shows {typical} eye "
            f"blob(s) per frame, but {facing} should show {want}: {counts}"
        )


def test_the_sheet_carries_five_animations_in_four_directions_bar_one():
    """Nineteen rows for twenty clips. The absentee is the north idle, and the
    reason is that it would be an eye-blink drawn from behind."""
    _asset, _sheet, meta = _built()
    have = {(a["facing"], a["name"]) for a in meta["animations"]}
    assert have == {(f, a) for f in FACINGS for a in ACTIONS}

    pack, _ = _pack()
    rows = {(f, a): spec["row"]
            for f, blk in pack["modes"]["afoot"]["clips"].items()
            for a, spec in blk.items()}
    assert len({*rows.values()}) == 19, "nineteen distinct rows are drawn"
    assert rows[("north", "idle")] == rows[("north", "walk")]
    assert pack["modes"]["afoot"]["clips"]["north"]["idle"]["frames"] == 1


def test_a_borrowed_idle_is_the_artists_own_convention():
    """North's idle is taken as the first frame of its walk. That is only
    honest if the artist does the same where a real idle exists — facing south
    it does, and the two first frames are byte-identical."""
    pack, root = _pack()
    sheet = load_sheet(root / "Character_base.png")
    cw, ch = pack["sources"]["base"]["cell"]
    blk = pack["modes"]["afoot"]["clips"]["south"]

    def frame0(row):
        return sheet[row * ch:(row + 1) * ch, 0:cw]

    differ = int(np.any(frame0(blk["idle"]["row"]) != frame0(blk["walk"]["row"]),
                        axis=2).sum())
    assert differ == 0, f"south idle and walk start {differ} px apart"


def _profile_mirror_gap(pack, sheet, action, frames):
    """Per-frame pixel difference between west and east's mirror, one action."""
    cw, ch = pack["sources"]["base"]["cell"]
    clips = pack["modes"]["afoot"]["clips"]
    w_row = clips["west"][action]["row"]
    e_row = clips["east"][action]["row"]
    assert w_row != e_row
    out = []
    for c in range(frames):
        x0 = c * cw
        west = sheet[w_row * ch:(w_row + 1) * ch, x0:x0 + cw]
        east = sheet[e_row * ch:(e_row + 1) * ch, x0:x0 + cw]
        out.append(int(np.any(west != east[:, ::-1], axis=2).sum()))
    return out


# Which profile actions the pack draws twice and which it flips. Measured, and
# it reverses what this file used to claim: under the old out-of-phase 18px cut
# every action looked hand-drawn, because comparing two scrambled cells always
# finds a difference. At the true pitch three of the five are exact mirrors.
MIRRORED = {"idle": 2, "walk": 8, "run": 6}
DRAWN_TWICE = {"lead_walk": 9, "lead_run": 8}


@pytest.mark.parametrize("action,frames", sorted(MIRRORED.items()))
def test_three_profile_actions_are_flipped_artwork(action, frames):
    """East is west mirrored, pixel for pixel. Worth pinning because it is the
    difference between ten rows of artwork and seven: if a future sheet stops
    being a mirror here, the west/east split can no longer lean on frame-count
    symmetry, which for these three is true by construction."""
    pack, root = _pack()
    sheet = load_sheet(root / "Character_base.png")
    gaps = _profile_mirror_gap(pack, sheet, action, frames)
    assert max(gaps) <= 5, f"{action}: west is not east mirrored: {gaps}"


@pytest.mark.parametrize("action,frames", sorted(DRAWN_TWICE.items()))
def test_the_two_leading_cycles_are_drawn_rather_than_mirrored(action, frames):
    """The other two are separate artwork — the arm the character leads with
    does not swap when it turns around, so a flip would be wrong."""
    pack, root = _pack()
    sheet = load_sheet(root / "Character_base.png")
    gaps = _profile_mirror_gap(pack, sheet, action, frames)
    assert min(gaps) > 5, f"{action}: west is a pixel mirror of east: {gaps}"


def test_west_and_east_carry_the_same_frame_counts():
    """A check on the west/east split, and a weaker one than it looks: for the
    three actions in MIRRORED the two rows are the same frames flipped, so
    symmetry there is true by construction. The load-bearing evidence is the
    iris position above; this only has teeth for run and lead_run."""
    _asset, _sheet, meta = _built()
    by = {(a["facing"], a["name"]): a["frames"] for a in meta["animations"]}
    for action in ACTIONS:
        assert by[("west", action)] == by[("east", action)], action


# --- a second family through the same producer -----------------------------

def test_the_asset_is_not_a_horse():
    _asset, _sheet, meta = _built()
    assert meta["family"] == "character"
    assert meta["id"] == "character_base_afoot"
    assert "tack" not in meta, "a family with no tack axis omits the field"


def test_subjects_and_coats_are_the_same_list_under_two_names():
    pack, _ = _pack()
    assert "subjects" not in pack and pack["coats"]["base"]["label"] == "Farmhand"
    with pytest.raises(ValueError, match="not both"):
        ingest.resolve_modes({"subjects": {}, "coats": {}, "modes": {}})


def test_the_character_validates_against_the_project_schema():
    from pixelasset.config import load_schema, validate_schema
    from pixelasset.paths import ProjectPaths

    _asset, _sheet, meta = _built()
    validate_schema(meta, load_schema(ProjectPaths(ROOT), "metadata"),
                    source="character metadata")


# --- which profile row is which action -------------------------------------

def _row_part(sheet, cw, ch, row, col, y0, y1):
    return sheet[row * ch + y0:row * ch + y1, col * cw:(col + 1) * cw]


def _gait_distance(sheet, cw, ch, a_row, a_n, b_row, b_n):
    """Summed pixel difference of the knees-down half, minimised over cyclic
    frame offsets. Low means two rows animate the same legs."""
    n = min(a_n, b_n)
    return min(
        sum(int(np.any(_row_part(sheet, cw, ch, a_row, c, 24, ch)
                       != _row_part(sheet, cw, ch, b_row, (c + s) % b_n, 24, ch),
                       axis=2).sum())
            for c in range(n))
        for s in range(b_n)
    )


def _stride(sheet, cw, ch, row, frames):
    """Mean horizontal spread of each frame's feet."""
    out = []
    for c in range(frames):
        feet = _row_part(sheet, cw, ch, row, c, ch - 6, ch)[:, :, 3] > 0
        xs = np.nonzero(feet.any(axis=0))[0]
        out.append(xs.max() - xs.min() + 1 if len(xs) else 0)
    return float(np.mean(out))


@pytest.mark.parametrize("facing", ("west", "east"))
def test_a_led_profile_row_animates_the_gait_it_is_named_for(facing):
    """The profile block does not follow the sheet's row order, and reading it
    off that order put `run` and `lead_walk` on each other's rows. The artwork
    says otherwise: a lead animation is its gait with the arm redrawn, so
    lead_walk's legs are walk's legs and lead_run's are run's."""
    pack, root = _pack()
    sheet = load_sheet(root / "Character_base.png")
    cw, ch = pack["sources"]["base"]["cell"]
    blk = pack["modes"]["afoot"]["clips"][facing]
    n = {a: len(detect.row_frames(detect.alpha(sheet), cw, ch, blk[a]["row"]))
         for a in ACTIONS}

    def gap(a, b):
        return _gait_distance(sheet, cw, ch, blk[a]["row"], n[a],
                              blk[b]["row"], n[b])

    same = max(gap("walk", "lead_walk"), gap("run", "lead_run"))
    apart = min(gap("walk", "run"), gap("walk", "lead_run"),
                gap("lead_walk", "run"), gap("lead_walk", "lead_run"))
    # 48 and 151 within a gait against 197 across it, west and east alike.
    # The walk pair is the tight one; the run pair is looser because the two
    # run rows carry different frame counts (6 against 8), so no cyclic offset
    # lines them up exactly. The clusters still do not overlap.
    assert same < apart, (
        f"{facing}: a gait and the led version of it differ by up to {same} px "
        f"in the legs, but different gaits differ by only {apart} — the rows "
        f"are on the wrong actions"
    )


@pytest.mark.parametrize("facing", ("west", "east"))
def test_a_profile_run_strides_further_than_a_profile_walk(facing):
    """The other half of the row map, and the one a player feels: the old
    assignment gave `run` a 6.56 stride against `walk`'s 6.50, so holding the
    run key changed the speed and not the animation."""
    pack, root = _pack()
    sheet = load_sheet(root / "Character_base.png")
    cw, ch = pack["sources"]["base"]["cell"]
    blk = pack["modes"]["afoot"]["clips"][facing]
    a = detect.alpha(sheet)
    stride = {act: _stride(sheet, cw, ch, blk[act]["row"],
                           len(detect.row_frames(a, cw, ch, blk[act]["row"])))
              for act in ACTIONS}
    assert stride["run"] > stride["walk"] * 1.25, stride
    assert stride["lead_run"] > stride["lead_walk"] * 1.25, stride
