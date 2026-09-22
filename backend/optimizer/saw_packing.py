from __future__ import annotations
from dataclasses import dataclass, field

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


@dataclass
class _StripInstance:
    """One full-length vertical strip: a fixed local-x width shared by every part inside it,
    with its parts simply stacked along local-y (length) in placement order."""
    width: float
    items: list[tuple[Part, bool, float, float]] = field(default_factory=list)  # (part, rotated, pw, ph)
    used: float = 0.0


def _place_parts_on_board_strips(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, sheet_index: int,
) -> tuple[Sheet, list[Part]]:
    """"strips" waste strategy: groups parts by shared width into full-length vertical strips —
    every strip holds exactly one width, and its parts are just stacked by length inside it. An
    operator manually cutting this only ever needs two kinds of cuts: a handful of full-length
    strip cuts across the whole board, then plain crosscuts within each strip — never a cut that
    starts or stops mid-board. Confirmed against a real MaxCut reference PDF the project owner
    uses for manual panel-saw work (2026-09-22): its own layouts follow exactly this pattern.
    This trades some material efficiency for that simplicity — measured on the real reference
    job, MaxCut's own strip layout had *higher* wastage than this app's free-rectangle packer on
    the same material/sheets, not lower — so this is an operator-convenience choice, not a
    strict efficiency win, which is why it's opt-in rather than the default.
    """
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom

    # Step 1: pick each part's (rotated, pw, ph). Grain-locked parts have only one valid
    # footprint (_footprint's own grain-mandated split, can_rotate()==False for them) — used
    # as-is. grain="none" parts are free to run either raw dimension (cutLength or cutWidth)
    # along the strip-width axis, since nothing downstream of the panel saw's own PDF/preview
    # cares which one "means" rotated here (unlike the Nanxing router, where a real machine
    # label convention forces a specific choice — see _footprint's own docstring). So instead
    # of defaulting to _footprint(part, False)'s pick (which favors cutLength for grain="none"
    # for an unrelated reason), each grain="none" part picks whichever of its two raw
    # dimensions is shared by more parts overall — maximizing how many parts land in the same
    # strip group, which is the entire point of this mode. Real-data check: a real 130-part
    # drawer-parts CSV has cutWidth repeat far more than cutLength (2 dominant widths cover 90
    # of 130 parts, vs. cutLength values that are mostly unique per cabinet opening) — this
    # heuristic finds that automatically rather than assuming which raw column repeats more.
    value_counts: dict[float, int] = {}
    for part in parts:
        if allow_rotation and part.can_rotate():
            for v in (part.cutLength, part.cutWidth):
                key = round(v, 1)
                value_counts[key] = value_counts.get(key, 0) + 1
        else:
            fixed_pw, _ = _footprint(part, False)
            key = round(fixed_pw, 1)
            value_counts[key] = value_counts.get(key, 0) + 1

    assigned: dict[str, tuple[bool, float, float]] = {}
    for part in parts:
        if allow_rotation and part.can_rotate():
            len_key, wid_key = round(part.cutLength, 1), round(part.cutWidth, 1)
            if value_counts.get(wid_key, 0) > value_counts.get(len_key, 0):
                assigned[part.id] = (True, part.cutWidth, part.cutLength)
            else:
                assigned[part.id] = (False, part.cutLength, part.cutWidth)
        else:
            pw, ph = _footprint(part, False)
            assigned[part.id] = (False, pw, ph)

    # Step 2: group by final chosen width, then split each group into one or more same-width
    # strip instances via first-fit-decreasing on length — a group's total length doesn't
    # necessarily fit in one board-length strip.
    groups: dict[float, list[Part]] = {}
    for part in parts:
        _, pw, _ = assigned[part.id]
        groups.setdefault(round(pw, 1), []).append(part)

    all_instances: list[_StripInstance] = []
    for group_parts in groups.values():
        ordered = sorted(group_parts, key=lambda p: -assigned[p.id][2])
        open_instances: list[_StripInstance] = []
        for part in ordered:
            rotated, pw, ph = assigned[part.id]
            needed = ph + gap
            if needed > height + 1e-9:
                # Doesn't fit this board's usable length in the only orientation this group
                # allows it — leave it out of every instance; it stays in still_remaining, and
                # the caller's genuinely-unplaceable handling takes over if that never changes.
                continue
            target = next((inst for inst in open_instances if inst.used + needed <= height + 1e-9), None)
            if target is None:
                target = _StripInstance(width=pw)
                open_instances.append(target)
            target.items.append((part, rotated, pw, ph))
            target.used += needed
        all_instances.extend(open_instances)

    # Step 3: decide which strip instances fit this board's width — widest first, but still
    # scanning every remaining instance afterward rather than stopping at the first miss, so a
    # later, narrower instance can still use whatever width is left. Simple and deterministic,
    # not a provably optimal knapsack fill.
    all_instances.sort(key=lambda inst: -inst.width)
    accepted: list[_StripInstance] = []
    used_width = 0.0
    for inst in all_instances:
        needed = inst.width + gap
        if used_width + needed <= width + 1e-9:
            accepted.append(inst)
            used_width += needed

    placed_ids = {part.id for inst in accepted for part, *_ in inst.items}
    still_remaining = [p for p in parts if p.id not in placed_ids]

    placed_parts: list[PlacedPart] = []
    offcuts: list[Offcut] = []
    x_cursor = 0.0
    for inst in accepted:
        y_cursor = 0.0
        for part, rotated, pw, ph in inst.items:
            placed_parts.append(
                PlacedPart(
                    partId=part.id, x=x_cursor + margin.left, y=y_cursor + margin.top, rotated=rotated,
                    w=pw, h=ph, name=part.name, material=part.material, thickness=part.thickness, grain=part.grain,
                )
            )
            y_cursor += ph + gap
        leftover_len = height - inst.used
        if leftover_len > 1e-6:
            offcuts.append(Offcut(x=x_cursor + margin.left, y=inst.used + margin.top, w=inst.width, h=leftover_len))
        x_cursor += inst.width + gap

    leftover_width = width - used_width
    if leftover_width > 1e-6:
        offcuts.append(Offcut(x=used_width + margin.left, y=margin.top, w=leftover_width, h=height))

    board_area = width * height
    placed_area = sum(p.w * p.h for p in placed_parts)
    utilization = 0.0 if board_area <= 0 else round(placed_area / board_area * 100.0, 2)

    return (
        Sheet(
            index=sheet_index, material=board.material, boardL=board.length, boardW=board.width,
            thickness=board.thickness, placed=placed_parts, offcuts=offcuts, utilizationPct=utilization,
        ),
        still_remaining,
    )


def place_parts_on_board(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, sheet_index: int,
    waste_strategy: WasteStrategy = "balanced",
) -> tuple[Sheet, list[Part]]:
    """Best-short-side-fit placement against a tracked list of free rectangles: every part
    is matched against every currently free rectangle on the sheet (not just the most
    recent one), so leftover space anywhere on the sheet can still be backfilled by a
    later, smaller part. `gap` is the clearance reserved around each part (saw kerf or
    router part-spacing — same role either way).

    `waste_strategy="strips"` dispatches to a completely different placement algorithm
    (_place_parts_on_board_strips) rather than just changing guillotine_split's cut axis like
    "balanced"/"edge" do — see that function's own docstring.
    """
    if waste_strategy == "strips":
        return _place_parts_on_board_strips(parts, board, margin, gap, allow_rotation, sheet_index)
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
