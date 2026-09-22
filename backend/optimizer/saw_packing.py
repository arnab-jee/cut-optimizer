from __future__ import annotations
from dataclasses import dataclass

from .model import Margin, Part, PlacedPart, Sheet, StockBoard, Offcut, WasteStrategy

# This is the Panel Saw's own copy of the free-rectangle guillotine placement engine.
# optimizer/nanxing_packing.py holds an independent copy for the router (Updates/update_003.md:
# "maintain separate packers" — the two machines previously shared one implementation via
# optimizer/packing.py; that consolidation was undone deliberately so each machine's packer can
# evolve on its own, even though the starting logic is currently the same).

EPS = 1e-6

# Tolerance for treating two free rectangles as sharing an edge in merge_free_rects. Real CSV
# parts that are meant to sit in the "same lane" can differ by a fraction of a mm (e.g. 702mm vs
# 701.8mm) while getting the same kerf/gap — under the old exact (EPS) match, their leftover
# strips landed at x=706 vs x=705.8 and could never merge, permanently stranding the shorter
# strip as an unusably small island for the rest of that sheet. 0.5mm comfortably covers realistic
# CSV/rounding noise while staying far below any meaningful cut precision, so it won't merge two
# genuinely different lanes.
MERGE_EPS = 0.5


@dataclass
class Rectangle:
    x: float
    y: float
    w: float
    h: float

    def area(self) -> float:
        return max(self.w, 0.0) * max(self.h, 0.0)

    def can_fit(self, width: float, height: float) -> bool:
        return width <= self.w + 1e-9 and height <= self.h + 1e-9


def guillotine_split(free: Rectangle, pw: float, ph: float, waste_strategy: WasteStrategy = "balanced") -> list[Rectangle]:
    """Split `free` into up to two children after placing a pw x ph part in its
    bottom-left corner, using a single straight cut across the whole free rect.
    This guarantees the result is always a valid guillotine partition: no two free
    rectangles in the tree ever overlap.

    `waste_strategy` picks which axis that cut runs along:
    - "balanced" (default): cut along whichever axis leaves the *shorter* leftover strip,
      so each individual placement fits as tightly as possible. This is locally greedy and
      can fragment leftover space into many small pieces scattered across the sheet.
    - "edge": always cut vertically, so the "right" child always keeps the free rect's full
      height and the "top" child is only ever as wide as the part just placed. Leftover
      space then keeps accumulating into one shrinking strip per free region instead of
      being sliced into a new top-strip on every placement — consolidating wastage toward
      fewer, larger, edge-aligned regions.
    """
    remain_w = free.w - pw
    remain_h = free.h - ph
    if waste_strategy == "edge":
        horizontal_cut = False
    else:
        horizontal_cut = remain_w <= remain_h
    children: list[Rectangle] = []
    if horizontal_cut:
        # horizontal cut across the full width, above the part
        right = Rectangle(free.x + pw, free.y, remain_w, ph)
        top = Rectangle(free.x, free.y + ph, free.w, remain_h)
    else:
        # vertical cut across the full height, right of the part
        right = Rectangle(free.x + pw, free.y, remain_w, free.h)
        top = Rectangle(free.x, free.y + ph, pw, remain_h)
    if right.area() > 1e-6:
        children.append(right)
    if top.area() > 1e-6:
        children.append(top)
    return children


def merge_free_rects(rects: list[Rectangle]) -> list[Rectangle]:
    """Repeatedly merges pairs of free rectangles that share a full edge into one larger
    rectangle. guillotine_split alone can leave two freshly-created (or older) free
    rectangles sitting flush against each other — merging them keeps wastage consolidated
    into fewer, larger regions instead of staying fragmented, independent of which
    waste_strategy produced them.

    Edges are matched within MERGE_EPS rather than requiring an exact float match (see that
    constant's own comment for why real data needs this). When two candidates' matching edges
    are close but not bit-identical, the merged rectangle takes the *intersection* of their
    extents along that shared axis (never the union) — this is always safe, since both source
    rectangles were already proven free, so anything inside both of them is guaranteed free
    too; merging can only ever give up a sliver of claimed space, never invent any.
    """
    rects = list(rects)
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            a = rects[i]
            for j in range(i + 1, len(rects)):
                b = rects[j]
                if abs(a.x - b.x) < MERGE_EPS and abs(a.w - b.w) < MERGE_EPS:
                    mx = max(a.x, b.x)
                    mw = min(a.x + a.w, b.x + b.w) - mx
                    if abs((a.y + a.h) - b.y) < MERGE_EPS:
                        rects[i] = Rectangle(mx, a.y, mw, a.h + b.h)
                        rects.pop(j)
                        merged = True
                        break
                    if abs((b.y + b.h) - a.y) < MERGE_EPS:
                        rects[i] = Rectangle(mx, b.y, mw, a.h + b.h)
                        rects.pop(j)
                        merged = True
                        break
                if abs(a.y - b.y) < MERGE_EPS and abs(a.h - b.h) < MERGE_EPS:
                    my = max(a.y, b.y)
                    mh = min(a.y + a.h, b.y + b.h) - my
                    if abs((a.x + a.w) - b.x) < MERGE_EPS:
                        rects[i] = Rectangle(a.x, my, a.w + b.w, mh)
                        rects.pop(j)
                        merged = True
                        break
                    if abs((b.x + b.w) - a.x) < MERGE_EPS:
                        rects[i] = Rectangle(b.x, my, a.w + b.w, mh)
                        rects.pop(j)
                        merged = True
                        break
            if merged:
                break
    return rects


