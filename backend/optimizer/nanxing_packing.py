from __future__ import annotations
from dataclasses import dataclass, replace

from .model import Margin, Part, PlacedPart, Sheet, StockBoard, Offcut, WasteStrategy

# This is the Nanxing router's own copy of the free-rectangle guillotine placement engine.
# optimizer/saw_packing.py holds an independent copy for the panel saw (Updates/update_003.md:
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


def _migrate_all_parts(
    parts_to_place: list[PlacedPart],
    targets: list[tuple[list[PlacedPart], list[Rectangle]]],
    margin: Margin, gap: float, waste_strategy: WasteStrategy,
) -> list[tuple[list[PlacedPart], list[Rectangle]]] | None:
    """Try to relocate every part in `parts_to_place` into one of `targets`' leftover free
    space (each target is that sheet's own (placed_parts, free_rects) pair). Each part keeps
    its already-decided footprint and orientation exactly as-is (w/h/rotated unchanged) --
    this relocates a part, it never re-decides which pose it should use, since the part was
    already placed via place_parts_on_board's own hard-constraint-correct orientation logic
    on its original sheet.

    Best-fit across ALL targets at once (not just the first one that fits), so a batch of
    migrating parts spreads across whichever targets have the roomiest matching leftover
    space, rather than piling everything onto one. Returns the updated targets (a fresh list,
    the input is never mutated) if every part found a home; returns None, with nothing
    changed, the moment any single part has nowhere to go -- an all-or-nothing move, since a
    partial migration would leave the sheet being dissolved with orphaned parts.
    """
    trial = [(list(placed), list(rects)) for placed, rects in targets]
    for part in sorted(parts_to_place, key=lambda p: -(p.w * p.h)):
        footprint_w, footprint_h = part.w + gap, part.h + gap
        best = None  # (score, target_idx, rect_idx)
        for ti, (_, rects) in enumerate(trial):
            for ri, rect in enumerate(rects):
                if rect.can_fit(footprint_w, footprint_h):
                    short_side = min(rect.w - footprint_w, rect.h - footprint_h)
                    score = (short_side, rect.area())
                    if best is None or score < best[0]:
                        best = (score, ti, ri)
        if best is None:
            return None
        _, ti, ri = best
        placed_list, rects = trial[ti]
        target_rect = rects.pop(ri)
        placed_list.append(replace(part, x=target_rect.x + margin.left, y=target_rect.y + margin.top))
        rects.extend(guillotine_split(target_rect, footprint_w, footprint_h, waste_strategy))
        trial[ti] = (placed_list, merge_free_rects(rects))
    return trial


def consolidate_sheets(
    sheets: list[Sheet], board: StockBoard, margin: Margin, gap: float, waste_strategy: WasteStrategy = "balanced",
) -> list[Sheet]:
    """Post-processing pass over a single (material, thickness, grain) group's already-packed
    sheets: repeatedly tries to dissolve the *least full* sheet by relocating every one of its
    parts into other sheets' real leftover free space, dropping the sheet entirely when that
    fully succeeds. `place_parts_on_board` fills sheets greedily, one at a time, and never
    revisits an earlier one -- so a part that would have fit comfortably into sheet 1's
    leftover space can end up stranded on its own sparse sheet 3 instead, real material this
    pass exists to recover (CLAUDE.md pass 29, prompted directly by a real annotated
    screenshot showing exactly this pattern: a 37.9%-full sheet sitting next to others with
    real unused width).

    Never changes which orientation any part uses (relocation only, via `_migrate_all_parts`)
    and never fails to preserve every part -- a sheet is only ever dropped once *all* of its
    parts have a confirmed new home; if even one doesn't fit anywhere else, that sheet (and
    every part on it) is left exactly as `place_parts_on_board` produced it.
    """
    if len(sheets) <= 1:
        return sheets
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom

    def offcuts_to_rects(sheet: Sheet) -> list[Rectangle]:
        return [Rectangle(o.x - margin.left, o.y - margin.top, o.w, o.h) for o in sheet.offcuts]

    state: list[tuple[list[PlacedPart], list[Rectangle]]] = [(list(s.placed), offcuts_to_rects(s)) for s in sheets]
    # `alive` tracks which original sheet indices survive, always kept in their *original*
    # relative order (index 0 first, etc.) so the final output preserves the same sheet order
    # place_parts_on_board produced -- separate from `attempt_order`, a throwaway ranking
    # (least-full first) used only to decide which sheet to *try* dissolving next.
    alive = list(range(len(sheets)))

    progressed = True
    while progressed and len(alive) > 1:
        progressed = False
        attempt_order = sorted(alive, key=lambda i: sum(p.w * p.h for p in state[i][0]))
        for idx in attempt_order:
            other_indices = [j for j in alive if j != idx]
            others = [state[j] for j in other_indices]
            migrated = _migrate_all_parts(state[idx][0], others, margin, gap, waste_strategy)
            if migrated is not None:
                for oi, new_state in zip(other_indices, migrated):
                    state[oi] = new_state
                alive.remove(idx)
                progressed = True
                break  # restart the ranking from the new set of still-alive sheets

    board_area = width * height
    result: list[Sheet] = []
    for i in alive:
        placed, rects = state[i]
        placed_area = sum(p.w * p.h for p in placed)
        utilization = 0.0 if board_area <= 0 else round(placed_area / board_area * 100.0, 2)
        offcuts = [Offcut(x=r.x + margin.left, y=r.y + margin.top, w=r.w, h=r.h) for r in rects if r.area() > 1e-6]
        result.append(replace(sheets[i], placed=placed, offcuts=offcuts, utilizationPct=utilization))
    return result
