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
    with its parts simply stacked along local-y (length) in placement order.

    `nested` holds *other* (usually narrower, usually sparser) instances spliced into this
    one's own leftover length instead of each claiming a full board-width column of their
    own — see _absorb_slack's docstring. Bounded to exactly one level: a nested instance
    never itself carries further nested instances."""
    width: float
    items: list[tuple[Part, bool, float, float]] = field(default_factory=list)  # (part, rotated, pw, ph)
    used: float = 0.0
    nested: list["_StripInstance"] = field(default_factory=list)


def _build_strip_instances(
    parts: list[Part], allow_rotation: bool, gap: float, width: float, height: float,
) -> tuple[list[_StripInstance], set[str]]:
    """Steps 1+2 of "strips" packing: pick each part's strip-width axis, then split each
    resulting width-group into one or more same-width strip instances via first-fit-
    decreasing on length. Returns (instances, unplaceable_part_ids) — a part whose own length
    exceeds `height` in the only orientation its grain allows never joins any instance;
    unplaceable_part_ids records exactly which ones, so a caller doing the *entire* job in one
    pass (pack_all_strips) doesn't have to rediscover this by noticing an id never got placed.
    A whole group whose *width* exceeds the board's own width (so no instance of it could
    ever fit any board, empty or not) is excluded the same way, checked here rather than left
    for the bin-packer to discover — `_bin_pack_instances` always finds *some* bin for an
    instance it's given (opening a new one if needed), so it must never be handed something
    that can't fit even a completely empty board.
    """
    # Grain-locked parts have only one valid
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
            # An orientation is only a candidate if it physically fits the board (strip width
            # within board width, strip length within board length). Frequency alone must not
            # pick an orientation that can never fit -- e.g. a 2262x832 part whose 2262 repeats
            # elsewhere would otherwise become a 2262mm-wide strip and be rejected outright.
            fits_as_is = part.cutLength + gap <= width + 1e-9 and part.cutWidth + gap <= height + 1e-9
            fits_swapped = part.cutWidth + gap <= width + 1e-9 and part.cutLength + gap <= height + 1e-9
            if fits_swapped and not fits_as_is:
                swap = True
            elif fits_as_is and not fits_swapped:
                swap = False
            else:
                swap = value_counts.get(wid_key, 0) > value_counts.get(len_key, 0)
            if swap:
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
    unplaceable_ids: set[str] = set()
    for group_width, group_parts in groups.items():
        if group_width + gap > width + 1e-9:
            # This group's whole strip-width exceeds the board's own width -- no instance of
            # it could ever fit any board, empty or not. Excluded before instance-building
            # rather than left for _bin_pack_instances to (wrongly) accept anyway.
            unplaceable_ids.update(p.id for p in group_parts)
            continue
        ordered = sorted(group_parts, key=lambda p: -assigned[p.id][2])
        open_instances: list[_StripInstance] = []
        for part in ordered:
            rotated, pw, ph = assigned[part.id]
            needed = ph + gap
            if needed > height + 1e-9:
                # Doesn't fit this board's usable length in the only orientation this group
                # allows it -- genuinely unplaceable on any board of this size, not just this
                # one; leave it out of every instance and record it directly.
                unplaceable_ids.add(part.id)
                continue
            target = next((inst for inst in open_instances if inst.used + needed <= height + 1e-9), None)
            if target is None:
                target = _StripInstance(width=pw)
                open_instances.append(target)
            target.items.append((part, rotated, pw, ph))
            target.used += needed
        all_instances.extend(open_instances)

    return all_instances, unplaceable_ids


def _absorb_slack(instances: list[_StripInstance], gap: float, height: float) -> list[_StripInstance]:
    """A bounded second cutting level: rather than let every instance's unused tail length
    (`height - inst.used`) sit as pure waste, try to splice *other* instances into it —
    side by side, within this instance's own width — instead of each claiming its own full
    board-width column elsewhere.

    This is exactly the structure a real MaxCut reference layout was found to use (decoded
    directly from its own PDF geometry, 2026-09-23): one board-width column can hold a single
    width for part of the board's length, then switch to several narrower parallel columns
    for the rest — not one width held constant the full board length, which is what the
    "strips" strategy's first cutting level alone produces. Nesting is bounded to exactly one
    level deep (a nested instance never itself hosts further nested instances) so a real
    operator only ever sees at most one extra round of cuts per column, never the unbounded
    jagged tree the free-rectangle engine ("balanced"/"edge") was built to avoid.

    Sparsest instances (least `used`, i.e. most flexible about whose slack they'll fit in)
    are tried as guests first, against the fullest remaining instances as host candidates
    first (since a full instance needs its own board-width column regardless, nesting a
    guest into it is free). Absorbing a guest only ever *removes* it from the top-level
    instance list handed to `_bin_pack_instances` — it can reduce the number of boards
    needed, never increase it, and a run that finds nothing to absorb behaves identically to
    before this function existed.
    """
    remaining_width = {id(inst): inst.width for inst in instances}
    is_host = {id(inst): False for inst in instances}
    absorbed: set[int] = set()
    guest_order = sorted(instances, key=lambda inst: inst.used)
    for guest in guest_order:
        gid = id(guest)
        if gid in absorbed or is_host[gid]:
            # already nested elsewhere, or already hosting something itself -- nesting stays
            # exactly one level deep, so a host can never also become a guest.
            continue
        # guest.used already bakes in a trailing gap after its own last item (see
        # _build_strip_instances), and rendering (_build_sheet_from_instances) starts a
        # nested guest immediately at the host's own y_cursor with no extra gap inserted --
        # so the fit check below must match that exactly, not double-count a second gap.
        needed_len = guest.used
        host_candidates = sorted(
            (inst for inst in instances if id(inst) != gid and id(inst) not in absorbed),
            key=lambda inst: -inst.used,
        )
        for host in host_candidates:
            hid = id(host)
            slack = height - host.used
            if needed_len <= slack + 1e-9 and guest.width + gap <= remaining_width[hid] + 1e-9:
                host.nested.append(guest)
                remaining_width[hid] -= guest.width + gap
                is_host[hid] = True
                absorbed.add(gid)
                break
    return [inst for inst in instances if id(inst) not in absorbed]


def _bin_pack_instances(instances: list[_StripInstance], width: float, gap: float) -> list[list[_StripInstance]]:
    """Step 3 of "strips" packing: first-fit-decreasing at the bin level — widest instance
    first, into the first already-open bin with room; open a new bin only when none fits.
    Returns *every* bin, not just the first — the caller decides how many of them to realize
    as actual sheets in this pass (pack_all_strips: all of them; the legacy single-board
    _place_parts_on_board_strips: only the first, for backward compatibility)."""
    ordered = sorted(instances, key=lambda inst: -inst.width)
    bins: list[list[_StripInstance]] = []
    bin_used_width: list[float] = []
    for inst in ordered:
        needed = inst.width + gap
        target_bin = next((bi for bi, uw in enumerate(bin_used_width) if uw + needed <= width + 1e-9), None)
        if target_bin is None:
            bins.append([inst])
            bin_used_width.append(needed)
        else:
            bins[target_bin].append(inst)
            bin_used_width[target_bin] += needed
    return bins


def _build_sheet_from_instances(
    instances: list[_StripInstance], board: StockBoard, margin: Margin, width: float, height: float, gap: float,
    sheet_index: int,
) -> Sheet:
    """Renders one bin's worth of strip instances into an actual Sheet: re-orders by leftover
    length (fullest first) so the emptiest instances cluster together at one edge instead of
    scattering their leftover space (see the "strips" docstring's own note on this), then lays
    out x/y positions, offcuts, and utilization exactly as a single-board strips pass always
    has."""
    instances = sorted(instances, key=lambda inst: height - inst.used)

    placed_parts: list[PlacedPart] = []
    offcuts: list[Offcut] = []
    x_cursor = 0.0
    for inst in instances:
        y_cursor = 0.0
        for part, rotated, pw, ph in inst.items:
            placed_parts.append(
                PlacedPart(
                    partId=part.id, x=x_cursor + margin.left, y=y_cursor + margin.top, rotated=rotated,
                    w=pw, h=ph, name=part.name, material=part.material, thickness=part.thickness, grain=part.grain,
                )
            )
            y_cursor += ph + gap

        # Second cutting level (see _absorb_slack): render any instances nested into this
        # one's own leftover length side by side, fullest first, each in its own narrower
        # x-sub-range within inst.width and its own y-range starting right after inst's own
        # items -- never past this instance's own width or leftover length, so this can only
        # ever consume space that would otherwise sit unused.
        nested_x = x_cursor
        nested_y_start = y_cursor
        for guest in sorted(inst.nested, key=lambda g: -g.used):
            guest_y = nested_y_start
            for part, rotated, pw, ph in guest.items:
                placed_parts.append(
                    PlacedPart(
                        partId=part.id, x=nested_x + margin.left, y=guest_y + margin.top, rotated=rotated,
                        w=pw, h=ph, name=part.name, material=part.material, thickness=part.thickness,
                        grain=part.grain,
                    )
                )
                guest_y += ph + gap
            guest_leftover = height - nested_y_start - guest.used
            if guest_leftover > 1e-6:
                offcuts.append(
                    Offcut(x=nested_x + margin.left, y=guest_y + margin.top, w=guest.width, h=guest_leftover)
                )
            nested_x += guest.width + gap

        leftover_len = height - inst.used
        if leftover_len > 1e-6:
            unused_nested_width = inst.width - (nested_x - x_cursor)
            if unused_nested_width > 1e-6:
                offcuts.append(
                    Offcut(x=nested_x + margin.left, y=inst.used + margin.top, w=unused_nested_width, h=leftover_len)
                )
        x_cursor += inst.width + gap

    leftover_width = width - x_cursor
    if leftover_width > 1e-6:
        offcuts.append(Offcut(x=x_cursor + margin.left, y=margin.top, w=leftover_width, h=height))

    board_area = width * height
    placed_area = sum(p.w * p.h for p in placed_parts)
    utilization = 0.0 if board_area <= 0 else round(placed_area / board_area * 100.0, 2)

    return Sheet(
        index=sheet_index, material=board.material, boardL=board.length, boardW=board.width,
        thickness=board.thickness, placed=placed_parts, offcuts=offcuts, utilizationPct=utilization,
    )


def pack_all_strips(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, start_sheet_index: int,
) -> tuple[list[Sheet], list[Part]]:
    """Packs an entire (material, thickness, grain) group's parts into as many "strips"
    sheets as needed, in one pass, instead of the usual one-board-per-call loop
    (`guillotine.py`'s `optimize()` dispatches here directly for `waste_strategy="strips"`,
    bypassing `place_parts_on_board`'s normal `while remaining:` loop entirely for this case).

    This exists because a board-by-board decision structurally can't do better than this:
    each call to a single-board packer only ever sees "whatever's left" and has no way to
    know how the instances it rejects will fare on a *later* board it hasn't planned yet --
    real bug, reported via a rendered PDF showing several sheets at 80-90%+ wastage
    (2026-09-23), traced to exactly this: a group's instances ending up stranded across
    multiple near-empty boards purely because of processing-order accident across repeated
    calls, not because that many boards were actually needed. Computing every board's
    instance assignment in one bin-packing pass (from the *entire* remaining group at once)
    fixes that structurally rather than tweaking the single-board heuristic further.
    """
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom
    instances, unplaceable_ids = _build_strip_instances(parts, allow_rotation, gap, width, height)
    instances = _absorb_slack(instances, gap, height)
    bins = _bin_pack_instances(instances, width, gap)
    sheets = [
        _build_sheet_from_instances(bin_instances, board, margin, width, height, gap, start_sheet_index + i)
        for i, bin_instances in enumerate(bins)
    ]
    unplaced = [p for p in parts if p.id in unplaceable_ids]
    return sheets, unplaced


def _place_parts_on_board_strips(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, sheet_index: int,
) -> tuple[Sheet, list[Part]]:
    """Legacy single-board entry point for "strips" packing, kept for direct callers/tests
    that want one board's worth at a time rather than `pack_all_strips`'s whole-group batch
    (which is what `guillotine.py` actually uses now — see that function's own docstring for
    why a single-board-per-call decision can't do as well). Realizes only the first bin from
    `_bin_pack_instances`; the rest are folded back into `still_remaining` exactly as before,
    with the whole `_build_strip_instances`/`_bin_pack_instances` computation simply redone
    from scratch on whatever's left the next time this is called.
    """
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom
    # Deliberately does NOT call _absorb_slack: this legacy function is only kept for direct
    # single-board callers/tests, never for the real "strips" dispatch path (guillotine.py
    # calls pack_all_strips directly, see its own docstring for why per-call, from-scratch
    # regrouping already loses information a whole-group pass has). Adding slack absorption
    # here too would just mask that same real gap for anyone still calling this directly.
    instances, unplaceable_ids = _build_strip_instances(parts, allow_rotation, gap, width, height)
    bins = _bin_pack_instances(instances, width, gap)
    accepted = bins[0] if bins else []
    sheet = _build_sheet_from_instances(accepted, board, margin, width, height, gap, sheet_index)
    placed_ids = {
        part.id
        for inst in accepted
        for part, *_ in inst.items + [item for guest in inst.nested for item in guest.items]
    }
    # Unplaceable parts (per unplaceable_ids) stay in still_remaining rather than being
    # dropped here -- this legacy function has no separate "unplaced" return slot, so it
    # relies on the same mechanism every other single-board packer does: the caller's
    # `while remaining:` loop retries them on a fresh board, gets an empty sheet.placed
    # again, and only then reports them unplaced.
    still_remaining = [p for p in parts if p.id not in placed_ids]
    return sheet, still_remaining


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
