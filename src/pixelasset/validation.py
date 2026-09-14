"""Machine-readable validation gates (PIPELINE.md §20–21)."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixelasset.pixelops import (
    bounding_box,
    center_of_mass,
    connected_components,
    is_opaque,
    opaque_pixels,
    palette_rgb_set,
    rgb_tuple,
    role_centroid,
)

# Hard-fail codes that never promote to production/.
PALETTE_VIOLATION = "PALETTE_VIOLATION"
FRAME_SIZE_MISMATCH = "FRAME_SIZE_MISMATCH"
ANCHOR_DRIFT = "ANCHOR_DRIFT"
SILHOUETTE_BREAK = "SILHOUETTE_BREAK"
ANATOMY_DRIFT = "ANATOMY_DRIFT"
LIGHTING_DRIFT = "LIGHTING_DRIFT"

# Named for later slices / drift family.
SCALE_DRIFT = "SCALE_DRIFT"
PALETTE_DRIFT = "PALETTE_DRIFT"
SEMI_TRANSPARENT_PIXEL = "SEMI_TRANSPARENT_PIXEL"
REVIEW_PENDING = "REVIEW_PENDING"
STAGE_BLOCKED = "STAGE_BLOCKED"
STAGE_SKIPPED_SILENT = "STAGE_SKIPPED_SILENT"

HARD_FAIL_CODES = {
    PALETTE_VIOLATION,
    FRAME_SIZE_MISMATCH,
    ANCHOR_DRIFT,
    SILHOUETTE_BREAK,
    ANATOMY_DRIFT,
    LIGHTING_DRIFT,
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

    if silhouette.get("horse"):
        errors.extend(
            _check_horse_silhouette(image, silhouette, all_points, box, frame=frame)
        )
    return errors


def _horizontal_runs(xs: list[int]) -> int:
    if not xs:
        return 0
    ordered = sorted(xs)
    runs = 1
    for prev, cur in zip(ordered, ordered[1:]):
        if cur > prev + 1:
            runs += 1
    return runs


def _check_horse_silhouette(
    image: Image.Image,
    silhouette: dict[str, Any],
    all_points: list[tuple[int, int]],
    box: tuple[int, int, int, int],
    *,
    frame: int,
) -> list[dict[str, Any]]:
    """Side-view horse must read at native size: head right, tail left, hooves down."""
    errors: list[dict[str, Any]] = []
    min_x, min_y, max_x, max_y = box
    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1
    facing = str(silhouette.get("facing") or "right")

    top_cut = min_y + max(6, int(0.25 * bbox_h))
    top_pts = [(x, y) for x, y in all_points if y <= top_cut]
    if top_pts:
        avg_top_x = sum(x for x, _ in top_pts) / len(top_pts)
        avg_x = sum(x for x, _ in all_points) / len(all_points)
        if facing == "right" and avg_top_x <= avg_x:
            errors.append(
                _issue(
                    SILHOUETTE_BREAK,
                    message="horse does not face right (head/ears not on the right)",
                    frame=frame,
                )
            )
        if facing == "left" and avg_top_x >= avg_x:
            errors.append(
                _issue(
                    SILHOUETTE_BREAK,
                    message="horse does not face left",
                    frame=frame,
                )
            )

    mid_lo = min_y + bbox_h // 4
    mid_hi = min_y + (bbox_h * 3) // 4
    left_band = [x for x, y in all_points if mid_lo <= y <= mid_hi]
    if left_band and min(left_band) > min_x + max(2, bbox_w // 6):
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message="tail does not read on the left of the silhouette",
                frame=frame,
            )
        )

    hoof_xs = [x for x, y in all_points if y == max_y]
    clusters = _horizontal_runs(hoof_xs)
    min_hooves = int(silhouette.get("min_hoof_clusters") or 3)
    max_hooves = int(silhouette.get("max_hoof_clusters") or 4)
    if clusters < min_hooves or clusters > max_hooves:
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message=(
                    f"hoof ground-contact clusters {clusters} not in "
                    f"{min_hooves}–{max_hooves}"
                ),
                frame=frame,
            )
        )

    if max_x < image.size[0] // 2:
        errors.append(
            _issue(
                SILHOUETTE_BREAK,
                message="silhouette does not read as a horse facing across the canvas",
                frame=frame,
            )
        )
    return errors


def check_static_stubs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Animation drift codes are not applicable to a single static frame."""
    if spec.get("animation", {}).get("enabled"):
        return []
    return []


