"""The drawing sheet, defined once: pages, cells, markers, and where each sits.

The renderer prints from this and the extractor cuts from this, so a cell can
never be printed in one place and looked for in another. Everything on paper
is in millimetres; everything about the face is in the face's own 0..400
units (frontend/src/face_view.tsx), and every cell maps between the two at the
same scale, so a part drawn on paper keeps its size relative to the others.
"""

from dataclasses import dataclass


page_width_mm = 210.0
page_height_mm = 297.0
margin_mm = 10.0

# One face unit on paper. 0.6 makes the eye cell 12 cm wide - roomy for a
# child's marker - and keeps the five talking mouths on one A4 page.
mm_per_unit = 0.6

# Four ArUco markers per page, one in each corner. Their ids say which page a
# photo shows (page index * 4 + corner), so pages can be photographed in any
# order, and their corners give the perspective correction.
marker_size_mm = 14.0
marker_dictionary = "DICT_4X4_50"
markers_per_page = 4

header_bottom_mm = 30.0
footer_top_mm = page_height_mm - margin_mm - marker_size_mm - 4.0
label_height_mm = 9.0
row_gap_mm = 8.0
column_gap_mm = 10.0
# Center ticks sit outside the cell border, so nothing printed lies inside the
# area that gets cut out.
tick_length_mm = 3.0
# The cut stays this far inside the border, clear of the printed line even
# after a slightly off perspective correction.
cut_inset_mm = 1.5


@dataclass(frozen=True)
class face_point:
    x: float
    y: float


@dataclass(frozen=True)
class face_box:
    """A rectangle in face units."""

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class mm_rect:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


@dataclass(frozen=True)
class cell:
    key: str
    label: str
    # Where the cell's centre lands on the face, and how much face it covers.
    anchor: face_point
    width_units: float
    height_units: float
    optional: bool
    # Cut into a left and a right part at the centre line - for a pair drawn
    # together whose halves move on their own (the brows).
    split: bool
    # Which viseme kinds this mouth stands for; empty for everything else. The
    # first one is the shape the example sheet draws.
    visemes: tuple[str, ...]

    @property
    def face_area(self) -> face_box:
        return face_box(
            left=self.anchor.x - self.width_units / 2,
            top=self.anchor.y - self.height_units / 2,
            width=self.width_units,
            height=self.height_units,
        )


@dataclass(frozen=True)
class page:
    title: str
    # Rows top to bottom; cells in a row are centred left to right.
    rows: tuple[tuple[cell, ...], ...]


@dataclass(frozen=True)
class placed_cell:
    cell: cell
    page_index: int
    rect: mm_rect
    label_rect: mm_rect


# Face geometry the cells are centred on: frontend/src/face_view.tsx (eyes at
# y 168, brows at ~113, the cropped viewBox 60 68 280 250) and face_shapes.ts
# (mouth centre 200, 268).
eyes_anchor = face_point(200, 168)
brows_anchor = face_point(200, 113)
mouth_anchor = face_point(200, 268)
base_anchor = face_point(200, 193)

eye_cell_size = (200.0, 90.0)
mouth_cell_size = (130.0, 100.0)


def eye_cell(key: str, label: str, optional: bool) -> cell:
    return cell(key, label, eyes_anchor, *eye_cell_size, optional=optional, split=False, visemes=())


def mouth_cell(key: str, label: str, visemes: tuple[str, ...]) -> cell:
    return cell(key, label, mouth_anchor, *mouth_cell_size, optional=False, split=False, visemes=visemes)


