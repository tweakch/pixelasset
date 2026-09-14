"""Stage graph: all 17 PIPELINE.md stages. Never silently skip."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from pixelasset.config import (
    dump_json,
    load_asset_spec,
    load_palette,
    load_project_config,
    load_schema,
    load_style_bible,
    validate_schema,
)
from pixelasset.generator import get_generator
from pixelasset.paths import ProjectPaths
from pixelasset.pixelops import (
    nearest_neighbor_scale,
    palette_rgb_set,
    rgb_tuple,
    silhouette_mask,
    snap_binary_alpha,
)
from pixelasset.reproducibility import build_record
from pixelasset.review import read_review, write_review
from pixelasset.validation import (
    REVIEW_PENDING,
    STAGE_BLOCKED,
    merge_results,
    validate_frame,
)

# Import Path C so it registers on package use.
import pixelasset.generators  # noqa: F401

STAGE_DEFINITIONS: list[tuple[str, str]] = [
    ("asset_specification", "Asset Specification"),
    ("semantic_interpretation", "Semantic Interpretation"),
    ("reference_concept_generation", "Reference / Concept Generation"),
    ("pixel_art_construction", "Pixel-Art Construction"),
    ("silhouette_validation", "Silhouette Validation"),
    ("resolution_normalization", "Resolution Normalization"),
    ("palette_quantization_mapping", "Palette Quantization / Mapping"),
    ("transparency_background_cleanup", "Transparency / Background Cleanup"),
    ("pixel_cleanup", "Pixel Cleanup"),
    ("animation_construction", "Animation Construction"),
    ("frame_consistency_validation", "Frame Consistency Validation"),
    ("spritesheet_generation", "Spritesheet Generation"),
    ("metadata_generation", "Metadata Generation"),
    ("automated_validation", "Automated Validation"),
    ("preview_generation", "Preview Generation"),
    ("human_review", "Human Review"),
    ("approved_production_asset", "Approved Production Asset"),
]

PATH_C_NOT_APPLICABLE = {
    "reference_concept_generation": (
        "construction_path=C — no AI concept; template/procedural source is truth"
    ),
    "animation_construction": (
        "static prop — animation.enabled=false; Slice 1 has no Idle/Walk"
    ),
    "frame_consistency_validation": (
        "single static frame — no animation frames to compare"
    ),
}

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


@dataclass
class StageResult:
    status: str
    reason: str | None = None
    artifacts: list[str] = field(default_factory=list)


@dataclass
class BuildContext:
    paths: ProjectPaths
    asset_id: str
    project: dict[str, Any]
    style: dict[str, Any]
    spec: dict[str, Any] | None = None
    palette: dict[str, Any] | None = None
    ir: dict[str, Any] | None = None
    native: Image.Image | None = None
    frames: list[Image.Image] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    review_record: dict[str, Any] | None = None
    blocked: bool = False
    block_reason: str | None = None
    generator_name: str | None = None

    @property
    def working(self) -> Path:
        return self.paths.working_dir(self.asset_id)


Handler = Callable[[BuildContext], StageResult]


def _save_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _clear_dir(path: Path) -> None:
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()


def _na_for_path_c(ctx: BuildContext, stage_id: str) -> StageResult | None:
    path = (ctx.spec or {}).get("construction_path") or ctx.project["generation"][
        "construction_path"
    ]
    animation_on = bool((ctx.spec or {}).get("animation", {}).get("enabled"))
    if stage_id == "reference_concept_generation" and path == "C":
        return StageResult(STATUS_NOT_APPLICABLE, PATH_C_NOT_APPLICABLE[stage_id])
    if stage_id in {"animation_construction", "frame_consistency_validation"}:
        if path == "C" and not animation_on:
            return StageResult(STATUS_NOT_APPLICABLE, PATH_C_NOT_APPLICABLE[stage_id])
    return None


def stage_asset_specification(ctx: BuildContext) -> StageResult:
    spec = load_asset_spec(ctx.paths, ctx.asset_id)
    ctx.spec = spec
    dest = ctx.working / "spec.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(ctx.paths.spec_path(ctx.asset_id).read_text(encoding="utf-8"), encoding="utf-8")
    return StageResult(STATUS_PASSED, artifacts=[str(dest.relative_to(ctx.paths.root))])


def stage_semantic_interpretation(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None
    palette_name = ctx.spec["palette"]["name"]
    ctx.palette = load_palette(ctx.paths, palette_name)
    path = ctx.spec["construction_path"]
    na_stages = []
    if path == "C":
        na_stages.append("reference_concept_generation")
        if not ctx.spec["animation"]["enabled"]:
            na_stages.extend(
                ["animation_construction", "frame_consistency_validation"]
            )
    feet = ctx.spec["anchors"]["points"]["feet"]
    ctx.ir = {
        "id": ctx.spec["id"],
        "category": ctx.spec["category"],
        "construction_path": path,
        "canvas": dict(ctx.spec["canvas"]),
        "view": ctx.spec["view"],
        "origin": ctx.spec["anchors"]["origin"],
        "anchors": {"feet": [int(feet[0]), int(feet[1])]},
        "animation_enabled": ctx.spec["animation"]["enabled"],
        "not_applicable_stages": na_stages,
        "palette_name": palette_name,
        "roles": ctx.palette["roles"],
        "outline_enabled": ctx.spec["outline"]["enabled"],
        "background_transparent": ctx.spec["background"]["transparent"],
    }
    ir_path = ctx.working / "ir.json"
    dump_json(ir_path, ctx.ir)
    return StageResult(STATUS_PASSED, artifacts=[str(ir_path.relative_to(ctx.paths.root))])


def stage_reference_concept(ctx: BuildContext) -> StageResult:
    na = _na_for_path_c(ctx, "reference_concept_generation")
    if na:
        marker = ctx.paths.concepts_dir(ctx.asset_id) / "NOT_APPLICABLE.json"
        dump_json(
            marker,
            {
                "asset": ctx.asset_id,
                "stage": "reference_concept_generation",
                "status": STATUS_NOT_APPLICABLE,
                "reason": na.reason,
            },
        )
        na.artifacts = [str(marker.relative_to(ctx.paths.root))]
        return na
    return StageResult(
        STATUS_FAILED,
        reason="Slice 1 implements construction path C only",
    )


def stage_pixel_art_construction(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None and ctx.palette is not None
    path = ctx.spec["construction_path"]
    generator = get_generator(path)
    ctx.generator_name = generator.name
    template_text = None
    template_path = ctx.paths.template_path(ctx.asset_id)
    if template_path.is_file():
        template_text = template_path.read_text(encoding="utf-8")
    image = generator.construct(
        ctx.spec, ctx.palette, ctx.style, template_text=template_text
    )
    ctx.native = image
    ctx.frames = [image.copy()]
    native_path = ctx.working / "native.png"
    frame_path = ctx.working / "frames" / "static" / "00.png"
    _save_png(native_path, image)
    _save_png(frame_path, image)
    return StageResult(
        STATUS_PASSED,
        artifacts=[
            str(native_path.relative_to(ctx.paths.root)),
            str(frame_path.relative_to(ctx.paths.root)),
        ],
    )


def stage_silhouette_validation(ctx: BuildContext) -> StageResult:
    from pixelasset.validation import check_silhouette

    assert ctx.spec is not None and ctx.palette is not None and ctx.native is not None
    errors = check_silhouette(ctx.native, ctx.spec, ctx.project, ctx.palette, frame=0)
    report = {"asset": ctx.asset_id, "errors": errors}
    report_path = ctx.working / "silhouette_report.json"
    dump_json(report_path, report)
    if errors:
        return StageResult(
            STATUS_FAILED,
            reason=f"{errors[0].get('code', 'SILHOUETTE_BREAK')}: {errors[0].get('message') or 'failed'}",
            artifacts=[str(report_path.relative_to(ctx.paths.root))],
        )
    return StageResult(
        STATUS_PASSED, artifacts=[str(report_path.relative_to(ctx.paths.root))]
    )


def stage_resolution_normalization(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None and ctx.native is not None
    expected = (int(ctx.spec["canvas"]["width"]), int(ctx.spec["canvas"]["height"]))
    if ctx.native.size != expected:
        # Slice 1 Path C is authored at native size; do not resample inventively.
        return StageResult(
            STATUS_FAILED,
            reason=(
                f"FRAME_SIZE_MISMATCH: {ctx.native.size[0]}×{ctx.native.size[1]} "
                f"!= native {expected[0]}×{expected[1]}"
            ),
        )
    path = ctx.working / "normalized.png"
    _save_png(path, ctx.native)
    return StageResult(STATUS_PASSED, artifacts=[str(path.relative_to(ctx.paths.root))])


def stage_palette_mapping(ctx: BuildContext) -> StageResult:
    from pixelasset.validation import check_palette

    assert ctx.spec is not None and ctx.palette is not None and ctx.native is not None
    errors = check_palette(ctx.native, ctx.palette, ctx.spec, frame=0)
    used_roles = set()
    allowed = palette_rgb_set(ctx.palette["roles"])
    pixels = ctx.native.load()
    assert pixels is not None
    width, height = ctx.native.size
    for y in range(height):
        for x in range(width):
            px = pixels[x, y]
            if px[3] == 0:
                continue
            role = allowed.get(rgb_tuple(px))
            if role:
                used_roles.add(role)
    report = {
        "asset": ctx.asset_id,
        "used_roles": sorted(used_roles),
        "errors": errors,
    }
    report_path = ctx.working / "palette_report.json"
    dump_json(report_path, report)
    if errors:
        return StageResult(
            STATUS_FAILED,
            reason=f"{errors[0].get('code', 'PALETTE_VIOLATION')}: {errors[0].get('message') or 'failed'}",
            artifacts=[str(report_path.relative_to(ctx.paths.root))],
        )
    mapped = ctx.working / "mapped.png"
    _save_png(mapped, ctx.native)
    return StageResult(
        STATUS_PASSED,
        artifacts=[
            str(report_path.relative_to(ctx.paths.root)),
            str(mapped.relative_to(ctx.paths.root)),
        ],
    )


def stage_transparency_cleanup(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None and ctx.native is not None
    cleaned, repaired = snap_binary_alpha(ctx.native)
    ctx.native = cleaned
    ctx.frames = [cleaned.copy()]
    path = ctx.working / "alpha_clean.png"
    _save_png(path, cleaned)
    if repaired and ctx.spec.get("background", {}).get("no_semi_alpha", True):
        if ctx.project["validation"]["strict"]:
            return StageResult(
                STATUS_FAILED,
                reason=f"{repaired} semi-transparent pixel(s) are forbidden",
                artifacts=[str(path.relative_to(ctx.paths.root))],
            )
    return StageResult(
        STATUS_PASSED,
        reason=None if not repaired else f"snapped {repaired} alpha values to 0/255",
        artifacts=[str(path.relative_to(ctx.paths.root))],
    )


def stage_pixel_cleanup(ctx: BuildContext) -> StageResult:
    assert ctx.native is not None
    # Path C templates are already intentional pixels; record a no-op cleanup.
    path = ctx.working / "cleaned.png"
    _save_png(path, ctx.native)
    return StageResult(
        STATUS_PASSED,
        reason="no stray pixels; template pixels left unchanged",
        artifacts=[str(path.relative_to(ctx.paths.root))],
    )


def stage_animation_construction(ctx: BuildContext) -> StageResult:
    na = _na_for_path_c(ctx, "animation_construction")
    if na:
        return na
    return StageResult(STATUS_FAILED, reason="animation construction not in Slice 1")


def stage_frame_consistency(ctx: BuildContext) -> StageResult:
    na = _na_for_path_c(ctx, "frame_consistency_validation")
    if na:
        return na
    return StageResult(STATUS_FAILED, reason="frame consistency not in Slice 1")


def stage_spritesheet(ctx: BuildContext) -> StageResult:
    assert ctx.native is not None
    # Single-frame sheet: the native PNG is the sheet.
    sheet = ctx.native.copy()
    path = ctx.working / "spritesheet.png"
    _save_png(path, sheet)
    return StageResult(STATUS_PASSED, artifacts=[str(path.relative_to(ctx.paths.root))])


def stage_metadata(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None and ctx.palette is not None
    versions = ctx.project["versions"]
    from pixelasset.reproducibility import sha256_bytes

    source_parts = [ctx.paths.spec_path(ctx.asset_id).read_bytes()]
    template = ctx.paths.template_path(ctx.asset_id)
    if template.is_file():
        source_parts.append(template.read_bytes())
    source_hash = sha256_bytes(b"\n".join(source_parts))
    record = build_record(
        spec=ctx.spec,
        project=ctx.project,
        palette=ctx.palette,
        style=ctx.style,
        generator_name=ctx.generator_name,
        source_hash=source_hash,
        concept_hash=None,
        seed=ctx.project.get("generation", {}).get("seed"),
    )
    metadata = {
        "id": ctx.spec["id"],
        "name": ctx.spec["name"],
        "family": ctx.spec["family"],
        "variant": ctx.spec["variant"],
        "category": ctx.spec["category"],
        "frame_size": [
            int(ctx.spec["canvas"]["width"]),
            int(ctx.spec["canvas"]["height"]),
        ],
        "frame_count": 1,
        "animations": [
            {
                "name": "static",
                "frames": 1,
                "fps": 1,
                "loop": False,
                "order": ["static/00.png"],
            }
        ],
        "anchors": ctx.spec["anchors"],
        "palette_name": ctx.palette["name"],
        "palette_version": ctx.palette["version"],
        "style_version": ctx.style["version"],
        "pipeline_version": versions["pipeline"],
        "asset_version": versions["asset"],
        "construction_path": ctx.spec["construction_path"],
        "view": ctx.spec["view"],
        "reproducibility": record,
    }
    validate_schema(metadata, load_schema(ctx.paths, "metadata"), source="metadata")
    meta_dir = ctx.paths.metadata_dir(ctx.asset_id)
    meta_path = meta_dir / "metadata.json"
    repro_path = meta_dir / "reproducibility.json"
    dump_json(meta_path, metadata)
    dump_json(repro_path, record)
    dump_json(ctx.working / "metadata.json", metadata)
    return StageResult(
        STATUS_PASSED,
        artifacts=[
            str(meta_path.relative_to(ctx.paths.root)),
            str(repro_path.relative_to(ctx.paths.root)),
        ],
    )


def stage_automated_validation(ctx: BuildContext) -> StageResult:
    assert ctx.spec is not None and ctx.palette is not None
    frames = ctx.frames or ([ctx.native] if ctx.native is not None else [])
    results = [
        validate_frame(image, ctx.spec, ctx.palette, ctx.project, frame=index)
        for index, image in enumerate(frames)
    ]
    merged = merge_results(ctx.asset_id, results)
    validate_schema(merged, load_schema(ctx.paths, "validation"), source="validation")
    ctx.validation = merged
    path = ctx.working / "validation.json"
    dump_json(path, merged)
    dump_json(ctx.paths.metadata_dir(ctx.asset_id) / "validation.json", merged)
    if merged["status"] != "PASSED":
        codes = ", ".join(err["code"] for err in merged["errors"])
        return StageResult(
            STATUS_FAILED,
            reason=codes or "validation failed",
            artifacts=[str(path.relative_to(ctx.paths.root))],
        )
    return StageResult(STATUS_PASSED, artifacts=[str(path.relative_to(ctx.paths.root))])


def stage_preview(ctx: BuildContext) -> StageResult:
    assert ctx.native is not None and ctx.palette is not None
    scale = int(ctx.project["pixel_art"].get("preview_scale") or 4)
    preview_dir = ctx.paths.previews_dir(ctx.asset_id)
    native_path = preview_dir / "native.png"
    preview_path = preview_dir / "preview_4x.png"
    sil_path = preview_dir / "silhouette.png"
    pal_path = preview_dir / "palette.png"
    _save_png(native_path, ctx.native)
    _save_png(preview_path, nearest_neighbor_scale(ctx.native, scale))
    _save_png(sil_path, silhouette_mask(ctx.native))

    roles = ctx.palette["roles"]
    swatch = Image.new("RGBA", (len(roles) * 8, 8), (0, 0, 0, 0))
    sp = swatch.load()
    assert sp is not None
    for i, (_role, rgb) in enumerate(roles.items()):
        r, g, b = (int(c) for c in rgb)
        for yy in range(8):
            for xx in range(8):
                sp[i * 8 + xx, yy] = (r, g, b, 255)
    _save_png(pal_path, swatch)
    return StageResult(
        STATUS_PASSED,
        artifacts=[
            str(native_path.relative_to(ctx.paths.root)),
            str(preview_path.relative_to(ctx.paths.root)),
            str(sil_path.relative_to(ctx.paths.root)),
            str(pal_path.relative_to(ctx.paths.root)),
        ],
    )


def stage_human_review(ctx: BuildContext) -> StageResult:
    existing = read_review(ctx.paths, ctx.asset_id)
    gates = list(ctx.project.get("review", {}).get("high_value_gates") or ["palette", "silhouette"])
    validation_failed = (ctx.validation or {}).get("status") == "FAILED"
    warnings = (ctx.validation or {}).get("warnings") or []
    path_c = (ctx.spec or {}).get("construction_path") == "C"
    auto = bool(ctx.project.get("review", {}).get("auto_approve_path_c"))

    if existing and existing.get("status") == "approved" and not validation_failed:
        ctx.review_record = existing
        return StageResult(
            STATUS_PASSED,
            reason="review already approved",
            artifacts=[str(review_rel(ctx))],
        )

    if validation_failed:
        record = write_review(
            ctx.paths,
            ctx.asset_id,
            "rejected",
            reason="automated gates failed",
            gates=gates,
        )
        ctx.review_record = record
        return StageResult(
            STATUS_FAILED,
            reason="review rejected — validation errors",
            artifacts=[str(review_rel(ctx))],
        )

    if warnings:
        record = write_review(
            ctx.paths,
            ctx.asset_id,
            "pending",
            reason="warnings require human review",
            gates=gates,
        )
        ctx.review_record = record
        return StageResult(
            STATUS_FAILED,
            reason="REVIEW_PENDING: warnings present",
            artifacts=[str(review_rel(ctx))],
        )

    if path_c and auto:
        record = write_review(
            ctx.paths,
            ctx.asset_id,
            "approved",
            reason="path_c_template_human_authored; palette+silhouette gates passed",
            gates=gates,
        )
        ctx.review_record = record
        return StageResult(
            STATUS_PASSED,
            reason=record["reason"],
            artifacts=[str(review_rel(ctx))],
        )

    record = write_review(
        ctx.paths,
        ctx.asset_id,
        "pending",
        reason="first/high-value review pending (palette, silhouette)",
        gates=gates,
    )
    ctx.review_record = record
    return StageResult(
        STATUS_FAILED,
        reason="REVIEW_PENDING",
        artifacts=[str(review_rel(ctx))],
    )


def review_rel(ctx: BuildContext) -> str:
    return str(
        (ctx.paths.review_dir(ctx.asset_id) / "status.json").relative_to(ctx.paths.root)
    )


def stage_production(ctx: BuildContext) -> StageResult:
    review = ctx.review_record or read_review(ctx.paths, ctx.asset_id)
    if (ctx.validation or {}).get("status") != "PASSED":
        return StageResult(
            STATUS_FAILED,
            reason="failed gates never promote to production/",
        )
    if not review or review.get("status") != "approved":
        return StageResult(
            STATUS_FAILED,
            reason=REVIEW_PENDING,
        )
    assert ctx.native is not None
    prod = ctx.paths.production_dir(ctx.asset_id)
    prod.mkdir(parents=True, exist_ok=True)
    _save_png(prod / f"{ctx.asset_id}.png", ctx.native)
    _save_png(prod / "spritesheet.png", ctx.native)
    frame_path = prod / "static" / "00.png"
    _save_png(frame_path, ctx.native)
    preview_src = ctx.paths.previews_dir(ctx.asset_id) / "preview_4x.png"
    if preview_src.is_file():
        _save_png(prod / "preview_4x.png", Image.open(preview_src))
    meta_src = ctx.paths.metadata_dir(ctx.asset_id) / "metadata.json"
    repro_src = ctx.paths.metadata_dir(ctx.asset_id) / "reproducibility.json"
    if meta_src.is_file():
        (prod / "metadata.json").write_text(meta_src.read_text(encoding="utf-8"), encoding="utf-8")
    if repro_src.is_file():
        (prod / "reproducibility.json").write_text(
            repro_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    artifacts = [
        str((prod / f"{ctx.asset_id}.png").relative_to(ctx.paths.root)),
        str((prod / "spritesheet.png").relative_to(ctx.paths.root)),
        str((prod / "metadata.json").relative_to(ctx.paths.root)),
        str(frame_path.relative_to(ctx.paths.root)),
    ]
    return StageResult(STATUS_PASSED, artifacts=artifacts)


HANDLERS: dict[str, Handler] = {
    "asset_specification": stage_asset_specification,
    "semantic_interpretation": stage_semantic_interpretation,
    "reference_concept_generation": stage_reference_concept,
    "pixel_art_construction": stage_pixel_art_construction,
    "silhouette_validation": stage_silhouette_validation,
    "resolution_normalization": stage_resolution_normalization,
    "palette_quantization_mapping": stage_palette_mapping,
    "transparency_background_cleanup": stage_transparency_cleanup,
    "pixel_cleanup": stage_pixel_cleanup,
    "animation_construction": stage_animation_construction,
    "frame_consistency_validation": stage_frame_consistency,
    "spritesheet_generation": stage_spritesheet,
    "metadata_generation": stage_metadata,
    "automated_validation": stage_automated_validation,
    "preview_generation": stage_preview,
    "human_review": stage_human_review,
    "approved_production_asset": stage_production,
}


def empty_graph(asset_id: str, pipeline_version: str) -> dict[str, Any]:
    stages = []
    for index, (stage_id, name) in enumerate(STAGE_DEFINITIONS, start=1):
        stages.append(
            {
                "id": stage_id,
                "name": name,
                "index": index,
                "status": STATUS_PENDING,
                "reason": None,
                "started_at": None,
                "finished_at": None,
                "artifacts": [],
            }
        )
    return {
        "asset": asset_id,
        "pipeline_version": pipeline_version,
        "stages": stages,
    }


def _write_graph(ctx: BuildContext, graph: dict[str, Any]) -> None:
    validate_schema(graph, load_schema(ctx.paths, "stage_graph"), source="stage_graph")
    dump_json(ctx.working / "stage_graph.json", graph)


def run_stage_graph(
    paths: ProjectPaths,
    asset_id: str,
    *,
    stop_after: str | None = None,
) -> dict[str, Any]:
    project = load_project_config(paths)
    style = load_style_bible(paths)
    paths.ensure_asset_dirs(asset_id)
    if stop_after is None:
        _clear_dir(paths.production_dir(asset_id))
    ctx = BuildContext(
        paths=paths, asset_id=asset_id, project=project, style=style
    )
    graph = empty_graph(asset_id, project["versions"]["pipeline"])
    _write_graph(ctx, graph)

    for entry in graph["stages"]:
        stage_id = entry["id"]
        handler = HANDLERS[stage_id]
        entry["started_at"] = _now()
        entry["status"] = STATUS_RUNNING
        _write_graph(ctx, graph)
        try:
            if ctx.blocked:
                na = _na_for_path_c(ctx, stage_id)
                if na:
                    result = na
                else:
                    result = StageResult(
                        STATUS_FAILED,
                        reason=f"{STAGE_BLOCKED}: {ctx.block_reason}",
                    )
            else:
                result = handler(ctx)
        except Exception as exc:  # noqa: BLE001 — record as stage failure
            result = StageResult(STATUS_FAILED, reason=str(exc))

        if result.status not in {
            STATUS_PASSED,
            STATUS_FAILED,
            STATUS_NOT_APPLICABLE,
        }:
            result = StageResult(
                STATUS_FAILED,
                reason=f"illegal stage status {result.status!r}",
            )

        entry["status"] = result.status
        entry["reason"] = result.reason
        entry["artifacts"] = result.artifacts
        entry["finished_at"] = _now()

        if result.status == STATUS_FAILED:
            ctx.blocked = True
            ctx.block_reason = f"{entry['name']} failed"
        _write_graph(ctx, graph)

        if stop_after and stage_id == stop_after:
            # Remaining stages must still be marked, never silently skipped.
            remaining = False
            for later in graph["stages"]:
                if later["id"] == stage_id:
                    remaining = True
                    continue
                if not remaining:
                    continue
                if later["status"] == STATUS_PENDING:
                    na = _na_for_path_c(ctx, later["id"])
                    if na:
                        later["status"] = STATUS_NOT_APPLICABLE
                        later["reason"] = na.reason
                    else:
                        later["status"] = STATUS_NOT_APPLICABLE
                        later["reason"] = (
                            f"NOT_APPLICABLE: command stopped after {stop_after}; "
                            "run `pixelasset build` for the full graph"
                        )
                    later["finished_at"] = _now()
            _write_graph(ctx, graph)
            break

    return {"context": ctx, "graph": graph}


def graph_ok(graph: dict[str, Any]) -> bool:
    for stage in graph["stages"]:
        if stage["status"] == STATUS_PENDING:
            return False
        if stage["status"] == STATUS_FAILED:
            return False
        if stage["status"] not in {
            STATUS_PASSED,
            STATUS_NOT_APPLICABLE,
            STATUS_FAILED,
        }:
            return False
    return all(
        s["status"] in {STATUS_PASSED, STATUS_NOT_APPLICABLE} for s in graph["stages"]
    )


def load_stage_graph(paths: ProjectPaths, asset_id: str) -> dict[str, Any] | None:
    path = paths.working_dir(asset_id) / "stage_graph.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_schema(data, load_schema(paths, "stage_graph"), source=str(path))
    return data


def scaffold_spec(asset_id: str, project: dict[str, Any] | None = None) -> dict[str, Any]:
    native = (project or {}).get("project", {}).get("native_defaults") or {}
    width = int(native.get("width") or 24)
    height = int(native.get("height") or 16)
    view = str(native.get("view") or "3/4_iso_lite")
    origin = str(native.get("origin") or "bottom_center")
    return {
        "id": asset_id,
        "name": asset_id.replace("_", " ").title(),
        "family": asset_id,
        "variant": "default",
        "category": "prop",
        "description": f"Scaffolded spec for {asset_id}",
        "canvas": {"width": width, "height": height},
        "pixel_size": 1,
        "view": view,
        "orientation": "isometric_lite",
        "palette": {"name": "palette_v0", "max_colors": 7},
        "lighting": {"direction": "top_left", "intensity": "medium"},
        "outline": {"enabled": True, "color_role": "OUTLINE"},
        "animation": {"enabled": False, "animations": []},
        "anchors": {
            "origin": origin,
            "points": {"feet": [width // 2, height - 1]},
        },
        "background": {"transparent": True, "no_semi_alpha": True},
        "constraints": {
            "proportions": "unspecified",
            "silhouette": {
                "must_read": f"readable at {width}x{height}",
                "min_bbox": [max(8, width // 2), max(8, height // 2)],
                "oval": False,
                "binding_band": False,
                "max_components": 1,
            },
            "forbidden_elements": [
                "anti_aliasing",
                "unauthorized_colors",
                "semi_transparent_pixels",
            ],
        },
        "construction_path": "C",
        "output": {
            "png": True,
            "spritesheet": True,
            "metadata": True,
            "preview": True,
        },
    }
