"""The metadata contract for directional animated assets.

Slice 1 and 2 describe a single non-directional clip packed as one horizontal
strip. A character with four facings and several gaits needs three more things,
and each is here because a consumer cannot work without it:

    spritesheet {columns, rows, layout}   how to index the packed sheet
    animations[].facing                   which way a clip faces
    animations[].off_ground               inclusive frames off the ground

All three are optional, so a static prop stays valid without them. These tests
pin both halves of that: the extension works, and it did not break hay_bale.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from pixelasset import ingest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas/metadata.schema.json").read_text())
PACK_FILE = ROOT / "packs/full_pack.yaml"

MINIMAL = {
    "id": "prop", "frame_size": [24, 16], "frame_count": 1,
    "animations": [{"name": "static", "frames": 1, "fps": 1, "loop": False,
                    "order": ["static/00.png"]}],
    "anchors": {"origin": "bottom_center", "points": {"feet": [12, 15]}},
    "palette_version": "palette_v0", "pipeline_version": "0.1.0",
    "construction_path": "C",
}


def test_static_prop_needs_none_of_the_new_fields():
    jsonschema.validate(MINIMAL, SCHEMA)


def test_shipped_hay_bale_metadata_still_validates():
    built = ROOT / "assets/production/hay_bale/metadata.json"
    if not built.is_file():
        pytest.skip("hay_bale not built; run `pixelasset build hay_bale`")
    jsonschema.validate(json.loads(built.read_text()), SCHEMA)


def test_directional_animated_asset_validates():
    doc = json.loads(json.dumps(MINIMAL))
    doc.update({
        "frame_count": 17,
        "spritesheet": {"file": "spritesheet.png", "columns": 9,
                        "rows": 2, "layout": "row_per_animation"},
        "animations": [
            {"name": "walk", "facing": "east", "row": 0, "frames": 9,
             "fps": 9, "loop": True,
             "order": [f"walk_east/{i:02d}.png" for i in range(9)]},
            {"name": "jump", "facing": "north", "row": 1, "frames": 8,
             "fps": 14, "loop": False, "off_ground": [2, 4],
             "order": [f"jump_north/{i:02d}.png" for i in range(8)]},
        ],
    })
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.parametrize("field,value", [
    ("facing", "northeast"),          # only the four cardinal facings exist
    ("off_ground", [2]),              # must be an inclusive pair
    ("row", -1),
])
def test_bad_animation_fields_are_rejected(field, value):
    doc = json.loads(json.dumps(MINIMAL))
    doc["animations"][0][field] = value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_typos_in_animation_fields_are_rejected():
    """`additionalProperties: false` so `offground` fails loudly rather than
    being silently ignored and leaving collision permanently disabled."""
    doc = json.loads(json.dumps(MINIMAL))
    doc["animations"][0]["offground"] = [2, 4]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_unsupported_layout_is_rejected():
    doc = json.loads(json.dumps(MINIMAL))
    doc["spritesheet"] = {"file": "s.png", "columns": 1, "rows": 1,
                          "layout": "diagonal"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


# --- end to end, against the real pack -------------------------------------
#
# These need samples/Full_Pack, which is gitignored third-party art, so they
# skip when it is absent rather than failing. Everything above runs anywhere.

def _pack():
    if not PACK_FILE.is_file():
        pytest.skip("no pack description")
    pack = ingest.load_pack(PACK_FILE)
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip(f"{root} not present (gitignored third-party pack)")
    return pack, root


def _ingested(coat="bay", mode="ride"):
    pack, root = _pack()
    asset = ingest.build_asset(pack, root, coat, mode)
    ingest.render_sheet(asset)          # assigns row indices
    return asset, ingest.metadata(asset)


def test_ingested_asset_matches_the_schema():
    _, meta = _ingested()
    jsonschema.validate(meta, SCHEMA)


def test_rows_are_unique_and_fit_the_declared_sheet():
    _, meta = _ingested()
    sheet = meta["spritesheet"]
    rows = [a["row"] for a in meta["animations"]]
    assert sorted(rows) == list(range(sheet["rows"])), "rows must be dense and unique"
    assert max(a["frames"] for a in meta["animations"]) == sheet["columns"]


def test_off_ground_indices_are_inside_the_clip():
    _, meta = _ingested()
    tagged = [a for a in meta["animations"] if "off_ground" in a]
    assert tagged, "the mounted asset has jump clips; none were tagged"
    for anim in tagged:
        first, last = anim["off_ground"]
        assert 0 <= first <= last < anim["frames"], anim["name"]


def test_order_length_matches_frame_count():
    for mode in ("ride", "lead"):
        _, meta = _ingested(mode=mode)
        for anim in meta["animations"]:
            assert len(anim["order"]) == anim["frames"], f"{mode} {anim['name']}"
        assert meta["frame_count"] == sum(a["frames"] for a in meta["animations"])


# --- provenance ------------------------------------------------------------
#
# `source` is the fourth contract extension and the only one nothing needs in
# order to draw. It is here so a defect can be reported against the artwork
# rather than against the packed output: "frame 3 of walk west is wrong" names
# a picture, and "row 6 column 3 of Character_base.png" names something a
# producer can go and fix. The animation debugger prints it under every frame.
#
# Which makes the only interesting property its exactness. A provenance field
# that is approximately right is worse than none at all.

def test_every_ingested_clip_says_where_it_came_from():
    for pack_file in ("packs/full_pack.yaml", "packs/character.yaml"):
        pack = ingest.load_pack(ROOT / pack_file)
        root = ROOT / pack["pack"]["root"]
        if not root.is_dir():
            pytest.skip(f"{root} not present (gitignored third-party pack)")
        subjects = pack.get("coats") or pack["subjects"]
        mode = sorted(pack["modes"])[0]
        asset = ingest.build_asset(pack, root, sorted(subjects)[0], mode)
        ingest.render_sheet(asset)
        meta = ingest.metadata(asset)
        jsonschema.validate(meta, SCHEMA)
        for anim in meta["animations"]:
            src = anim["source"]
            assert (ROOT / src["sheet"]).is_file(), src["sheet"]
            assert src["row"] >= 0
            assert len(src["columns"]) == anim["frames"]
            assert src["columns"] == sorted(set(src["columns"])), src["columns"]


def test_the_source_of_a_frame_is_that_frame():
    """The claim, checked against pixels rather than against the code that
    wrote it: every frame of the built sheet is byte-identical to the source
    cell its own metadata names, once both are cropped to the content. They
    cannot be compared whole — ingest re-blits onto one common cell with one
    origin, so the padding around the subject is exactly what changed.

    Two fields earn their keep here. `columns` is a list because `lead` is not
    consecutive everywhere, and under a first-plus-count contract every frame
    after a gap would name the picture next to the one it is. `patched` is the
    exception list: those frames deliberately do NOT match the raw sheet, and
    this checks that the set of frames that differ is exactly the set that says
    it will — a correction that leaked into a second frame fails here."""
    import numpy as np
    from pixelasset.coat import load_sheet

    pack, root = _pack()
    # Source specs by the path metadata names them under, so a sheet is read
    # through the same loader the producer used, corrections and all.
    spec_for = {}
    for src, spec in pack["sources"].items():
        for coat in pack["coats"].values():
            if src in coat["files"]:
                spec_for[str(root / spec["dir"] / coat["files"][src])] = spec

    asset = ingest.build_asset(pack, root, "bay", "lead")
    sheet = np.array(ingest.render_sheet(asset).convert("RGBA"))
    meta = ingest.metadata(asset)
    cw, ch = meta["frame_size"]
    raw, fixed = {}, {}

    def content(a):
        """The visible subject, cropped to its alpha bounding box and with the
        RGB under transparent pixels zeroed.

        Both halves matter. The crop is because ingest re-blits onto one common
        cell, so the padding is exactly what it changed. The zeroing is because
        this pack's PNGs carry colour underneath fully transparent pixels — the
        coat renderer keeps that in a separate channel to stay byte-identical,
        and a re-blit does not, so comparing it here would be asserting
        something neither producer promises. What must match is what you can
        see, which is every pixel with alpha."""
        opaque = a[:, :, 3] > 0
        ys, xs = np.nonzero(opaque)
        out = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
        out[out[:, :, 3] == 0] = 0
        return out

    checked = 0
    differs_from_raw = set()
    declared = set()
    for anim in meta["animations"]:
        src = anim["source"]
        path = str(ROOT / src["sheet"])
        if path not in raw:
            raw[path] = load_sheet(ROOT / src["sheet"])
            fixed[path] = ingest.load_source_sheet(ROOT / src["sheet"],
                                                   spec_for[path])
        sw, sh = src["cell"]
        for i in src.get("patched", []):
            declared.add((anim["facing"], anim["name"], i))
        for i in range(anim["frames"]):
            built = sheet[anim["row"] * ch:(anim["row"] + 1) * ch,
                          i * cw:(i + 1) * cw]
            col = src["columns"][i]
            box = (slice(src["row"] * sh, (src["row"] + 1) * sh),
                   slice(col * sw, (col + 1) * sw))
            assert np.array_equal(content(built), content(fixed[path][box])), (
                f"{anim['name']}/{anim['facing']} frame {i} is not "
                f"{src['sheet']} row {src['row']} col {col}"
            )
            if not np.array_equal(raw[path][box], fixed[path][box]):
                differs_from_raw.add((anim["facing"], anim["name"], i))
            checked += 1
    assert checked > 100, checked
    assert differs_from_raw == declared, (
        f"frames that differ from the raw sheet: {sorted(differs_from_raw)}, "
        f"frames declaring `patched`: {sorted(declared)}"
    )
    assert declared, "the leading sheet carries a correction; it should be listed"


def test_provenance_is_optional():
    """The stage graph has no single source row per clip and emits none, so a
    consumer must treat the field as absent-able. `hay_bale` is the case."""
    shipped = ROOT / "assets/production/hay_bale/metadata.json"
    if not shipped.is_file():
        pytest.skip("hay_bale not built")
    meta = json.loads(shipped.read_text())
    jsonschema.validate(meta, SCHEMA)
    assert all("source" not in a for a in meta["animations"])