def check_frame_consistency(
    frames: list[Image.Image],
    spec: dict[str, Any],
    palette: dict[str, Any],
    project: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare idle frames to the key pose. Hard-fail anatomy and lighting drift."""
    errors: list[dict[str, Any]] = []
    if len(frames) < 2:
        return [
            _issue(
                ANATOMY_DRIFT,
                message="frame consistency requires at least 2 idle frames",
            )
        ]
    validation = project.get("validation") or {}
    max_bbox = float(validation.get("max_anatomy_bbox_drift_px") or 1)
    max_com = float(validation.get("max_anatomy_com_drift_px") or 1.5)
    max_occupied = float(validation.get("max_anatomy_occupied_delta") or 12)
    max_light = float(validation.get("max_lighting_centroid_drift_px") or 2)
    max_anchor = float(validation.get("max_anchor_drift_px") or 1)

    key = frames[0]
    key_pts = [(x, y) for x, y, _ in opaque_pixels(key)]
    key_box = bounding_box(key_pts)
    key_com = center_of_mass(key)
    if key_box is None or key_com is None:
        return [_issue(ANATOMY_DRIFT, message="key pose has no opaque pixels")]
    key_count = len(key_pts)
    key_hoof_y = key_box[3]
    roles = palette["roles"]
    light_rgbs = {
        (int(roles["LIGHT"][0]), int(roles["LIGHT"][1]), int(roles["LIGHT"][2])),
        (
            int(roles["HIGHLIGHT"][0]),
            int(roles["HIGHLIGHT"][1]),
            int(roles["HIGHLIGHT"][2]),
        ),
    }
    key_light = role_centroid(key, light_rgbs)

    for index, frame in enumerate(frames):
        errors.extend(check_frame_size(frame, spec, frame=index))
        if index == 0:
            continue
        pts = [(x, y) for x, y, _ in opaque_pixels(frame)]
        box = bounding_box(pts)
        com = center_of_mass(frame)
        if box is None or com is None:
            errors.append(_issue(ANATOMY_DRIFT, message="empty frame", frame=index))
            continue
        bbox_drift = max(abs(a - b) for a, b in zip(box, key_box))
        if bbox_drift > max_bbox:
            errors.append(
                _issue(
                    ANATOMY_DRIFT,
                    message=f"bbox drift {bbox_drift:.2f}px > {max_bbox}",
                    frame=index,
                    pixels=bbox_drift,
                )
            )
        com_drift = max(abs(com[0] - key_com[0]), abs(com[1] - key_com[1]))
        if com_drift > max_com:
            errors.append(
                _issue(
                    ANATOMY_DRIFT,
                    message=f"center-of-mass drift {com_drift:.2f}px > {max_com}",
                    frame=index,
                    pixels=com_drift,
                )
            )
        occupied_delta = abs(len(pts) - key_count)
        if occupied_delta > max_occupied:
            errors.append(
                _issue(
                    ANATOMY_DRIFT,
                    message=(
                        f"occupied delta {occupied_delta} > {max_occupied} "
                        "(idle must be key pose + minimal shifts)"
                    ),
                    frame=index,
                    pixels=occupied_delta,
                )
            )
        hoof_y = box[3]
        if abs(hoof_y - key_hoof_y) > max_anchor:
            errors.append(
                _issue(
                    ANCHOR_DRIFT,
                    message=f"hoof y {hoof_y} drifted from key {key_hoof_y}",
                    frame=index,
                    pixels=abs(hoof_y - key_hoof_y),
                )
            )
        light = role_centroid(frame, light_rgbs)
        if key_light is None or light is None:
            errors.append(
                _issue(
                    LIGHTING_DRIFT,
                    message="missing LIGHT/HIGHLIGHT centroid",
                    frame=index,
                )
            )
        else:
            light_drift = max(
                abs(light[0] - key_light[0]), abs(light[1] - key_light[1])
            )
            if light_drift > max_light:
                errors.append(
                    _issue(
                        LIGHTING_DRIFT,
                        message=(
                            f"lighting centroid drift {light_drift:.2f}px > {max_light}"
                        ),
                        frame=index,
                        pixels=light_drift,
                    )
                )
    return errors


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
