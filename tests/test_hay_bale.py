from __future__ import annotations

import json
from hashlib import sha256

from PIL import Image

from pixelasset.cli import main
from pixelasset.generator import Generator, available_paths, get_generator, register_generator
from pixelasset.paths import ProjectPaths
from pixelasset.stages import graph_ok, run_stage_graph


def test_palette_violation_does_not_promote(project_copy, monkeypatch) -> None:
    from pixelasset.generators.path_c import PathCGenerator

    class Dirty(PathCGenerator):
        def construct(self, spec, palette, style, *, template_text=None):
            image = super().construct(
                spec, palette, style, template_text=template_text
            )
            image.putpixel((12, 6), (255, 0, 255, 255))
            return image

    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    register_generator("C", Dirty)
    try:
        result = run_stage_graph(ProjectPaths(project_copy), "hay_bale")
        assert not graph_ok(result["graph"])
        codes = []
        for stage in result["graph"]["stages"]:
            if stage["status"] == "FAILED" and stage.get("reason"):
                codes.append(stage["reason"])
        assert any(
            "PALETTE_VIOLATION" in reason or "not in palette" in reason
            for reason in codes
        )
        prod = project_copy / "assets" / "production" / "hay_bale"
        assert list(prod.rglob("*.png")) == []
    finally:
        register_generator("C", PathCGenerator)


def test_only_path_c_registered_by_default() -> None:
    assert "C" in available_paths()
    gen = get_generator("C")
    assert gen.construction_path == "C"
    assert gen.name == "path_c.template"


def test_generator_is_replaceable() -> None:
    class Fake(Generator):
        name = "fake"
        construction_path = "C"

        def construct(self, spec, palette, style, *, template_text=None):
            raise AssertionError("should not be called in this test")

    original = get_generator("C").__class__
    try:
        register_generator("C", Fake)
        assert get_generator("C").name == "fake"
    finally:
        register_generator("C", original)


def test_build_hay_bale_end_to_end(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    rc = main(["--root", str(project_copy), "build", "hay_bale"])
    assert rc == 0
    prod = project_copy / "assets" / "production" / "hay_bale"
    assert (prod / "hay_bale.png").is_file()
    assert (prod / "spritesheet.png").is_file()
    assert (prod / "metadata.json").is_file()
    assert (prod / "preview_4x.png").is_file()
    native = Image.open(prod / "hay_bale.png")
    assert native.size == (24, 16)
    assert native.mode == "RGBA"
    meta = json.loads((prod / "metadata.json").read_text())
    assert meta["id"] == "hay_bale"
    assert meta["construction_path"] == "C"
    assert meta["frame_size"] == [24, 16]
    assert meta["palette_version"] == "palette_v0"
    assert meta["reproducibility"]["concept_hash"] is None
    assert meta["reproducibility"]["palette_version"] == "palette_v0"
    review = json.loads(
        (project_copy / "assets" / "review" / "hay_bale" / "status.json").read_text()
    )
    assert review["status"] == "approved"
    assert "palette" in review["gates"]
    assert "silhouette" in review["gates"]
    preview = project_copy / "assets" / "previews" / "hay_bale"
    assert (preview / "native.png").is_file()
    assert (preview / "preview_4x.png").is_file()
    assert (preview / "silhouette.png").is_file()
    assert (preview / "palette.png").is_file()
    scaled = Image.open(preview / "preview_4x.png")
    assert scaled.size == (96, 64)


def test_hay_bale_is_reproducible(project_copy, monkeypatch) -> None:
    monkeypatch.setenv("PIXELASSET_ROOT", str(project_copy))
    paths = ProjectPaths(project_copy)
    first = run_stage_graph(paths, "hay_bale")
    assert graph_ok(first["graph"])
    png = project_copy / "assets" / "production" / "hay_bale" / "hay_bale.png"
    hash1 = sha256(png.read_bytes()).hexdigest()
    second = run_stage_graph(paths, "hay_bale")
    assert graph_ok(second["graph"])
    hash2 = sha256(png.read_bytes()).hexdigest()
    assert hash1 == hash2


def test_template_uses_only_palette_roles(repo_root) -> None:
    from pixelasset.config import load_asset_spec, load_palette, load_style_bible
    from pixelasset.pixelops import palette_rgb_set, rgb_tuple

    paths = ProjectPaths(repo_root)
    spec = load_asset_spec(paths, "hay_bale")
    palette = load_palette(paths, "palette_v0")
    style = load_style_bible(paths)
    template = paths.template_path("hay_bale").read_text(encoding="utf-8")
    image = get_generator("C").construct(spec, palette, style, template_text=template)
    allowed = palette_rgb_set(palette["roles"])
    pixels = image.load()
    assert pixels is not None
    used = set()
    for y in range(16):
        for x in range(24):
            px = pixels[x, y]
            if px[3] == 0:
                continue
            assert px[3] == 255
            rgb = rgb_tuple(px)
            assert rgb in allowed
            used.add(allowed[rgb])
    assert "OUTLINE" in used
    assert "ACCENT" in used
