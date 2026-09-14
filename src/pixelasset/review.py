"""Human review state as a file (PIPELINE.md §22)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pixelasset.config import dump_json, load_json, load_schema, validate_schema
from pixelasset.paths import ProjectPaths


def review_path(paths: ProjectPaths, asset_id: str) -> Any:
    return paths.review_dir(asset_id) / "status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def read_review(paths: ProjectPaths, asset_id: str) -> dict[str, Any] | None:
    path = review_path(paths, asset_id)
    if not path.is_file():
        return None
    data = load_json(path)
    validate_schema(data, load_schema(paths, "review"), source=str(path))
    return data


def write_review(
    paths: ProjectPaths,
    asset_id: str,
    status: str,
    *,
    reason: str,
    gates: list[str] | None = None,
) -> dict[str, Any]:
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError(f"invalid review status {status!r}")
    record = {
        "asset": asset_id,
        "status": status,
        "gates": gates or ["palette", "silhouette"],
        "reason": reason,
        "updated_at": utc_now(),
    }
    validate_schema(record, load_schema(paths, "review"), source="review")
    path = review_path(paths, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_json(path, record)
    return record
