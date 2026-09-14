"""The production manifest: what has been promoted, for consumers to enumerate.

There are two producers — the Path C stage graph and `pixelasset.ingest` — and
they run independently over different subsets. So this upserts one entry at a
time and never rewrites the file wholesale: an earlier version had ingest write
the whole index, which meant a `pixelasset build` was invisible and a partial
ingest would have deleted everything else.

Entries are derived from metadata rather than passed in, so the manifest cannot
drift from the asset it describes.
"""

from __future__ import annotations

import json
from typing import Any

from . import __version__
from .paths import ProjectPaths

INDEX_NAME = "index.json"


def index_path(paths: ProjectPaths):
    return paths.assets_dir / "production" / INDEX_NAME


def entry_for(meta: dict[str, Any]) -> dict[str, Any]:
    """Summarise one asset for a consumer that has not loaded it yet."""
    facings = sorted({a["facing"] for a in meta["animations"] if a.get("facing")})
    return {
        "id": meta["id"],
        "family": meta.get("family"),
        "variant": meta.get("variant"),
        "label": meta.get("name") or meta["id"],
        "category": meta.get("category"),
        # `mode` is what makes an asset usable as a playable coat: it has a
        # riding or leading sheet. A single-facing idle has none, and the game's
        # coat cycle filters on exactly this so a prop cannot break it.
        "mode": meta.get("mode"),
        # Two assets of one coat can share a mode and differ only in tack, so a
        # consumer keying purely on `mode` would quietly keep whichever it saw
        # last. Carried here so the index is enough to build the pairing from.
        "tack": meta.get("tack"),
        "frame_size": meta["frame_size"],
        "frame_count": meta["frame_count"],
        "animations": len(meta["animations"]),
        "facings": facings,
        "layout": (meta.get("spritesheet") or {}).get("layout", "single_strip"),
    }


def register(paths: ProjectPaths, meta: dict[str, Any]) -> dict[str, Any]:
    path = index_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pipeline_version": __version__, "assets": []}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass                      # a corrupt index is rebuilt, not fatal
    entry = entry_for(meta)
    assets = [a for a in data.get("assets", []) if a.get("id") != entry["id"]]
    assets.append(entry)
    assets.sort(key=lambda a: a["id"])
    data["pipeline_version"] = __version__
    data["assets"] = assets
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return entry


def unregister(paths: ProjectPaths, asset_id: str) -> None:
    """Drop an asset that failed its gates, so the manifest never advertises
    something production/ no longer holds."""
    path = index_path(paths)
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    kept = [a for a in data.get("assets", []) if a.get("id") != asset_id]
    if len(kept) != len(data.get("assets", [])):
        data["assets"] = kept
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
