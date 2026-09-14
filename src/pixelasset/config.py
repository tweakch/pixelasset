"""Load and validate YAML configs against JSON schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from pixelasset.paths import ProjectPaths

SCHEMA_FILES = {
    "project": "project.schema.json",
    "asset": "asset.schema.json",
    "style_bible": "style_bible.schema.json",
    "palette": "palette.schema.json",
    "metadata": "metadata.schema.json",
    "review": "review.schema.json",
    "stage_graph": "stage_graph.schema.json",
    "validation": "validation.schema.json",
}


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ValueError(f"Empty YAML file: {path}")
    return data


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=False)
        handle.write("\n")


def load_schema(paths: ProjectPaths, name: str) -> dict[str, Any]:
    filename = SCHEMA_FILES[name]
    return load_json(paths.schemas_dir / filename)


def validate_schema(instance: Any, schema: dict[str, Any], *, source: str) -> None:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        messages = "; ".join(
            f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
            for err in errors
        )
        raise jsonschema.ValidationError(f"{source}: {messages}")


def load_project_config(paths: ProjectPaths) -> dict[str, Any]:
    data = load_yaml(paths.project_config)
    validate_schema(data, load_schema(paths, "project"), source=str(paths.project_config))
    return data


def load_style_bible(paths: ProjectPaths) -> dict[str, Any]:
    data = load_yaml(paths.style_bible)
    validate_schema(data, load_schema(paths, "style_bible"), source=str(paths.style_bible))
    return data


def load_palette(
    paths: ProjectPaths, name: str, coat: str | None = None
) -> dict[str, Any]:
    """Load a palette, optionally resolving `roles` to a named coat ramp.

    The ramp replaces `roles` in the returned dict rather than sitting beside
    it, so every downstream consumer — role mapping, palette validation, the
    metadata stage — keeps reading `palette["roles"]` and needs no change.
    """
    path = paths.palette_path(name)
    data = load_yaml(path)
    validate_schema(data, load_schema(paths, "palette"), source=str(path))
    if coat is None:
        return data
    ramps = data.get("coats") or {}
    if coat not in ramps:
        available = ", ".join(sorted(ramps)) or "none"
        raise ValueError(
            f"Palette {name!r} has no coat {coat!r} (available: {available})"
        )
    resolved = dict(data)
    resolved["roles"] = ramps[coat]
    resolved["coat"] = coat
    return resolved


def load_asset_spec(paths: ProjectPaths, asset_id: str) -> dict[str, Any]:
    path = paths.spec_path(asset_id)
    data = load_yaml(path)
    validate_schema(data, load_schema(paths, "asset"), source=str(path))
    if data.get("id") != asset_id:
        raise ValueError(f"Asset spec id {data.get('id')!r} does not match directory {asset_id!r}")
    return data
