from __future__ import annotations

import json
from hashlib import sha256

from PIL import Image

from pixelasset.cli import main
from pixelasset.paths import ProjectPaths
from pixelasset.stages import graph_ok, run_stage_graph
from pixelasset.validation import (
    ANATOMY_DRIFT,
    LIGHTING_DRIFT,
    PALETTE_VIOLATION,
    check_frame_consistency,
    validate_frame,
)

FROZEN_PALETTE_V1 = {
    "OUTLINE": [28, 19, 12],
    "SHADOW_DARK": [43, 29, 20],
    "SHADOW": [105, 68, 33],
    "BASE": [138, 90, 43],
    "LIGHT": [166, 108, 52],
    "HIGHLIGHT": [196, 137, 90],
    "ACCENT": [74, 53, 36],
}


def test_build_horse_bay_end_to_end(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    rc = main(["--root", str(project_copy), "build", "horse_bay"])
    assert rc == 0
    prod = project_copy / "assets" / "production" / "horse_bay"
    assert (prod / "horse_bay.png").is_file()
    assert (prod / "spritesheet.png").is_file()
    assert (prod / "metadata.json").is_file()
    assert (prod / "preview_4x.png").is_file()
    assert (prod / "idle" / "00.png").is_file()
    assert (prod / "idle" / "01.png").is_file()
    assert (prod / "idle" / "02.png").is_file()
    assert not (prod / "walk").exists()
    native = Image.open(prod / "horse_bay.png")
    assert native.size == (48, 32)
    assert native.mode == "RGBA"
    sheet = Image.open(prod / "spritesheet.png")
    assert sheet.size == (144, 32)
    key = Image.open(prod / "idle" / "00.png")
    assert key.tobytes() == native.tobytes()
    frame1 = Image.open(prod / "idle" / "01.png")
    frame2 = Image.open(prod / "idle" / "02.png")
    assert frame1.tobytes() != key.tobytes()
    assert frame2.tobytes() != key.tobytes()
    meta = json.loads((prod / "metadata.json").read_text())
    assert meta["id"] == "horse_bay"
    assert meta["construction_path"] == "C"
    assert meta["frame_size"] == [48, 32]
    assert meta["frame_count"] == 3
    assert meta["palette_version"] == "palette_v1"
    assert meta["animations"][0]["name"] == "idle"
    assert meta["animations"][0]["frames"] == 3
    assert meta["reproducibility"]["concept_hash"] is None
    assert "walk" not in json.dumps(meta).lower()
    review = json.loads(
        (project_copy / "assets" / "review" / "horse_bay" / "status.json").read_text()
    )
    assert review["status"] == "approved"
    assert review["gates"] == ["palette", "silhouette", "anatomy"]
    preview = project_copy / "assets" / "previews" / "horse_bay"
    assert (preview / "native.png").is_file()
    assert (preview / "preview_4x.png").is_file()
    assert (preview / "silhouette.png").is_file()
    assert (preview / "animation_preview.gif").is_file()
    graph = json.loads(
        (project_copy / "assets" / "working" / "horse_bay" / "stage_graph.json").read_text()
    )
    by_id = {s["id"]: s for s in graph["stages"]}
    assert by_id["reference_concept_generation"]["status"] == "NOT_APPLICABLE"
    assert by_id["animation_construction"]["status"] == "PASSED"
    assert by_id["frame_consistency_validation"]["status"] == "PASSED"
    assert graph_ok(graph)


def test_horse_bay_is_reproducible(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    paths = ProjectPaths(project_copy)
    first = run_stage_graph(paths, "horse_bay")
    assert graph_ok(first["graph"])
    png = project_copy / "assets" / "production" / "horse_bay" / "idle" / "01.png"
    hash1 = sha256(png.read_bytes()).hexdigest()
    second = run_stage_graph(paths, "horse_bay")
    assert graph_ok(second["graph"])
    hash2 = sha256(png.read_bytes()).hexdigest()
    assert hash1 == hash2


def test_horse_uses_palette_v1_only(repo_root) -> None:
    from pixelasset.config import load_asset_spec, load_palette, load_style_bible
    from pixelasset.generator import get_generator
    from pixelasset.pixelops import palette_rgb_set, rgb_tuple

    paths = ProjectPaths(repo_root)
    spec = load_asset_spec(paths, "horse_bay")
    palette = load_palette(paths, spec["palette"]["name"])
    v0 = load_palette(paths, "palette_v0")
    style = load_style_bible(paths)
    template = paths.template_path("horse_bay").read_text(encoding="utf-8")
    image = get_generator("C").construct(spec, palette, style, template_text=template)
    allowed = palette_rgb_set(palette["roles"])
    v0_rgbs = set(palette_rgb_set(v0["roles"]))
    pixels = image.load()
    assert pixels is not None
    used = set()
    for y in range(32):
        for x in range(48):
            px = pixels[x, y]
            if px[3] == 0:
                continue
            assert px[3] == 255
            rgb = rgb_tuple(px)
            assert rgb in allowed
            assert rgb not in v0_rgbs
            used.add(allowed[rgb])
    assert used >= {"OUTLINE", "SHADOW_DARK", "BASE", "ACCENT"}


def test_horse_palette_violation_does_not_promote(project_copy, monkeypatch) -> None:
    from pixelasset.generators.path_c import PathCGenerator
    from pixelasset.generator import register_generator

    class Dirty(PathCGenerator):
        def construct(self, spec, palette, style, *, template_text=None):
            image = super().construct(
                spec, palette, style, template_text=template_text
            )
            image.putpixel((24, 16), (255, 0, 255, 255))
            return image

    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    register_generator("C", Dirty)
    try:
        result = run_stage_graph(ProjectPaths(project_copy), "horse_bay")
        assert not graph_ok(result["graph"])
        prod = project_copy / "assets" / "production" / "horse_bay"
        assert list(prod.rglob("*.png")) == []
        codes = " ".join(
            stage.get("reason") or ""
            for stage in result["graph"]["stages"]
            if stage["status"] == "FAILED"
        )
        assert PALETTE_VIOLATION in codes or "not in palette" in codes
    finally:
        register_generator("C", PathCGenerator)


def test_anatomy_and_lighting_drift_are_hard_fails(repo_root) -> None:
    from pixelasset.config import (
        load_asset_spec,
        load_palette,
        load_project_config,
        load_style_bible,
    )
    from pixelasset.generator import get_generator
    from pixelasset.animation import apply_idle_motions

    paths = ProjectPaths(repo_root)
    spec = load_asset_spec(paths, "horse_bay")
    palette = load_palette(paths, "palette_v1")
    style = load_style_bible(paths)
    project = load_project_config(paths)
    template = paths.template_path("horse_bay").read_text(encoding="utf-8")
    key = get_generator("C").construct(spec, palette, style, template_text=template)
    good = apply_idle_motions(key, spec, palette, style)
    result = validate_frame(key, spec, palette, project)
    assert result["status"] == "PASSED"
    assert check_frame_consistency(good, spec, palette, project) == []

    redrawn = key.copy()
    redrawn.paste(key, (0, -4))
    drifted = [key.copy(), redrawn, key.copy()]
    errors = check_frame_consistency(drifted, spec, palette, project)
    assert any(err["code"] == ANATOMY_DRIFT for err in errors)

    lit = key.copy()
    highlight = tuple(palette["roles"]["HIGHLIGHT"]) + (255,)
    # Dump highlight onto the belly / bottom-right to move the lighting centroid.
    for y in range(20, 28):
        for x in range(30, 40):
            px = lit.getpixel((x, y))
            if px[3] == 255:
                lit.putpixel((x, y), highlight)
    lit_errors = check_frame_consistency([key.copy(), lit, key.copy()], spec, palette, project)
    assert any(err["code"] in {LIGHTING_DRIFT, ANATOMY_DRIFT} for err in lit_errors)
