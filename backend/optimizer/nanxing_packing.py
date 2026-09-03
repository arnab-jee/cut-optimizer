from __future__ import annotations
from dataclasses import dataclass

from .model import Margin, Part, PlacedPart, Sheet, StockBoard, Offcut, WasteStrategy

# This is the Nanxing router's own copy of the free-rectangle guillotine placement engine.
# optimizer/saw_packing.py holds an independent copy for the panel saw (Updates/update_003.md:
# "maintain separate packers" — the two machines previously shared one implementation via
# optimizer/packing.py; that consolidation was undone deliberately so each machine's packer can
# evolve on its own, even though the starting logic is currently the same).

EPS = 1e-6


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
    waste_strategy produced them."""
    rects = list(rects)
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            a = rects[i]
            for j in range(i + 1, len(rects)):
                b = rects[j]
                if abs(a.x - b.x) < EPS and abs(a.w - b.w) < EPS:
                    if abs((a.y + a.h) - b.y) < EPS:
                        rects[i] = Rectangle(a.x, a.y, a.w, a.h + b.h)
                        rects.pop(j)
                        merged = True
                        break
                    if abs((b.y + b.h) - a.y) < EPS:
                        rects[i] = Rectangle(a.x, b.y, a.w, a.h + b.h)
                        rects.pop(j)
                        merged = True
                        break
                if abs(a.y - b.y) < EPS and abs(a.h - b.h) < EPS:
                    if abs((a.x + a.w) - b.x) < EPS:
                        rects[i] = Rectangle(a.x, a.y, a.w + b.w, a.h)
                        rects.pop(j)
                        merged = True
                        break
                    if abs((b.x + b.w) - a.x) < EPS:
                        rects[i] = Rectangle(b.x, a.y, a.w + b.w, a.h)
                        rects.pop(j)
                        merged = True
                        break
            if merged:
                break
    return rects


def _footprint(part: Part, rotated: bool) -> tuple[float, float]:
    """Returns (pw, ph), the placement footprint's extent along the board's local x/y axes.
    `rotated` means the part has been physically turned 90 degrees from its own natural pose —
    it feeds directly into PlacedPart.rotated and the exported RotateAngle, so it must NOT
    simply mean "pw=cutWidth".

    For grain="length" parts, the natural (rotated=False) pose already has cutLength running
    along the board's length-derived axis — confirmed against real golden Nanxing machine data
    (207 grain="L" workpieces; e.g. WorkpieceId 26Y117T1F1B1_1001, CutLength=1323.4, placed
    with an X-span of ~1329mm on a 2440mm-length board, RotateAngle absent — i.e. the machine
    doesn't consider this a rotation at all). The previous version of this function always
    defaulted cutLength onto the board's *width*-derived axis regardless of grain, which is
    backwards for "length" grain and silently rejected any such part whose cutLength exceeded
    the board's width even though it fit easily along the length axis (Issues/issues_001.md).

    grain="none" parts share this same natural-pose baseline, not grain="width"'s — confirmed
    directly against two real machine-cut XML exports of the *same* job, one from this app, one
    from Fin China's own optimizer (results/26Y118_data/, 2026-09-02): for a grain="none" part,
    Fin China's RotateAngle="0" pose runs cutLength along the length axis, and its
    MachiningPoint="1" (the "not rotated" code) only makes sense under that same pose — matching
    grain="length"'s convention exactly, not grain="width"'s. place_parts_on_board requires
    this natural pose whenever it's achievable at all (a hard constraint as of pass 28 — see
    its own comment), falling back to the alternative only when the natural pose genuinely
    doesn't fit any board; before the original preference existed, the *only* orientation
    available with rotation disabled was already the wrong one for grain="none" parts, which is
    why toggling "allow rotation" off never changed the mislabeled-part symptom.
    """
    natural_swap = part.grain != "width"
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
        orientations = [False, True] if allow_rotation and part.can_rotate() else [False]
        # Prefer whichever orientation keeps CutLength running along the board's length axis
        # (ph == cutLength — see _footprint's own axis convention above) over pure packing
        # tightness. Grain-locked parts already get this for free (can_rotate() is False for
        # them, so there's only ever one orientation to try — their "natural" pose already
        # satisfies it, per _footprint's grain-aware swap). This only has a choice to make for
        # grain="none" parts with rotation allowed.
        #
        # A HARD constraint, not just a preference (CLAUDE.md pass 28) -- reverted from a
        # softer, searched preference (passes 22, 25, 26) once two things became clear: (1)
        # the original motivation, avoiding a mismatched-looking dimension label, no longer
        # applies at all -- pass 27 fixed the exporter so CutLength/CutWidth always follow
        # whichever axis the part actually landed on, for *any* orientation; (2) a real,
        # different bug was found instead (a physical machine screenshot showed the label
        # *placeholder* itself landing inside a neighboring part, not just off-center) that
        # correlates 100% with rotation (6/6 real rotated parts affected in one real job, 0/132
        # unrotated ones) and isn't explained by any exported field -- minimizing rotation is
        # the direct, practical mitigation regardless of the exact machine-side mechanism.
        # Verified this hard constraint can never leave a previously-placeable part unplaced:
        # only forced when the preferred pose is confirmed to fit a completely empty board, so
        # a fresh sheet is always guaranteed to accept it if the current one can't.
        if len(orientations) > 1:
            preferred = [r for r in orientations if _footprint(part, r)[1] == part.cutLength]
            if preferred:
                pref_pw, pref_ph = _footprint(part, preferred[0])
                fits_empty_board = (pref_pw + gap) <= width + 1e-9 and (pref_ph + gap) <= height + 1e-9
                orientation_groups = [preferred] if fits_empty_board else [preferred, [r for r in orientations if r not in preferred]]
            else:
                orientation_groups = [orientations]
        else:
            orientation_groups = [orientations]

        best_choice = None
        for group in orientation_groups:
            for rotated in group:
                pw, ph = _footprint(part, rotated)
                footprint_w = pw + gap
                footprint_h = ph + gap
                for rect_idx, rect in enumerate(free_rects):
                    if rect.can_fit(footprint_w, footprint_h):
                        short_side = min(rect.w - footprint_w, rect.h - footprint_h)
                        score = (short_side, rect.area())
                        if best_choice is None or score < best_choice[0]:
                            best_choice = (score, rect_idx, rotated, pw, ph)
            if best_choice is not None:
                break
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
