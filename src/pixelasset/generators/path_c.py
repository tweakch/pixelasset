"""Construction Path C: template / procedural / hand-authored. No AI."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixelasset.animation import apply_idle_motions
from pixelasset.generator import Generator, register_generator


class PathCGenerator(Generator):
    name = "path_c.template"
    construction_path = "C"

    def construct(
        self,
        spec: dict[str, Any],
        palette: dict[str, Any],
        style: dict[str, Any],
        *,
        template_text: str | None = None,
    ) -> Image.Image:
        if template_text is None:
            raise ValueError(
                f"Path C requires a template for asset {spec.get('id')!r}"
            )
        return render_template(spec, palette, style, template_text)

    def construct_idle(
        self,
        key_frame: Image.Image,
        spec: dict[str, Any],
        palette: dict[str, Any],
        style: dict[str, Any],
    ) -> list[Image.Image]:
        return apply_idle_motions(key_frame, spec, palette, style)


def render_template(
    spec: dict[str, Any],
    palette: dict[str, Any],
    style: dict[str, Any],
    template_text: str,
) -> Image.Image:
    width = int(spec["canvas"]["width"])
    height = int(spec["canvas"]["height"])
    tokens: dict[str, str | None] = style["construction"]["path_c_template_tokens"]
    roles: dict[str, list[int]] = palette["roles"]

    lines = [line.rstrip("\n") for line in template_text.splitlines() if line != ""]
    if len(lines) != height:
        raise ValueError(
            f"Template height {len(lines)} != canvas height {height}"
        )
    for y, line in enumerate(lines):
        if len(line) != width:
            raise ValueError(
                f"Template row {y} width {len(line)} != canvas width {width}"
            )

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    assert pixels is not None

    for y, line in enumerate(lines):
        for x, ch in enumerate(line):
            if ch not in tokens:
                raise ValueError(f"Unknown template token {ch!r} at ({x},{y})")
            role = tokens[ch]
            if role is None:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            if role not in roles:
                raise ValueError(f"Token {ch!r} maps to unknown role {role!r}")
            r, g, b = roles[role]
            pixels[x, y] = (int(r), int(g), int(b), 255)

    return image


register_generator("C", PathCGenerator)
