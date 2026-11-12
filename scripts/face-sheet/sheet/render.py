"""Print the sheets: blank.pdf for drawing, example.pdf with today's face filled in.

    uv run python -m sheet.render out/

Print at 100% (actual size) if the printer dialog offers it. Scaling to fit
does no harm - the extractor measures the page from its corner markers - but
the parts would then be drawn a little smaller than the cells promise.
"""

import sys
from pathlib import Path

import cv2
import pymupdf

from sheet import current_face, layout
from sheet.layout import face_point, mm_rect, placed_cell


points_per_mm = 72 / 25.4
border_rgb = (0.55, 0.55, 0.55)
hint_rgb = (0.45, 0.45, 0.45)
font = "sheet_font"
# Any Korean TrueType font will do; the first one found is embedded. The
# built-in CJK font of PyMuPDF spaces Hangul too widely to read comfortably.
font_candidates = (
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
)


def font_file() -> Path:
    found = [candidate for candidate in font_candidates if candidate.exists()]

    if not found:
        raise FileNotFoundError(f"no Korean font; add one to font_candidates: {[str(path) for path in font_candidates]}")

    return found[0]


def pt(value_mm: float) -> float:
    return value_mm * points_per_mm


def pt_rect(rect: mm_rect) -> pymupdf.Rect:
    return pymupdf.Rect(pt(rect.left), pt(rect.top), pt(rect.right), pt(rect.bottom))


def draw_marker(target: pymupdf.Page, rect: mm_rect, marker: int) -> None:
    """Vector modules from the ArUco bit matrix: crisp at any printer resolution."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, layout.marker_dictionary))
    # One pixel per module: the 4x4 code plus its one-module black frame.
    modules = cv2.aruco.generateImageMarker(dictionary, marker, 6)
    module_mm = rect.width / modules.shape[0]
    shape = target.new_shape()

    for row, column in zip(*(modules == 0).nonzero()):
        shape.draw_rect(pt_rect(mm_rect(rect.left + column * module_mm, rect.top + row * module_mm, module_mm, module_mm)))

    # A hairline in the fill colour closes the seams between adjacent modules.
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.3)
    shape.commit()


def draw_cell_frame(target: pymupdf.Page, placed: placed_cell) -> None:
    rect = placed.rect
    center_x = rect.left + rect.width / 2
    center_y = rect.top + rect.height / 2
    tick = layout.tick_length_mm
    shape = target.new_shape()
    shape.draw_rect(pt_rect(rect))

    for start, end in (
        ((center_x, rect.top - tick), (center_x, rect.top)),
        ((center_x, rect.bottom), (center_x, rect.bottom + tick)),
        ((rect.left - tick, center_y), (rect.left, center_y)),
        ((rect.right, center_y), (rect.right + tick, center_y)),
    ):
        shape.draw_line(pymupdf.Point(pt(start[0]), pt(start[1])), pymupdf.Point(pt(end[0]), pt(end[1])))

    shape.finish(color=border_rgb, width=pt(0.4))
    shape.commit()

    target.insert_textbox(pt_rect(placed.label_rect), placed.cell.label, fontname=font, fontsize=12, align=0)


def draw_base_guides(target: pymupdf.Page, placed: placed_cell) -> None:
    """Where the eyes and mouth will sit, as labelled ticks outside the base cell's border."""
    rect = placed.rect
    tick = layout.tick_length_mm
    eye_y = layout.face_to_page(placed, current_face.eye_centers[0])[1]
    mouth_y = layout.face_to_page(placed, layout.mouth_anchor)[1]
    eye_xs = [layout.face_to_page(placed, center)[0] for center in current_face.eye_centers]
    marks = [
        ("눈", (rect.right, eye_y), (rect.right + tick, eye_y)),
        ("입", (rect.right, mouth_y), (rect.right + tick, mouth_y)),
        *[("눈", (x, rect.bottom), (x, rect.bottom + tick)) for x in eye_xs],
    ]
    shape = target.new_shape()

    for _, start, end in marks:
        shape.draw_line(pymupdf.Point(pt(start[0]), pt(start[1])), pymupdf.Point(pt(end[0]), pt(end[1])))

    shape.finish(color=border_rgb, width=pt(0.4))
    shape.commit()

    for text, _, end in marks:
        target.insert_text(pymupdf.Point(pt(end[0] + 0.8), pt(end[1] + 3.5)), text, fontname=font, fontsize=8, color=hint_rgb)


def to_point(placed: placed_cell, point: face_point) -> pymupdf.Point:
    x, y = layout.face_to_page(placed, point)

    return pymupdf.Point(pt(x), pt(y))


def units_to_pt(units: float) -> float:
    return pt(units * layout.mm_per_unit)


