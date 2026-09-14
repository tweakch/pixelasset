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
import yaml

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
    pack = yaml.safe_load(PACK_FILE.read_text(encoding="utf-8"))
    root = ROOT / pack["pack"]["root"]
    if not root.is_dir():
        pytest.skip(f"{root} not present (gitignored third-party pack)")
    return pack, root


def _ingested(coat="bay", mode="ride"):
    from pixelasset import ingest
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
