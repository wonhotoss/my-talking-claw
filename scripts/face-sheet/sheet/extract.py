"""Cut the drawn parts out of photos of the sheets.

    uv run python -m sheet.extract photo1.jpg photo2.jpg ... --out parts/

Each photo may show any page (or several) in any order and at an angle; the
corner markers say which page it is and how to flatten it. A later photo of
the same page replaces the earlier one. Out come transparent PNGs, one per
drawn cell - the child's colours kept, black lines turned into the face's
glowing ink so they read on the dark screen - plus parts.json with where each one sits
on the face, and preview.png - the parts assembled into a few faces - to check
the result at a glance.
"""

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from sheet import current_face, layout
from sheet.layout import face_box, mm_rect, placed_cell


# Raster resolution of a flattened page. A phone photo of a full A4 page holds
# ~13 px/mm, so 10 loses little, and at 0.6 mm/unit it gives 6 px per face
# unit - ~4x what the kiosk draws (1.6 px/unit).
pixels_per_mm = 10.0
pixels_per_unit = pixels_per_mm * layout.mm_per_unit

# How far from white a pixel must be to count as ink: 1 - its darkest channel
# after the lighting is divided out. Below the low edge it is paper (and its
# grain, and the camera's noise); above the high edge, solid ink.
ink_low = 0.10
ink_high = 0.30
# A drawn cell needs at least this much solid ink; less is an empty cell with
# some speckle.
min_ink_mm2 = 12.0
# Ink blobs smaller than this are dust, not drawing.
min_blob_mm2 = 0.8
# Paper samples for the lighting model: a grid over the parts of the page
# where nothing is printed and nothing should be drawn.
paper_sample_step_mm = 4.0
paper_sample_window_px = 9
# Keep clear of printed things (borders, ticks, labels, markers) by this much.
paper_clearance_mm = 5.0

# The face lives on a dark screen (styles.css .face_root), where a child's
# black marker would vanish. So colourless ink - black, grey, pencil - becomes
# the face's own glowing ink, and coloured ink keeps its hue but is lifted to
# at least this brightness. Chroma (max - min channel) below the low edge is
# fully colourless; the blend fades out by the high edge.
glow_bgr = np.array(current_face.ink_rgb[::-1])
glow_chroma_low = 0.08
glow_chroma_high = 0.25
glow_min_value = 0.85


@dataclass(frozen=True)
class part:
    key: str
    file: str
    # Where the image lands on the face, in face units: the image is
    # stretched to exactly this box.
    box: face_box


def read_image(path: Path) -> np.ndarray:
    # cv2.imread cannot open non-ASCII paths on Windows.
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError(f"{path}: not an image")

    return image


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)

    if not ok:
        raise ValueError(f"{path}: PNG encoding failed")

    encoded.tofile(str(path))


