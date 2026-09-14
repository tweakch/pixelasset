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
    "coat_palette": "coat_palette.schema.json",
    "marking": "marking.schema.json",
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


def load_coat_palette(paths: ProjectPaths, name: str) -> dict[str, Any]:
    """Load a coat palette for an ingested family, validated.

    Separate from `load_palette` because these are a different thing: a
    measured colour-for-colour correspondence over a third-party sheet, not a
    seven-role style ramp. See coat_palette.schema.json for why they are not
    folded together.

    Validation is split deliberately. The schema enforces shape — rows, hex
    literals, required keys — and this function enforces the one rule the
    schema cannot express: `from` is the key space and must be pairwise
    distinct, while `to` may repeat freely. JSON Schema 2020-12 has no
    unique-by-property keyword, so a duplicate key would otherwise slip
    through, silently collapse the map and lose a step.
    """
    path = paths.palette_path(name)
    data = load_yaml(path)
    validate_schema(data, load_schema(paths, "coat_palette"), source=str(path))

    if data["base"] == data["name"]:
        # A base coat declares the key space AND the role vocabulary, so every
        # row must carry both. Anything less and a derived coat addressing rows
        # by role would resolve against a half-named ladder.
        missing = [r.get("from") or r.get("role") for r in data["map"]
                   if "from" not in r or "role" not in r]
        if missing:
            raise ValueError(
                f"{path}: a base coat declares the roles, so every row needs "
                f"both `from` and `role`. Missing on: {', '.join(map(str, missing))}"
            )
        declared = {r for group in (data.get("roles") or {}).values()
                    for r in group}
        used = {r["role"] for r in data["map"]}
        if declared != used:
            raise ValueError(
                f"{path}: `roles` groups and `map` roles disagree — "
                f"{sorted(used - declared) or 'nothing'} ungrouped, "
                f"{sorted(declared - used) or 'nothing'} grouped but unused"
            )
    else:
        data = _resolve_roles(paths, path, data)

    seen: dict[str, int] = {}
    for row in data["map"]:
        seen[row["from"]] = seen.get(row["from"], 0) + 1
    repeats = sorted(c for c, n in seen.items() if n > 1)
    if repeats:
        raise ValueError(
            f"{path}: duplicate `from` colour(s) {', '.join(repeats)}. "
            f"`from` is the key space and must be distinct; only `to` may "
            f"repeat."
        )
    return data


def _resolve_roles(paths: ProjectPaths, path: Path, data: dict) -> dict:
    """Turn a derived coat's `role` addressing into `from`, via its base.

    Authoring by intent and diffing by colour want different things from the
    same file — a human writes "MANE_LIGHT", a reviewer wants to see which bay
    colour moved. Resolving here means the file can be written either way and
    everything downstream still reads `from`, so `mapping_for`, the ordering
    check and the extractor are all untouched.
    """
    base = load_coat_palette(paths, data["base"])
    by_role = {r["role"]: r["from"] for r in base["map"]}

    resolved = dict(data)
    rows = []
    for row in data["map"]:
        if "role" in row and "from" in row:
            # Both is allowed and useful — an extracted coat carries the role
            # so it reads as more than hex — but it is checked rather than
            # trusted, because two addressings for one row can disagree.
            expected = by_role.get(row["role"])
            if expected != row["from"]:
                raise ValueError(
                    f"{path}: row says role {row['role']} is {row['from']}, "
                    f"but {data['base']} pairs that role with {expected}"
                )
            rows.append(row)
            continue
        if "role" in row:
            if row["role"] not in by_role:
                known = ", ".join(sorted(by_role))
                raise ValueError(
                    f"{path}: unknown role {row['role']!r} for base "
                    f"{data['base']!r} (known: {known})"
                )
            row = {**row, "from": by_role[row["role"]]}
        rows.append(row)
    resolved["map"] = rows
    return resolved


def load_marking(paths: ProjectPaths, name: str) -> dict[str, Any]:
    """Load a marking descriptor, validated.

    The descriptor does not define the marking — `coat.derive_marking` does,
    from the pack's own sheets. This is the §19 record of what that derivation
    found, and the tests compare the two so a swapped pack is noticed.
    """
    path = paths.root / "config" / "markings" / f"{name}.yaml"
    data = load_yaml(path)
    validate_schema(data, load_schema(paths, "marking"), source=str(path))
    return data


def load_asset_spec(paths: ProjectPaths, asset_id: str) -> dict[str, Any]:
    path = paths.spec_path(asset_id)
    data = load_yaml(path)
    validate_schema(data, load_schema(paths, "asset"), source=str(path))
    if data.get("id") != asset_id:
        raise ValueError(f"Asset spec id {data.get('id')!r} does not match directory {asset_id!r}")
    return data