pages: tuple[page, ...] = (
    page(
        title="1. 얼굴 바탕",
        rows=(
            (
                cell(
                    "base",
                    "볼·점·머리핀처럼 늘 그대로인 것 (눈과 입은 다음 장에)",
                    base_anchor,
                    300.0,
                    270.0,
                    optional=True,
                    split=False,
                    visemes=(),
                ),
            ),
        ),
    ),
    page(
        title="2. 눈 — 두 눈을 한 칸에 같이",
        rows=(
            (eye_cell("eyes_open", "뜬 눈", optional=False),),
            (eye_cell("eyes_half", "반쯤 감은 눈 (안 그려도 돼요)", optional=True),),
            (eye_cell("eyes_closed", "감은 눈", optional=False),),
        ),
    ),
    page(
        title="3. 말하는 입 — 거울 보고 소리 내 보면서",
        rows=(
            (
                mouth_cell("mouth_a", "'아~' 하는 입", ("a",)),
                mouth_cell("mouth_ei", "'이~' 하는 입", ("e", "i")),
            ),
            (
                mouth_cell("mouth_ou", "'오~' 하는 입", ("o", "u")),
                mouth_cell("mouth_m", "'음~' 꼭 다문 입", ("m",)),
            ),
            (mouth_cell("mouth_nx", "살짝 벌린 입 (말하다 잠깐 쉴 때)", ("n", "x")),),
        ),
    ),
    page(
        title="4. 표정 입과 눈썹",
        rows=(
            (
                mouth_cell("mouth_idle", "가만히 있을 때 (웃는 입)", ()),
                mouth_cell("mouth_listening", "귀 기울여 들을 때", ()),
            ),
            (mouth_cell("mouth_thinking", "곰곰이 생각할 때", ()),),
            (
                cell(
                    "brows",
                    "눈썹 두 개 (안 그려도 돼요)",
                    brows_anchor,
                    220.0,
                    50.0,
                    optional=True,
                    split=True,
                    visemes=(),
                ),
            ),
        ),
    ),
)

page_hint = "굵은 사인펜으로, 칸 가운데 눈금에 맞춰 그려요. 칸 밖은 잘려요."


def cell_size_mm(target: cell) -> tuple[float, float]:
    return target.width_units * mm_per_unit, target.height_units * mm_per_unit


def row_height_mm(row: tuple[cell, ...]) -> float:
    return label_height_mm + max(cell_size_mm(target)[1] for target in row)


def place_row(row: tuple[cell, ...], page_index: int, top: float) -> list[placed_cell]:
    widths = [cell_size_mm(target)[0] for target in row]
    row_width = sum(widths) + column_gap_mm * (len(row) - 1)
    lefts = [
        (page_width_mm - row_width) / 2 + sum(widths[:index]) + column_gap_mm * index
        for index in range(len(row))
    ]

    return [
        placed_cell(
            cell=target,
            page_index=page_index,
            rect=mm_rect(left, top + label_height_mm, *cell_size_mm(target)),
            label_rect=mm_rect(left, top, cell_size_mm(target)[0], label_height_mm),
        )
        for target, left in zip(row, lefts)
    ]


def place_page(page_index: int) -> list[placed_cell]:
    """Rows stacked from the header down, the whole block centred vertically."""
    rows = pages[page_index].rows
    heights = [row_height_mm(row) for row in rows]
    block_height = sum(heights) + row_gap_mm * (len(rows) - 1)
    available = footer_top_mm - header_bottom_mm

    if block_height > available:
        raise ValueError(f"page {page_index + 1}: rows need {block_height:.0f} mm, {available:.0f} mm fit")

    first_top = header_bottom_mm + (available - block_height) / 2
    tops = [first_top + sum(heights[:index]) + row_gap_mm * index for index in range(len(rows))]

    return [placed for row, top in zip(rows, tops) for placed in place_row(row, page_index, top)]


def placed_cells() -> list[placed_cell]:
    return [placed for page_index in range(len(pages)) for placed in place_page(page_index)]


def marker_rects() -> list[mm_rect]:
    """Corner markers in id order within a page: top-left, top-right, bottom-right, bottom-left."""
    near = margin_mm
    far_x = page_width_mm - margin_mm - marker_size_mm
    far_y = page_height_mm - margin_mm - marker_size_mm

    return [
        mm_rect(near, near, marker_size_mm, marker_size_mm),
        mm_rect(far_x, near, marker_size_mm, marker_size_mm),
        mm_rect(far_x, far_y, marker_size_mm, marker_size_mm),
        mm_rect(near, far_y, marker_size_mm, marker_size_mm),
    ]


def marker_id(page_index: int, corner: int) -> int:
    return page_index * markers_per_page + corner


def face_to_page(placed: placed_cell, point: face_point) -> tuple[float, float]:
    """A face-unit point inside a cell, in page millimetres."""
    area = placed.cell.face_area

    return (
        placed.rect.left + (point.x - area.left) * mm_per_unit,
        placed.rect.top + (point.y - area.top) * mm_per_unit,
    )
