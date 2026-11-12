"""The face as it is drawn today, as primitives the example sheet can print.

Mouth shapes are read from frontend/src/face_shapes.ts rather than copied, so
the example sheet follows the app when the tables are tuned. mouth_curves is
a port of mouth_path() there; eye, brow and cheek geometry are the JSX
literals of frontend/src/face_view.tsx and the stroke widths are styles.css.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from sheet.layout import face_point


face_shapes_path = Path(__file__).resolve().parents[3] / "frontend" / "src" / "face_shapes.ts"

ink_rgb = (0x7F / 255, 0xD6 / 255, 0xC4 / 255)
# On screen the cheeks are 16% ink over the background. That pale a tint on
# paper is under the extractor's ink threshold (and a child's cheeks are
# opaque anyway), so the example sheet prints them stronger - at 0.4 they
# still came out speckled, half on either side of the threshold.
cheek_opacity = 0.6
mouth_stroke_units = 10.0
brow_stroke_units = 9.0

eye_centers = (face_point(138, 168), face_point(262, 168))
eye_rx = 23.0
eye_ry_open = 27.0
eye_ry_closed = 2.5
eye_ry_half = (eye_ry_open + eye_ry_closed) / 2

brow_lines = (
    (face_point(112, 116), face_point(164, 110)),
    (face_point(236, 110), face_point(288, 116)),
)
cheek_centers = (face_point(104, 232), face_point(296, 232))
cheek_radius = 22.0


@dataclass(frozen=True)
class mouth_shape:
    half_width: float
    open: float
    round: float
    corner: float


@dataclass(frozen=True)
class face_shapes:
    mouth_center: face_point
    kappa: float
    visemes: dict[str, mouth_shape]
    moods: dict[str, mouth_shape]


def read_number(source: str, name: str) -> float:
    found = re.search(rf"const {name} = ([-\d.]+);", source)

    if found is None:
        raise ValueError(f"{face_shapes_path}: no `const {name} = <number>;`")

    return float(found.group(1))


def read_table(source: str, name: str) -> dict[str, mouth_shape]:
    block = re.search(rf"export const {name}: Record<[^>]+> = \{{(.*?)\n\}};", source, re.DOTALL)

    if block is None:
        raise ValueError(f"{face_shapes_path}: no table `{name}`")

    rows = re.findall(
        r"(\w+): \{ half_width: ([-\d.]+), open: ([-\d.]+), round: ([-\d.]+), corner: ([-\d.]+) \}",
        block.group(1),
    )

    return {key: mouth_shape(*(float(value) for value in values)) for key, *values in rows}


def load_face_shapes() -> face_shapes:
    source = face_shapes_path.read_text(encoding="utf-8")

    return face_shapes(
        mouth_center=face_point(read_number(source, "mouth_center_x"), read_number(source, "mouth_center_y")),
        kappa=read_number(source, "mouth_kappa"),
        visemes=read_table(source, "viseme_shapes"),
        moods=read_table(source, "mood_shapes"),
    )


def mouth_curves(shapes: face_shapes, shape: mouth_shape) -> tuple[tuple[face_point, ...], tuple[face_point, ...]]:
    """The two cubic Béziers of mouth_path(): upper lip left to right, lower lip back."""
    center = shapes.mouth_center
    width = shape.half_width * (1 - 0.22 * shape.round)
    upper = shape.open * (0.56 + 0.3 * shape.round)
    lower = shape.open * (0.78 + 0.14 * shape.round)
    lift = shape.corner * 9
    control = width * shapes.kappa

    left = face_point(center.x - width, center.y - lift)
    right = face_point(center.x + width, center.y - lift)

    return (
        (left, face_point(center.x - control, center.y - upper), face_point(center.x + control, center.y - upper), right),
        (right, face_point(center.x + control, center.y + lower), face_point(center.x - control, center.y + lower), left),
    )


def cheek_rgb() -> tuple[float, float, float]:
    """The cheek's translucent ink over white paper, as one opaque print colour."""
    return tuple(1 - cheek_opacity * (1 - channel) for channel in ink_rgb)
