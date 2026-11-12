"""Print -> photograph -> extract, with the photograph simulated.

The example sheet is today's face drawn in the cells, so cutting it back out
must give parts that sit where today's face parts sit - and the blank sheet
must give none. The simulated photo is tilted, unevenly lit, slightly blurred
and noisy, and the page lies on a table, so the markers, the lighting model
and the speck filter all have to do their job.
"""

import numpy as np
import pymupdf
import pytest

from sheet import current_face, extract, layout, render


photo_pixels_per_mm = 9.0


def page_image(document: pymupdf.Document, page_index: int) -> np.ndarray:
    pixmap = document[page_index].get_pixmap(dpi=round(photo_pixels_per_mm * 25.4))
    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)

    return rgb[..., 2::-1].copy()


def photograph(page: np.ndarray, seed: int) -> np.ndarray:
    """The page on a wooden-ish table, seen from an angle under a lamp."""
    import cv2

    random = np.random.default_rng(seed)
    height, width = page.shape[:2]
    out_width, out_height = round(width * 1.3), round(height * 1.2)
    source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    target = np.float32(
        [
            [out_width * 0.12, out_height * 0.06],
            [out_width * 0.86, out_height * 0.09],
            [out_width * 0.93, out_height * 0.95],
            [out_width * 0.06, out_height * 0.91],
        ]
    )
    homography = cv2.getPerspectiveTransform(source, target)
    table = np.full((out_height, out_width, 3), (60, 90, 130), dtype=np.uint8)
    photo = cv2.warpPerspective(page, homography, (out_width, out_height), dst=table, borderMode=cv2.BORDER_TRANSPARENT)

    grid_y, grid_x = np.mgrid[0:out_height, 0:out_width]
    light = 0.62 + 0.38 * (grid_x / out_width) * (0.6 + 0.4 * grid_y / out_height)
    warm = np.array([0.88, 0.97, 1.0])
    lit = photo.astype(np.float64) * light[..., None] * warm
    noisy = lit + random.normal(0, 3.0, lit.shape)

    return cv2.GaussianBlur(np.clip(noisy, 0, 255).astype(np.uint8), (3, 3), 0)


def extracted(example: bool) -> dict[str, tuple[extract.part, np.ndarray]]:
    document = render.render(example)

    return {
        found.key: (found, image)
        for page_index in range(len(layout.pages))
        for photo in [photograph(page_image(document, page_index), page_index)]
        for page_seen, homography in extract.detect_pages(photo).items()
        for found, image in extract.extract_page(photo, homography, page_seen)
    }


@pytest.fixture(scope="module")
def example_parts() -> dict[str, tuple[extract.part, np.ndarray]]:
    return extracted(True)


def test_every_page_is_found_by_its_markers() -> None:
    document = render.render(False)

    for page_index in range(len(layout.pages)):
        photo = photograph(page_image(document, page_index), page_index)
        assert list(extract.detect_pages(photo)) == [page_index]


def test_blank_sheet_gives_no_parts() -> None:
    assert extracted(False) == {}


def test_example_sheet_gives_every_part(example_parts) -> None:
    cell_keys = {placed.cell.key for placed in layout.placed_cells()}
    expected = (cell_keys - {"brows"}) | {"brows_left", "brows_right"}

    assert set(example_parts) == expected


def assert_box_near(box: layout.face_box, left: float, top: float, right: float, bottom: float) -> None:
    # The stroke's soft edge and the trim padding add up to ~1.5 units.
    tolerance = 2.5
    assert box.left == pytest.approx(left, abs=tolerance)
    assert box.top == pytest.approx(top, abs=tolerance)
    assert box.left + box.width == pytest.approx(right, abs=tolerance)
    assert box.top + box.height == pytest.approx(bottom, abs=tolerance)


def test_open_eyes_land_where_the_app_draws_them(example_parts) -> None:
    box = example_parts["eyes_open"][0].box
    left_eye, right_eye = current_face.eye_centers
    rx, ry = current_face.eye_rx, current_face.eye_ry_open

    assert_box_near(box, left_eye.x - rx, left_eye.y - ry, right_eye.x + rx, right_eye.y + ry)


def test_brows_are_split_into_their_own_halves(example_parts) -> None:
    half_stroke = current_face.brow_stroke_units / 2
    (left_start, left_end), (right_start, right_end) = current_face.brow_lines

    assert_box_near(
        example_parts["brows_left"][0].box,
        left_start.x - half_stroke,
        left_end.y - half_stroke,
        left_end.x + half_stroke,
        left_start.y + half_stroke,
    )
    assert_box_near(
        example_parts["brows_right"][0].box,
        right_start.x - half_stroke,
        right_start.y - half_stroke,
        right_end.x + half_stroke,
        right_end.y + half_stroke,
    )


def test_ink_keeps_its_colour_and_paper_turns_clear(example_parts) -> None:
    _, image = example_parts["eyes_open"]
    solid = image[..., 3] == 255
    ink_bgr = np.array(current_face.ink_rgb[::-1]) * 255

    assert (image[..., 3] == 0).any()
    # The photo's lamp is warm and uneven; the paper model divides both out.
    assert np.abs(image[solid][:, :3].mean(axis=0) - ink_bgr).max() < 25


def bgr_hue(color: np.ndarray) -> float:
    import cv2

    return float(cv2.cvtColor(color.reshape(1, 1, 3).astype(np.float32), cv2.COLOR_BGR2HSV)[0, 0, 0])


def test_black_ink_glows_in_the_face_ink_colour() -> None:
    black_and_grey = np.array([[[0.1, 0.1, 0.1], [0.45, 0.43, 0.44]]])

    assert np.allclose(extract.glow(black_and_grey), extract.glow_bgr, atol=1e-6)


def test_coloured_ink_keeps_its_hue_and_is_lifted() -> None:
    # Dark brown, deep red, navy: each must stay itself, only brighter.
    inks = np.array([[[0.12, 0.25, 0.42], [0.10, 0.10, 0.70], [0.45, 0.15, 0.10]]])
    glowing = extract.glow(inks)

    for before, after in zip(inks[0], glowing[0]):
        assert after.max() == pytest.approx(extract.glow_min_value, abs=1e-3)
        assert bgr_hue(after) == pytest.approx(bgr_hue(before), abs=1.0)