def draw_eyes(target: pymupdf.Page, placed: placed_cell, ry: float) -> None:
    shape = target.new_shape()

    for center in current_face.eye_centers:
        corner_a = to_point(placed, face_point(center.x - current_face.eye_rx, center.y - ry))
        corner_b = to_point(placed, face_point(center.x + current_face.eye_rx, center.y + ry))
        shape.draw_oval(pymupdf.Rect(corner_a, corner_b))

    shape.finish(color=current_face.ink_rgb, fill=current_face.ink_rgb, width=0)
    shape.commit()


def draw_brows(target: pymupdf.Page, placed: placed_cell) -> None:
    # closePath would turn each line into a closed subpath, which has no caps.
    shape = target.new_shape()

    for start, end in current_face.brow_lines:
        shape.draw_line(to_point(placed, start), to_point(placed, end))

    shape.finish(
        color=current_face.ink_rgb,
        width=units_to_pt(current_face.brow_stroke_units),
        lineCap=1,
        closePath=False,
    )
    shape.commit()


def draw_mouth(target: pymupdf.Page, placed: placed_cell, shapes: current_face.face_shapes, shape_key: str) -> None:
    mouth = shapes.visemes[shape_key] if shape_key in shapes.visemes else shapes.moods[shape_key]
    shape = target.new_shape()

    for curve in current_face.mouth_curves(shapes, mouth):
        shape.draw_bezier(*(to_point(placed, point) for point in curve))

    shape.finish(
        color=current_face.ink_rgb,
        fill=current_face.ink_rgb,
        width=units_to_pt(current_face.mouth_stroke_units),
        lineCap=1,
        lineJoin=1,
        closePath=True,
    )
    shape.commit()


def draw_cheeks(target: pymupdf.Page, placed: placed_cell) -> None:
    shape = target.new_shape()

    for center in current_face.cheek_centers:
        shape.draw_circle(to_point(placed, center), units_to_pt(current_face.cheek_radius))

    shape.finish(color=None, fill=current_face.cheek_rgb(), width=0)
    shape.commit()


def draw_example(target: pymupdf.Page, placed: placed_cell, shapes: current_face.face_shapes) -> None:
    key = placed.cell.key
    eye_ry = {
        "eyes_open": current_face.eye_ry_open,
        "eyes_half": current_face.eye_ry_half,
        "eyes_closed": current_face.eye_ry_closed,
    }

    if key == "base":
        draw_cheeks(target, placed)
    elif key in eye_ry:
        draw_eyes(target, placed, eye_ry[key])
    elif key == "brows":
        draw_brows(target, placed)
    elif key.startswith("mouth_"):
        draw_mouth(
            target,
            placed,
            shapes,
            placed.cell.visemes[0] if placed.cell.visemes else key.removeprefix("mouth_"),
        )
    else:
        raise ValueError(f"no example drawing for cell {key}")


def render(example: bool) -> pymupdf.Document:
    document = pymupdf.open()
    font_path = font_file()
    shapes = current_face.load_face_shapes()
    cells = layout.placed_cells()

    for page_index, sheet_page in enumerate(layout.pages):
        target = document.new_page(width=pt(layout.page_width_mm), height=pt(layout.page_height_mm))
        target.insert_font(fontname=font, fontfile=str(font_path))

        for corner, rect in enumerate(layout.marker_rects()):
            draw_marker(target, rect, layout.marker_id(page_index, corner))

        marker_inner = layout.margin_mm + layout.marker_size_mm + 4
        heading = mm_rect(marker_inner, layout.margin_mm, layout.page_width_mm - 2 * marker_inner, 18)
        subtitle = "예시 — 지금 얼굴" if example else "그려 주세요"
        target.insert_textbox(pt_rect(heading), f"{sheet_page.title}\n{subtitle}", fontname=font, fontsize=15, align=1)

        footer = mm_rect(
            marker_inner,
            layout.page_height_mm - layout.margin_mm - layout.marker_size_mm + 2,
            layout.page_width_mm - 2 * marker_inner,
            12,
        )
        target.insert_textbox(
            pt_rect(footer),
            f"{layout.page_hint}\n{page_index + 1} / {len(layout.pages)}",
            fontname=font,
            fontsize=9,
            color=hint_rgb,
            align=1,
        )

        for placed in (placed for placed in cells if placed.page_index == page_index):
            draw_cell_frame(target, placed)

            if placed.cell.key == "base":
                draw_base_guides(target, placed)

            if example:
                draw_example(target, placed, shapes)

    return document


def main(arguments: list[str]) -> None:
    if len(arguments) != 1:
        raise SystemExit("usage: python -m sheet.render <out_dir>")

    out_dir = Path(arguments[0])
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, example in (("blank.pdf", False), ("example.pdf", True)):
        render(example).save(out_dir / name)
        print(f"wrote {out_dir / name}")


if __name__ == "__main__":
    main(sys.argv[1:])