def detect_pages(image: np.ndarray) -> dict[int, np.ndarray]:
    """Page index -> homography from photo pixels to flattened page pixels."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, layout.marker_dictionary))
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))

    if ids is None:
        return {}

    found: dict[int, list[tuple[int, np.ndarray]]] = {}

    for marker, quad in zip(ids.flatten().tolist(), corners):
        page_index, corner = divmod(marker, layout.markers_per_page)

        if page_index < len(layout.pages):
            found.setdefault(page_index, []).append((corner, quad.reshape(4, 2)))

    rects = layout.marker_rects()

    def page_quad(corner: int) -> np.ndarray:
        rect = rects[corner]

        return np.array(
            [
                (rect.left, rect.top),
                (rect.right, rect.top),
                (rect.right, rect.bottom),
                (rect.left, rect.bottom),
            ],
            dtype=np.float32,
        ) * pixels_per_mm

    # Three markers are the minimum that pins the page's whole area; two
    # adjacent ones would leave the far side to extrapolation.
    return {
        page_index: cv2.findHomography(
            np.concatenate([quad for _, quad in markers]).astype(np.float32),
            np.concatenate([page_quad(corner) for corner, _ in markers]),
            cv2.RANSAC,
            3.0,
        )[0]
        for page_index, markers in found.items()
        if len({corner for corner, _ in markers}) >= 3
    }


def page_pixels(value_mm: float) -> int:
    return round(value_mm * pixels_per_mm)


def flatten(image: np.ndarray, homography: np.ndarray) -> np.ndarray:
    size = (page_pixels(layout.page_width_mm), page_pixels(layout.page_height_mm))

    return cv2.warpPerspective(image, homography, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def grown(rect: mm_rect, by: float) -> mm_rect:
    return mm_rect(rect.left - by, rect.top - by, rect.width + 2 * by, rect.height + 2 * by)


def printed_rects(page_index: int) -> list[mm_rect]:
    """Everything the renderer prints on a page, and every cell a child draws in."""
    cells = [placed for placed in layout.placed_cells() if placed.page_index == page_index]
    tick = layout.tick_length_mm

    return [
        mm_rect(0, 0, layout.page_width_mm, layout.header_bottom_mm),
        mm_rect(0, layout.footer_top_mm, layout.page_width_mm, layout.page_height_mm - layout.footer_top_mm),
        *layout.marker_rects(),
        # Ticks and their labels stand outside the border; the label sits above.
        *[grown(placed.rect, tick + 6) for placed in cells],
        *[placed.label_rect for placed in cells],
    ]


def paper_samples(page_index: int) -> list[tuple[float, float]]:
    """Grid points, in mm, that lie on bare paper."""
    blocked = [grown(rect, paper_clearance_mm) for rect in printed_rects(page_index)]
    edge = layout.margin_mm / 2
    xs = np.arange(edge, layout.page_width_mm - edge, paper_sample_step_mm)
    ys = np.arange(edge, layout.page_height_mm - edge, paper_sample_step_mm)

    return [
        (float(x), float(y))
        for x in xs
        for y in ys
        if not any(rect.left <= x <= rect.right and rect.top <= y <= rect.bottom for rect in blocked)
    ]


def paper_model(page: np.ndarray, page_index: int) -> np.ndarray:
    """The paper's colour under this photo's lighting, everywhere on the page.

    A quadratic surface per channel, fitted to the bare margins and gaps
    between cells: a phone photo's light falls off smoothly, and the surface
    then extends under the drawn cells, where no paper colour can be read
    directly from a fully coloured-in area. Fitted twice, the second time
    without samples darker than the first fit - a stray mark outside a cell.
    """
    samples = paper_samples(page_index)

    if len(samples) < 30:
        raise ValueError(f"page {page_index + 1}: only {len(samples)} paper samples; layout leaves no bare paper")

    blurred = cv2.medianBlur(page, paper_sample_window_px)
    points = np.array(samples) * pixels_per_mm
    colors = np.array([blurred[round(y), round(x)] for x, y in points], dtype=np.float64)

    def terms(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        u = x / page.shape[1]
        v = y / page.shape[0]

        return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=-1)

    design = terms(points[:, 0], points[:, 1])
    first = np.linalg.lstsq(design, colors, rcond=None)[0]
    kept = (colors >= design @ first - 25).all(axis=1)
    fit = np.linalg.lstsq(design[kept], colors[kept], rcond=None)[0]

    grid_y, grid_x = np.mgrid[0 : page.shape[0], 0 : page.shape[1]].astype(np.float64)

    return np.clip(terms(grid_x, grid_y) @ fit, 1, 255)


def cut_rect(placed: placed_cell) -> mm_rect:
    return grown(placed.rect, -layout.cut_inset_mm)


def ink_layers(page: np.ndarray, paper: np.ndarray, rect: mm_rect) -> tuple[np.ndarray, np.ndarray]:
    """(colour 0..1 as BGR, alpha 0..1) inside a cell, the paper removed."""
    rows = slice(page_pixels(rect.top), page_pixels(rect.bottom))
    columns = slice(page_pixels(rect.left), page_pixels(rect.right))
    seen = np.clip(page[rows, columns].astype(np.float64) / paper[rows, columns], 0, 1)
    strength = 1 - seen.min(axis=2)
    alpha = np.clip((strength - ink_low) / (ink_high - ink_low), 0, 1)

    solid = (alpha > 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    min_blob_px = min_blob_mm2 * pixels_per_mm**2
    kept_labels = np.array([0] + [stats[label, cv2.CC_STAT_AREA] >= min_blob_px for label in range(1, count)], dtype=bool)
    kept = cv2.dilate(kept_labels[labels].astype(np.uint8), np.ones((5, 5), np.uint8))
    alpha = alpha * kept

    # The camera saw ink over white, partly: seen = alpha * ink + (1 - alpha).
    # Undo that so soft edges keep the ink's own colour instead of fading to
    # white - which would draw a pale halo on any background but paper.
    safe_alpha = np.maximum(alpha, 1e-3)[..., None]
    color = np.clip((seen - (1 - alpha[..., None])) / safe_alpha, 0, 1)

    return glow(color), alpha


def glow(color: np.ndarray) -> np.ndarray:
    """Ink seen on paper -> ink that reads on the dark screen (BGR, 0..1)."""
    chroma = color.max(axis=2) - color.min(axis=2)
    colourless = np.clip((glow_chroma_high - chroma) / (glow_chroma_high - glow_chroma_low), 0, 1)[..., None]
    hsv = cv2.cvtColor(color.astype(np.float32), cv2.COLOR_BGR2HSV)
    hsv[..., 2] = np.maximum(hsv[..., 2], glow_min_value)
    lifted = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float64)

    return colourless * glow_bgr + (1 - colourless) * lifted


def trimmed_part(key: str, color: np.ndarray, alpha: np.ndarray, area: face_box) -> tuple[part, np.ndarray] | None:
    """Crop to the ink, as a part whose box is where that crop sits on the face."""
    if (alpha > 0.5).sum() < min_ink_mm2 * pixels_per_mm**2:
        return None

    ys, xs = np.nonzero(alpha > 0)
    top, bottom = max(ys.min() - 2, 0), min(ys.max() + 3, alpha.shape[0])
    left, right = max(xs.min() - 2, 0), min(xs.max() + 3, alpha.shape[1])
    bgra = np.dstack([color[top:bottom, left:right], alpha[top:bottom, left:right]])

    return (
        part(
            key=key,
            file=f"{key}.png",
            box=face_box(
                left=area.left + left / pixels_per_unit,
                top=area.top + top / pixels_per_unit,
                width=(right - left) / pixels_per_unit,
                height=(bottom - top) / pixels_per_unit,
            ),
        ),
        np.round(bgra * 255).astype(np.uint8),
    )


def extract_cell(page: np.ndarray, paper: np.ndarray, placed: placed_cell) -> list[tuple[part, np.ndarray]]:
    rect = cut_rect(placed)
    color, alpha = ink_layers(page, paper, rect)
    face_area = placed.cell.face_area
    inset_units = layout.cut_inset_mm / layout.mm_per_unit
    area = face_box(
        face_area.left + inset_units,
        face_area.top + inset_units,
        face_area.width - 2 * inset_units,
        face_area.height - 2 * inset_units,
    )

    if not placed.cell.split:
        pieces = [(placed.cell.key, color, alpha, area)]
    else:
        middle = alpha.shape[1] // 2
        half_units = middle / pixels_per_unit
        pieces = [
            (f"{placed.cell.key}_left", color[:, :middle], alpha[:, :middle], area),
            (
                f"{placed.cell.key}_right",
                color[:, middle:],
                alpha[:, middle:],
                face_box(area.left + half_units, area.top, area.width - half_units, area.height),
            ),
        ]

    return [result for result in (trimmed_part(*piece) for piece in pieces) if result is not None]


def extract_page(image: np.ndarray, homography: np.ndarray, page_index: int) -> list[tuple[part, np.ndarray]]:
    page = flatten(image, homography)
    paper = paper_model(page, page_index)

    return [
        result
        for placed in layout.placed_cells()
        if placed.page_index == page_index
        for result in extract_cell(page, paper, placed)
    ]


def extract_photos(photos: list[Path]) -> dict[int, list[tuple[part, np.ndarray]]]:
    """Page index -> its parts. A page photographed twice keeps the later photo."""
    pages: dict[int, list[tuple[part, np.ndarray]]] = {}

    for photo in photos:
        image = read_image(photo)
        homographies = detect_pages(image)

        if not homographies:
            raise ValueError(f"{photo}: no sheet page found - are at least three corner markers in the picture?")

        for page_index, homography in sorted(homographies.items()):
            if page_index in pages:
                print(f"{photo}: page {page_index + 1} again, replacing the earlier photo")

            pages[page_index] = extract_page(image, homography, page_index)
            print(f"{photo}: page {page_index + 1}, {len(pages[page_index])} parts")

    return pages


def place_part(canvas: np.ndarray, image: np.ndarray, box: face_box, origin: tuple[float, float], scale: float) -> None:
    """Alpha-composite a part onto a BGR float canvas at scale px per face unit."""
    left = round((box.left - origin[0]) * scale)
    top = round((box.top - origin[1]) * scale)
    size = (max(round(box.width * scale), 1), max(round(box.height * scale), 1))
    resized = cv2.resize(image, size, interpolation=cv2.INTER_AREA).astype(np.float64) / 255
    rows = slice(max(top, 0), min(top + size[1], canvas.shape[0]))
    columns = slice(max(left, 0), min(left + size[0], canvas.shape[1]))
    piece = resized[rows.start - top : rows.stop - top, columns.start - left : columns.stop - left]
    alpha = piece[..., 3:4]
    canvas[rows, columns] = piece[..., :3] * alpha + canvas[rows, columns] * (1 - alpha)


# A few moments of the face, each a list of parts to stack, back to front.
preview_faces = (
    ("가만히", ("base", "brows_left", "brows_right", "eyes_open", "mouth_idle")),
    ("깜빡", ("base", "brows_left", "brows_right", "eyes_closed", "mouth_idle")),
    ("아~", ("base", "brows_left", "brows_right", "eyes_open", "mouth_a")),
    ("오~", ("base", "brows_left", "brows_right", "eyes_open", "mouth_ou")),
    ("생각", ("base", "brows_left", "brows_right", "eyes_half", "mouth_thinking")),
)
# The face's cropped viewBox (face_view.tsx), a little wider.
preview_origin = (50.0, 58.0)
preview_size_units = (300, 270)
preview_scale = 1.6
# The face screen's background, styles.css .face_root (#10171c).
preview_background_bgr = (0x1C / 255, 0x17 / 255, 0x10 / 255)


def preview(parts: dict[str, tuple[part, np.ndarray]]) -> np.ndarray:
    """The faces side by side on the screen's background. Missing parts are skipped."""
    width = round(preview_size_units[0] * preview_scale)
    height = round(preview_size_units[1] * preview_scale)

    def face(keys: tuple[str, ...]) -> np.ndarray:
        canvas = np.ones((height, width, 3)) * np.array(preview_background_bgr)

        for key in (key for key in keys if key in parts):
            found, image = parts[key]
            place_part(canvas, image, found.box, preview_origin, preview_scale)

        return canvas

    tiles = [face(keys) for _, keys in preview_faces]
    gap = np.ones((height, 8, 3))

    return np.round(np.hstack([piece for tile in tiles for piece in (tile, gap)][:-1]) * 255).astype(np.uint8)


