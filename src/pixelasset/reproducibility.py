"""Reproducibility record (PIPELINE.md §19)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pixelasset.review import utc_now


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_record(
    *,
    spec: dict[str, Any],
    project: dict[str, Any],
    palette: dict[str, Any],
    style: dict[str, Any],
    generator_name: str | None,
    source_hash: str,
    concept_hash: str | None,
    seed: int | None,
) -> dict[str, Any]:
    versions = project["versions"]
    return {
        "asset_id": spec["id"],
        "pipeline_version": versions["pipeline"],
        "style_version": style.get("version") or versions["style"],
        "palette_version": palette.get("version") or versions["palette"],
        "asset_version": versions["asset"],
        "construction_path": spec["construction_path"],
        "generator": generator_name,
        "model": project.get("generation", {}).get("model"),
        "prompt": None,
        "seed": seed,
        "concept_hash": concept_hash,
        "source_hash": source_hash,
        "created_at": utc_now(),
    }
