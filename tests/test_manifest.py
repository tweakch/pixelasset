"""The production manifest.

Two producers write it — the Path C stage graph and `pixelasset.ingest` — over
different subsets, independently. So the thing worth pinning is that neither
can erase the other's work, which a wholesale rewrite did before this existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixelasset import manifest
from pixelasset.paths import ProjectPaths

ROOT = Path(__file__).resolve().parent.parent


def _meta(asset_id: str, *, mode=None, facings=(), frames=1):
    anims = []
    for facing in (facings or [None]):
        anim = {"name": "idle", "frames": frames, "fps": 4, "loop": True,
                "order": [f"idle/{i:02d}.png" for i in range(frames)]}
        if facing:
            anim["facing"] = facing
        anims.append(anim)
    meta = {"id": asset_id, "name": asset_id, "family": "horse",
            "variant": asset_id, "category": "character",
            "frame_size": [48, 32], "frame_count": frames * len(anims),
            "animations": anims}
    if mode:
        meta["mode"] = mode
    return meta


@pytest.fixture
def paths(tmp_path):
    return ProjectPaths(tmp_path)


def test_register_creates_the_index(paths):
    manifest.register(paths, _meta("horse_fox"))
    data = json.loads(manifest.index_path(paths).read_text())
    assert [a["id"] for a in data["assets"]] == ["horse_fox"]


def test_producers_do_not_clobber_each_other(paths):
    """The stage graph and ingest run over different subsets. An earlier index
    was rewritten wholesale by ingest, which made every `pixelasset build`
    invisible — exactly the bug that hid horse_fox from the game."""
    manifest.register(paths, _meta("horse_fox"))                       # graph
    manifest.register(paths, _meta("horse_bay_ride", mode="ride",
                                   facings=("east", "west")))          # ingest
    manifest.register(paths, _meta("hay_bale"))                        # graph
    ids = [a["id"] for a in json.loads(manifest.index_path(paths).read_text())["assets"]]
    assert ids == ["hay_bale", "horse_bay_ride", "horse_fox"], "sorted, none lost"


def test_register_is_an_upsert(paths):
    manifest.register(paths, _meta("horse_fox", frames=1))
    manifest.register(paths, _meta("horse_fox", frames=5))
    assets = json.loads(manifest.index_path(paths).read_text())["assets"]
    assert len(assets) == 1
    assert assets[0]["frame_count"] == 5


def test_unregister_removes_a_held_back_asset(paths):
    """A failed gate must not leave the manifest advertising something
    production/ no longer holds (§20)."""
    manifest.register(paths, _meta("horse_fox"))
    manifest.register(paths, _meta("hay_bale"))
    manifest.unregister(paths, "horse_fox")
    ids = [a["id"] for a in json.loads(manifest.index_path(paths).read_text())["assets"]]
    assert ids == ["hay_bale"]


def test_unregister_is_harmless_when_absent(paths):
    manifest.unregister(paths, "never_built")       # no index file at all
    manifest.register(paths, _meta("hay_bale"))
    manifest.unregister(paths, "never_built")
    assert len(json.loads(manifest.index_path(paths).read_text())["assets"]) == 1


def test_corrupt_index_is_rebuilt_not_fatal(paths):
    path = manifest.index_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    manifest.register(paths, _meta("hay_bale"))
    assert [a["id"] for a in json.loads(path.read_text())["assets"]] == ["hay_bale"]


def test_entry_distinguishes_playable_from_prop(paths):
    """`mode` is how a consumer tells a rideable coat from a single-facing
    idle. The game's coat cycle filters on it, so a prop cannot break it."""
    prop = manifest.entry_for(_meta("horse_fox"))
    playable = manifest.entry_for(_meta("horse_bay_ride", mode="ride",
                                        facings=("east", "west", "south", "north")))
    assert prop["mode"] is None and prop["facings"] == []
    assert playable["mode"] == "ride" and len(playable["facings"]) == 4
    assert prop["layout"] == "single_strip"


def test_shipped_index_lists_both_producers():
    """Integration: the real manifest carries stage-graph and ingested assets."""
    path = manifest.index_path(ProjectPaths(ROOT))
    if not path.is_file():
        pytest.skip("nothing built yet")
    ids = {a["id"] for a in json.loads(path.read_text())["assets"]}
    assert "horse_fox" in ids, "stage-graph asset missing from the manifest"
    assert any(i.endswith("_ride") for i in ids), "ingested asset missing"