def _footprint(part: Part, rotated: bool) -> tuple[float, float]:
    """Returns (pw, ph), the placement footprint's extent along the board's local x/y axes.
    `rotated` means the part has been physically turned 90 degrees from its own natural,
    grain-mandated pose — it feeds directly into PlacedPart.rotated and the exported
    RotateAngle, so it must NOT simply mean "pw=cutWidth".

    For grain="length" parts, the natural (rotated=False) pose already has cutLength running
    along the board's length-derived axis — confirmed against real golden Nanxing machine data
    (207 grain="L" workpieces; e.g. WorkpieceId 26Y117T1F1B1_1001, CutLength=1323.4, placed
    with an X-span of ~1329mm on a 2440mm-length board, RotateAngle absent — i.e. the machine
    doesn't consider this a rotation at all). The previous version of this function always
    defaulted cutLength onto the board's *width*-derived axis regardless of grain, which is
    backwards for "length" grain and silently rejected any such part whose cutLength exceeded
    the board's width even though it fit easily along the length axis (Issues/issues_001.md).
    grain="width"/"none" parts are unaffected — their natural pose was already correct.
    """
    natural_swap = part.grain == "length"
    swap = natural_swap != rotated
    if swap:
        return part.cutWidth, part.cutLength
    return part.cutLength, part.cutWidth


def place_parts_on_board(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, sheet_index: int,
    waste_strategy: WasteStrategy = "balanced",
) -> tuple[Sheet, list[Part]]:
    """Best-short-side-fit placement against a tracked list of free rectangles: every part
    is matched against every currently free rectangle on the sheet (not just the most
    recent one), so leftover space anywhere on the sheet can still be backfilled by a
    later, smaller part. `gap` is the clearance reserved around each part (saw kerf or
    router part-spacing — same role either way).
    """
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom
    free_rects = [Rectangle(0.0, 0.0, width, height)]
    placed_parts: list[PlacedPart] = []
    unplaced: list[Part] = []
    for part in sorted(parts, key=lambda item: (-item.area(), -max(item.cutLength, item.cutWidth))):
        best_choice = None
        orientations = [False, True] if allow_rotation and part.can_rotate() else [False]
        for rotated in orientations:
            pw, ph = _footprint(part, rotated)
            footprint_w = pw + gap
            footprint_h = ph + gap
            for rect_idx, rect in enumerate(free_rects):
                if rect.can_fit(footprint_w, footprint_h):
                    short_side = min(rect.w - footprint_w, rect.h - footprint_h)
                    score = (short_side, rect.area())
                    if best_choice is None or score < best_choice[0]:
                        best_choice = (score, rect_idx, rotated, pw, ph)
        if best_choice is None:
            unplaced.append(part)
            continue
        _, rect_idx, rotated, pw, ph = best_choice
        target_rect = free_rects.pop(rect_idx)
        placed_parts.append(
            PlacedPart(
                partId=part.id,
                x=target_rect.x + margin.left,
                y=target_rect.y + margin.top,
                rotated=rotated,
                w=pw,
                h=ph,
                name=part.name,
                material=part.material,
                thickness=part.thickness,
                grain=part.grain,
            )
        )
        free_rects.extend(guillotine_split(target_rect, pw + gap, ph + gap, waste_strategy))
        free_rects = merge_free_rects(free_rects)
    board_area = width * height
    placed_area = sum(p.w * p.h for p in placed_parts)
    utilization = 0.0 if board_area <= 0 else round(placed_area / board_area * 100.0, 2)
    offcuts = [
        Offcut(x=r.x + margin.left, y=r.y + margin.top, w=r.w, h=r.h) for r in free_rects if r.area() > 1e-6
    ]
    return (
        Sheet(
            index=sheet_index,
            material=board.material,
            boardL=board.length,
            boardW=board.width,
            thickness=board.thickness,
            placed=placed_parts,
            offcuts=offcuts,
            utilizationPct=utilization,
        ),
        unplaced,
    )