def write_parts(pages: dict[int, list[tuple[part, np.ndarray]]], out_dir: Path) -> dict[str, tuple[part, np.ndarray]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = {found.key: (found, image) for results in pages.values() for found, image in results}

    for found, image in parts.values():
        write_png(out_dir / found.file, image)

    manifest = {
        "pixels_per_unit": pixels_per_unit,
        "parts": {key: asdict(found) for key, (found, _) in sorted(parts.items())},
    }
    (out_dir / "parts.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_png(out_dir / "preview.png", preview(parts))

    return parts


def missing_required(parts: dict[str, tuple[part, np.ndarray]]) -> list[str]:
    return [
        placed.cell.key
        for placed in layout.placed_cells()
        if not placed.cell.optional and placed.cell.key not in parts
    ]


def main(arguments: list[str]) -> None:
    if len(arguments) < 3 or "--out" not in arguments[:-1] or arguments.index("--out") != len(arguments) - 2:
        raise SystemExit("usage: python -m sheet.extract <photo> [<photo> ...] --out <dir>")

    photos = [Path(argument) for argument in arguments[:-2]]
    out_dir = Path(arguments[-1])
    parts = write_parts(extract_photos(photos), out_dir)
    print(f"wrote {len(parts)} parts to {out_dir}: {', '.join(sorted(parts))}")

    missing = missing_required(parts)

    if missing:
        print(f"not drawn yet (or not photographed): {', '.join(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])
