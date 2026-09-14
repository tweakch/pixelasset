"""Machine-readable validation gates (PIPELINE.md §20–21)."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixelasset.pixelops import (
    bounding_box,
    connected_components,
    is_opaque,
    opaque_pixels,
    palette_rgb_set,
    rgb_tuple,
)

# Hard-fail codes that never promote to production/.
PALETTE_VIOLATION = "PALETTE_VIOLATION"
FRAME_SIZE_MISMATCH = "FRAME_SIZE_MISMATCH"
ANCHOR_DRIFT = "ANCHOR_DRIFT"
SILHOUETTE_BREAK = "SILHOUETTE_BREAK"

# Stubbed / N-A for static Slice 1 props (still named for later slices).
SCALE_DRIFT = "SCALE_DRIFT"
PALETTE_DRIFT = "PALETTE_DRIFT"
ANATOMY_DRIFT = "ANATOMY_DRIFT"
LIGHTING_DRIFT = "LIGHTING_DRIFT"
SEMI_TRANSPARENT_PIXEL = "SEMI_TRANSPARENT_PIXEL"
REVIEW_PENDING = "REVIEW_PENDING"
STAGE_BLOCKED = "STAGE_BLOCKED"
STAGE_SKIPPED_SILENT = "STAGE_SKIPPED_SILENT"

HARD_FAIL_CODES = {
    PALETTE_VIOLATION,
    FRAME_SIZE_MISMATCH,
    ANCHOR_DRIFT,
    SILHOUETTE_BREAK,
}


def _issue(
    code: str,
    *,
    severity: str = "ERROR",
    message: str = "",
    frame: int | None = None,
    pixels: float | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "severity": severity}
    if message:
        item["message"] = message
    if frame is not None:
        item["frame"] = frame
    if pixels is not None:
        item["pixels"] = pixels
    return item


def check_frame_size(
    image: Image.Image,
    spec: dict[str, Any],
    *,
    frame: int = 0,
) -> list[dict[str, Any]]:
    expected = (int(spec["canvas"]["width"]), int(spec["canvas"]["height"]))
    if image.size != expected:
        return [
            _issue(
                FRAME_SIZE_MISMATCH,
                message=f"frame size {image.size[0]}×{image.size[1]} != {expected[0]}×{expected[1]}",
                frame=frame,
            )
        ]
    return []


def check_palette(
    image: Image.Image,
    palette: dict[str, Any],
    spec: dict[str, Any],
    *,
    frame: int = 0,
) -> list[dict[str, Any]]:
    allowed = palette_rgb_set(palette["roles"])
    errors: list[dict[str, Any]] = []
    pixels = image.load()
    assert pixels is not None
    width, height = image.size
    offenders = 0
    for y in range(height):
        for x in range(width):
            px = pixels[x, y]
            a = px[3]
            if a == 0:
                continue
            if 0 < a < 255 and spec.get("background", {}).get("no_semi_alpha", True):
                errors.append(
                    _issue(
                        PALETTE_VIOLATION,
                        message=f"semi-transparent pixel at ({x},{y}) alpha={a}",
                        frame=frame,
                    )
                )
                continue
            rgb = rgb_tuple(px)
            if rgb not in allowed:
                offenders += 1
    if offenders:
        errors.append(
            _issue(
                PALETTE_VIOLATION,
                message=f"{offenders} opaque pixel(s) not in palette roles",
                frame=frame,
                pixels=offenders,
            )
        )
    max_colors = int(spec.get("palette", {}).get("max_colors") or len(allowed))
    used = {rgb_tuple(px) for _, _, px in opaque_pixels(image)}
    if len(used) > max_colors:
        errors.append(
            _issue(
                PALETTE_VIOLATION,
                message=f"{len(used)} colors used > max_colors {max_colors}",
                frame=frame,
                pixels=len(used),
            )
        )
    return errors


def check_anchor_drift(
    image: Image.Image,
    spec: dict[str, Any],
    project: dict[str, Any],
    *,
    frame: int = 0,
) -> list[dict[str, Any]]:
    feet = spec["anchors"]["points"]["feet"]
    fx, fy = int(feet[0]), int(feet[1])
    max_drift = float(project["validation"]["max_anchor_drift_px"])
    opaque = opaque_pixels(image)
    if not opaque:
        return [
            _issue(
                ANCHOR_DRIFT,
                message="no opaque pixels to measure feet contact",
                frame=frame,
            )
        ]
    max_y = max(y for _, y, _ in opaque)
    ground = [(x, y) for x, y, _ in opaque if y == max_y]
    cx = sum(x for x, _ in ground) / len(ground)
    drift_x = abs(cx - fx)
    drift_y = abs(max_y - fy)
    drift = max(drift_x, drift_y)
    if drift > max_drift:
        return [
            _issue(
                ANCHOR_DRIFT,
                message=(
                    f"feet expected ({fx},{fy}) origin={spec['anchors']['origin']}; "
                    f"ground midpoint=({cx:.1f},{max_y}) drift={drift:.2f}px"
                ),
                frame=frame,
                pixels=drift,
            )
        ]
    return []


def _has_binding_band(
    image: Image.Image,
    accent_rgb: tuple[int, int, int],
    *,
    cluster_min: int = 2,
) -> bool:
    """One horizontal ACCENT band, cluster ≥2px, readable at native size."""
    opaque = [(x, y) for x, y, _ in opaque_pixels(image)]
    box = bounding_box(opaque)
    if box is None:
        return False
    min_x, min_y, max_x, max_y = box
    height = max_y - min_y + 1
    width = max_x - min_x + 1
    pixels = image.load()
    assert pixels is not None
    y_start = min_y + max(0, height // 5)
    y_end = max(y_start, max_y - max(0, height // 5))
    min_run = max(8, width // 2)
    band_rows: list[int] = []
    for y in range(y_start, y_end + 1):
        run = 0
        best = 0
        for x in range(min_x, max_x + 1):
            px = pixels[x, y]
            if is_opaque(px) and rgb_tuple(px) == accent_rgb:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best >= min_run:
            band_rows.append(y)
    if len(band_rows) < cluster_min:
        return False
    consecutive = 1
    best_streak = 1
    for prev, cur in zip(band_rows, band_rows[1:]):
        if cur == prev + 1:
            consecutive += 1
            best_streak = max(best_streak, consecutive)
        else:
            consecutive = 1
    return best_streak >= cluster_min


def check_silhouette(
    image: Image.Image,
    spec: dict[str, Any],
    project: dict[str, Any],
    palette: dict[str, Any],
    *,
    frame: int = 0,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    components = connected_components(image)
    silhouette = spec.get("constraints", {}).get("silhouette", {})
    if isinstance(silhouette, str):
        silhouette = {"must_read": silhouette}

    max_components = int(
        silhouette.get("max_components")
        or project["validation"]["max_components"]
    )
    if len(components) == 0:
        errors.append(
            _issue(SILHOUETTE_BREAK, message="empty silhouette", frame=frame)
        )
        return errors
    if len(components) > max_components:
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message=f"{len(components)} connected components > max {max_components}",
                frame=frame,
            )
        )

    all_points = [p for comp in components for p in comp]
    box = bounding_box(all_points)
    assert box is not None
    min_x, min_y, max_x, max_y = box
    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1
    min_bbox = silhouette.get("min_bbox") or [
        project["validation"].get("min_bbox_width", 8),
        project["validation"].get("min_bbox_height", 10),
    ]
    if bbox_w < int(min_bbox[0]) or bbox_h < int(min_bbox[1]):
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message=(
                    f"bbox {bbox_w}×{bbox_h} does not read at native size "
                    f"(min {min_bbox[0]}×{min_bbox[1]})"
                ),
                frame=frame,
            )
        )

    min_occupied = int(project["validation"].get("min_occupied_pixels", 40))
    if len(all_points) < min_occupied:
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message=f"occupied {len(all_points)} < min {min_occupied}",
                frame=frame,
                pixels=len(all_points),
            )
        )

    if silhouette.get("oval") and bbox_w < bbox_h:
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message=f"silhouette is not oval/wide (bbox {bbox_w}×{bbox_h})",
                frame=frame,
            )
        )

    if silhouette.get("binding_band"):
        accent = palette["roles"]["ACCENT"]
        accent_rgb = (int(accent[0]), int(accent[1]), int(accent[2]))
        if not _has_binding_band(image, accent_rgb):
            errors.append(
                _issue(
                    SILHOUETTE_BREAK,
                    message="binding band does not read at native size",
                    frame=frame,
                )
            )
    return errors


def check_static_stubs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Animation drift codes are not applicable to a single static frame."""
    if spec.get("animation", {}).get("enabled"):
        return []
    return []


def validate_frame(
    image: Image.Image,
    spec: dict[str, Any],
    palette: dict[str, Any],
    project: dict[str, Any],
    *,
    frame: int = 0,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors.extend(check_frame_size(image, spec, frame=frame))
    errors.extend(check_palette(image, palette, spec, frame=frame))
    errors.extend(check_anchor_drift(image, spec, project, frame=frame))
    errors.extend(check_silhouette(image, spec, project, palette, frame=frame))
    warnings.extend(check_static_stubs(spec))
    status = "FAILED" if errors else "PASSED"
    return {
        "asset": spec["id"],
        "status": status,
        "errors": errors,
        "warnings": warnings,
    }


def merge_results(asset_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for result in results:
        errors.extend(result.get("errors") or [])
        warnings.extend(result.get("warnings") or [])
    return {
        "asset": asset_id,
        "status": "FAILED" if errors else "PASSED",
        "errors": errors,
        "warnings": warnings,
    }
