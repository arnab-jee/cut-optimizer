from __future__ import annotations
from dataclasses import replace
from typing import Literal

from .model import Margin, Offcut, PlacedPart, Sheet, StockBoard

# Not part of either packing engine (saw_packing.py / nanxing_packing.py stay deliberately
# un-shared per update_003.md — see CLAUDE.md's M2 row) — this is a pure post-placement
# coordinate transform, so sharing it doesn't touch the "two machines = two different
# optimizers" principle: it never influences which parts get placed where relative to each
# other, only where the whole already-decided layout sits on the board.
#
# Prompted by a real physical dry-run (Issues/issues_005.md): a demo job cut 6-7mm short on one
# axis, with CutOptimizer's default placement sitting in a different corner of the table than the
# machine's own inbuilt optimizer's placement for a comparable job. To test whether table
# position (vacuum-zone coverage, axis calibration, fence distance) affects cut accuracy, the
# *exact* layout needs to be reproducible in a different board corner — this lets the operator
# choose one without changing anything about how parts get nested.
PlacementCorner = Literal["bottom-left", "bottom-right", "top-left", "top-right"]
DEFAULT_PLACEMENT_CORNER: PlacementCorner = "bottom-left"


def mirror_sheet(sheet: Sheet, board: StockBoard, margin: Margin, corner: PlacementCorner) -> Sheet:
    """Reflects an already-packed sheet's placed parts and offcuts to a different board corner.
    "bottom-left" (the packer's native fill origin — see saw_packing.py's/nanxing_packing.py's
    free_rects seed) is a no-op. The other three corners are area- and shape-preserving
    reflections: a layout that's guillotine-decomposable, has no overlaps, and stays within
    margin bounds before mirroring stays exactly so afterward, since reflection is rigid.

    Axis note: PlacedPart.x/w run along the packer's local x axis (board.width-derived);
    PlacedPart.y/h run along local y (board.length-derived) — see saw_packing.py's
    place_parts_on_board. So a "right" corner (further along the board's *length*) mirrors y/h;
    a "top" corner (further along the board's *width*) mirrors x/w — matching how those words
    read in the machine's own on-screen layout (X horizontal/length, Y vertical/width, per M11).
    """
    if corner == DEFAULT_PLACEMENT_CORNER:
        return sheet

    mirror_width_axis = corner in ("top-left", "top-right")
    mirror_length_axis = corner in ("bottom-right", "top-right")
    x_min, x_max = margin.left, board.width - margin.right
    y_min, y_max = margin.top, board.length - margin.bottom

    def flip_x(x: float, w: float) -> float:
        return x_min + x_max - x - w if mirror_width_axis else x

    def flip_y(y: float, h: float) -> float:
        return y_min + y_max - y - h if mirror_length_axis else y

    placed = [replace(p, x=flip_x(p.x, p.w), y=flip_y(p.y, p.h)) for p in sheet.placed]
    offcuts = [replace(o, x=flip_x(o.x, o.w), y=flip_y(o.y, o.h)) for o in sheet.offcuts]
    return replace(sheet, placed=placed, offcuts=offcuts)
