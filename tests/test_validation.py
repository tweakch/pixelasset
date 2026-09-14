from __future__ import annotations

from PIL import Image

from pixelasset.config import load_asset_spec, load_palette, load_project_config, load_style_bible
from pixelasset.generator import get_generator
from pixelasset.paths import ProjectPaths
from pixelasset.stages import run_stage_graph
from pixelasset.validation import (
    ANCHOR_DRIFT,
    FRAME_SIZE_MISMATCH,
    PALETTE_VIOLATION,
    SILHOUETTE_BREAK,
    validate_frame,
)


def _good_image(paths: ProjectPaths):
    spec = load_asset_spec(paths, "hay_bale")
    palette = load_palette(paths, spec["palette"]["name"])
    style = load_style_bible(paths)
    template = paths.template_path("hay_bale").read_text(encoding="utf-8")
    return get_generator("C").construct(spec, palette, style, template_text=template), spec, palette


def test_hard_gates_pass_on_hay_bale_template(repo_root) -> None:
    paths = ProjectPaths(repo_root)
    image, spec, palette = _good_image(paths)
    project = load_project_config(paths)
    result = validate_frame(image, spec, palette, project)
    assert result["status"] == "PASSED"
    assert result["errors"] == []
    assert image.size == (24, 16)


def test_palette_violation(repo_root) -> None:
    paths = ProjectPaths(repo_root)
    image, spec, palette = _good_image(paths)
    image.putpixel((12, 8), (255, 0, 255, 255))
    project = load_project_config(paths)
    result = validate_frame(image, spec, palette, project)
    assert result["status"] == "FAILED"
    assert any(err["code"] == PALETTE_VIOLATION for err in result["errors"])


def test_frame_size_mismatch(repo_root) -> None:
    paths = ProjectPaths(repo_root)
    _, spec, palette = _good_image(paths)
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    project = load_project_config(paths)
    result = validate_frame(image, spec, palette, project)
    assert any(err["code"] == FRAME_SIZE_MISMATCH for err in result["errors"])


def test_anchor_drift(repo_root) -> None:
    paths = ProjectPaths(repo_root)
    image, spec, palette = _good_image(paths)
    shifted = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shifted.paste(image, (0, -4))
    project = load_project_config(paths)
    result = validate_frame(shifted, spec, palette, project)
    assert any(err["code"] == ANCHOR_DRIFT for err in result["errors"])


def test_silhouette_break(repo_root) -> None:
    paths = ProjectPaths(repo_root)
    _, spec, palette = _good_image(paths)
    image = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
    outline = tuple(palette["roles"]["OUTLINE"]) + (255,)
    image.putpixel((12, 15), outline)
    image.putpixel((11, 15), outline)
    project = load_project_config(paths)
    result = validate_frame(image, spec, palette, project)
    assert any(err["code"] == SILHOUETTE_BREAK for err in result["errors"])


def test_failed_build_does_not_promote(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    template = project_copy / "assets" / "source" / "hay_bale" / "template.txt"
    lines = ["." * 24 for _ in range(16)]
    lines[15] = "...........OO..........."
    template.write_text("\n".join(lines) + "\n")
    result = run_stage_graph(ProjectPaths(project_copy), "hay_bale")
    assert result["graph"]["stages"][-1]["status"] == "FAILED"
    prod = project_copy / "assets" / "production" / "hay_bale"
    pngs = list(prod.rglob("*.png")) if prod.exists() else []
    assert pngs == []
    pending = [s["id"] for s in result["graph"]["stages"] if s["status"] == "PENDING"]
    assert pending == []
