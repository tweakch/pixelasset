"""CLI entry points (PIPELINE.md §17)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from pixelasset.config import load_asset_spec, load_project_config, load_schema, validate_schema
from pixelasset.paths import ProjectPaths, find_project_root
from pixelasset.review import read_review, write_review
from pixelasset.stages import (
    STATUS_FAILED,
    STATUS_NOT_APPLICABLE,
    STATUS_PASSED,
    graph_ok,
    run_stage_graph,
    scaffold_spec,
)

STOP_AFTER = {
    "create": "asset_specification",
    "concept": "reference_concept_generation",
    "pixelate": "pixel_art_construction",
    "palette": "palette_quantization_mapping",
    "animate": "animation_construction",
    "validate": "automated_validation",
    "spritesheet": "spritesheet_generation",
}


def _root(args: argparse.Namespace) -> ProjectPaths:
    start = Path(args.root).resolve() if getattr(args, "root", None) else None
    return ProjectPaths(find_project_root(start))


def _print_graph(graph: dict) -> None:
    print(f"asset: {graph['asset']}")
    for stage in graph["stages"]:
        marker = {
            STATUS_PASSED: "PASSED",
            STATUS_FAILED: "FAILED",
            STATUS_NOT_APPLICABLE: "NOT_APPLICABLE",
        }.get(stage["status"], stage["status"])
        extra = f" — {stage['reason']}" if stage.get("reason") else ""
        print(f"  [{stage['index']:02d}/17] {stage['name']}: {marker}{extra}")


def cmd_create(args: argparse.Namespace) -> int:
    paths = _root(args)
    asset_id = args.id
    paths.ensure_asset_dirs(asset_id)
    spec_path = paths.spec_path(asset_id)
    if spec_path.is_file():
        load_asset_spec(paths, asset_id)
        print(f"spec exists: {spec_path}")
        return 0
    spec = scaffold_spec(asset_id, load_project_config(paths))
    validate_schema(spec, load_schema(paths, "asset"), source="scaffold")
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    with spec_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(spec, handle, sort_keys=False)
    print(f"created {spec_path}")
    return 0


def _run_named(args: argparse.Namespace, command: str) -> int:
    paths = _root(args)
    stop = None if command == "build" else STOP_AFTER[command]
    result = run_stage_graph(paths, args.id, stop_after=stop)
    _print_graph(result["graph"])
    if command == "build":
        ok = graph_ok(result["graph"])
    else:
        target = next(s for s in result["graph"]["stages"] if s["id"] == stop)
        ok = target["status"] in {STATUS_PASSED, STATUS_NOT_APPLICABLE}
    if command == "build":
        prod = paths.production_dir(args.id)
        if ok:
            print(f"production: {prod}")
        else:
            print("production: not promoted")
    return 0 if ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    paths = _root(args)
    if args.all:
        ids = paths.list_asset_ids()
        if not ids:
            print("no assets in assets/source/", file=sys.stderr)
            return 2
        rc = 0
        for asset_id in ids:
            args.id = asset_id
            print(f"== build {asset_id} ==")
            rc = max(rc, _run_named(args, "build"))
        return rc
    if not args.id:
        print("pixelasset build requires <id> or --all", file=sys.stderr)
        return 2
    return _run_named(args, "build")


def cmd_review(args: argparse.Namespace) -> int:
    paths = _root(args)
    if args.approve == args.reject:
        current = read_review(paths, args.id)
        if current is None:
            print("no review status")
            return 1
        print(json_dumps(current))
        return 0 if current.get("status") == "approved" else 1
    status = "approved" if args.approve else "rejected"
    record = write_review(
        paths,
        args.id,
        status,
        reason=args.reason or f"cli {status}",
    )
    print(json_dumps(record))
    return 0


def json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixelasset",
        description="Pixel Art Asset Factory pipeline",
    )
    parser.add_argument(
        "--root",
        help="Project root (default: discover from cwd or PIXELASSET_ROOT)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create asset spec + directories")
    create.add_argument("id")
    create.set_defaults(func=cmd_create)

    for name, help_text in [
        ("concept", "Reference / concept generation"),
        ("pixelate", "Pixel-art construction"),
        ("palette", "Palette quantization / mapping"),
        ("animate", "Animation construction"),
        ("validate", "Automated validation"),
        ("spritesheet", "Spritesheet generation"),
    ]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("id")
        cmd.set_defaults(func=lambda a, n=name: _run_named(a, n), command_name=name)

    build = sub.add_parser("build", help="Run the full 17-stage graph")
    build.add_argument("id", nargs="?")
    build.add_argument("--all", action="store_true")
    build.set_defaults(func=cmd_build)

    review = sub.add_parser("review", help="Read or set review/<id>/status.json")
    review.add_argument("id")
    review.add_argument("--approve", action="store_true")
    review.add_argument("--reject", action="store_true")
    review.add_argument("--reason", default="")
    review.set_defaults(func=cmd_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
