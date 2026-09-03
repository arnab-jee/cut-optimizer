from __future__ import annotations
import random
from collections import Counter
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
    grain="length"'s convention exactly, not grain="width"'s. The place_parts_on_board scoring
    below still prefers this natural pose over the alternative when both fit, but only falls
    back — never a hard constraint — so a part with no free rectangle shaped for the natural
    pose can still place; before this fix, the *only* orientation available with rotation
    disabled was already the wrong one for grain="none" parts, which is why toggling "allow
    rotation" off never changed the mislabeled-part symptom.
    """
    natural_swap = part.grain != "width"
    swap = natural_swap != rotated
    if swap:
        return part.cutWidth, part.cutLength
    return part.cutLength, part.cutWidth


def footprint_signature(part: Part) -> tuple[float, float]:
    """A rotation-independent identity for "this part is the same size as that one" —
    (cutLength, cutWidth) sorted, rounded to 0.1mm. Shared between this module's own
    group-cap enforcement below and nanxing.py's per-sheet cap sampling, so the two always
    agree on which parts belong to the same duplicate group."""
    return tuple(sorted((round(part.cutLength, 1), round(part.cutWidth, 1))))


def place_parts_on_board(
    parts: list[Part], board: StockBoard, margin: Margin, gap: float, allow_rotation: bool, sheet_index: int,
    waste_strategy: WasteStrategy = "balanced",
    group_caps: dict[tuple[float, float], int] | None = None,
    search_rng: random.Random | None = None,
    candidate_pool: int = 1,
    defer_probability: float = 0.0,
    _caps_disabled: bool = False,
) -> tuple[Sheet, list[Part]]:
    """Best-short-side-fit placement against a tracked list of free rectangles: every part
    is matched against every currently free rectangle on the sheet (not just the most
    recent one), so leftover space anywhere on the sheet can still be backfilled by a
    later, smaller part. `gap` is the clearance reserved around each part (saw kerf or
    router part-spacing — same role either way).

    `group_caps`, `search_rng`, `candidate_pool`, and `defer_probability` are all additive,
    opt-in search knobs used by nanxing.py's multi-trial search wrapper (see that module's
    `optimize()` `search_time_budget_s` parameter) — every one of them defaults to a no-op,
    so a caller that doesn't pass them gets byte-identical behavior to the plain
    deterministic algorithm this function has always run. Passed as `None`/the empty
    defaults, this function is unchanged from before the search wrapper existed.

    Two coexisting, independently-controllable "defer to a fresh sheet" moves, both scoped
    to duplicate-sized (footprint recurs elsewhere) parts about to use their non-preferred
    fallback pose:

    - `group_caps` (keyed by `footprint_signature`): a direct, per-sheet cap on how many
      members of a group are allowed to use the fallback pose on *this* sheet — once
      reached, further members defer. A deliberate, joint per-sheet decision about a whole
      group, motivated directly by real evidence (CLAUDE.md pass 26): Fin China's own
      output spreads identical parts 2/2/2/1 across sheets rather than 6+1 on one.
    - `defer_probability`: the original, cruder per-part-encounter coin flip (pass 25) —
      kept alongside `group_caps`, not replaced by it, because real measurement (pass 26)
      found the two aren't strictly ordered: `group_caps`' more "principled" joint decision
      sometimes does *worse* than the plain per-encounter coin flip, most likely because
      re-rolling a fresh independent choice at every single encounter explores more distinct
      candidate layouts per trial than a single deterministic-once-computed cap does, even
      though the cap is conceptually closer to what Fin China's output implies. The search
      wrapper tries both, so it can use whichever wins for a given job rather than this
      function picking a winner that isn't consistently one.

    Both are safe for the same reason: a genuine duplicate's preferred pose is proven
    achievable by its sibling(s), so deferring to a fresh sheet can never fail outright
    (though it can still need its *own* fallback there — this is capped/searched, not an
    unconditional defer, for exactly that reason).
    """
    width = board.width - margin.left - margin.right
    height = board.length - margin.top - margin.bottom
    free_rects = [Rectangle(0.0, 0.0, width, height)]
    placed_parts: list[PlacedPart] = []
    unplaced: list[Part] = []
    deferred_this_sheet = False
    fallback_count_by_sig: dict[tuple[float, float], int] = {}
    # Scopes `defer_probability` to genuine duplicates only, the same way `group_caps`' own
    # keys are always pre-scoped by its caller: deferring is only safe when *some* sibling of
    # the identical footprint proves the preferred pose is achievable, so a signature with
    # only one member left in `parts` right now must never be offered the defer_probability
    # roll, even if it's part of a job-wide duplicate group whose other members already
    # placed on an earlier sheet -- conservative (it can occasionally decline to defer a
    # straggler that would technically still be safe to), never unsafe.
    sig_counts = Counter(footprint_signature(p) for p in parts if p.grain == "none")
    for part in sorted(parts, key=lambda item: (-item.area(), -max(item.cutLength, item.cutWidth))):
        orientations = [False, True] if allow_rotation and part.can_rotate() else [False]
        # Prefer whichever orientation keeps CutLength running along the board's length axis
        # (ph == cutLength — see _footprint's own axis convention above) over pure packing
        # tightness. Grain-locked parts already get this for free (can_rotate() is False for
        # them, so there's only ever one orientation to try — their "natural" pose already
        # satisfies it, per _footprint's grain-aware swap). This only has a choice to make for
        # grain="none" parts with rotation allowed.
        #
        # Reported directly (2026-09-02, real NaccNesting screenshots): the machine's own
        # on-screen dimension label appears to always print the CutLength value along the
        # horizontal/length-axis position, regardless of a given part's actual RotateAngle —
        # so a part we place with CutLength running *vertically* (a numerically tighter fit,
        # but "sideways" to a human, and mismatched on the physical label the shop floor reads
        # off the screen) shows the right geometry but the wrong-looking number next to the
        # wrong edge. This is a preference, not a hard constraint: it only reorders which
        # orientation is *tried first* — if the preferred orientation doesn't fit anywhere on
        # the sheet, the other orientation is still tried, so nothing that used to fit becomes
        # unplaced. It can, in principle, leave a little more slack per part than the pure
        # tightest-fit choice would, at the benefit of correct-looking placement and correct
        # on-machine labels.
        if len(orientations) > 1:
            preferred = [r for r in orientations if _footprint(part, r)[1] == part.cutLength]
            orientation_groups = [preferred, [r for r in orientations if r not in preferred]] if preferred else [orientations]
        else:
            orientation_groups = [orientations]

        chosen_candidates = None
        used_fallback_group = False
        for group_idx, group in enumerate(orientation_groups):
            candidates = []
            for rotated in group:
                pw, ph = _footprint(part, rotated)
                footprint_w = pw + gap
                footprint_h = ph + gap
                for rect_idx, rect in enumerate(free_rects):
                    if rect.can_fit(footprint_w, footprint_h):
                        short_side = min(rect.w - footprint_w, rect.h - footprint_h)
                        score = (short_side, rect.area())
                        candidates.append((score, rect_idx, rotated, pw, ph))
            if candidates:
                chosen_candidates = candidates
                used_fallback_group = group_idx > 0
                break
        if chosen_candidates is None:
            unplaced.append(part)
            continue

        # Search wrapper's "hold back a duplicate for a fresh sheet instead of accepting the
        # mismatched-looking fallback pose now" move (see nanxing.py's optimize() docstring for
        # why this needs a search, not a fixed rule: an unconditional version of this either
        # wastes material -- the earlier "safe hard preference" experiment made 26Y118 go
        # 20->22 sheets -- or, unconditional the other way, risks leaving a whole sheet's worth
        # of parts spuriously unplaced if every remaining part on it happens to be deferrable at
        # once; `_caps_disabled` is this function's own safety net against exactly that, see
        # below). `group_caps` bounds how many of this part's duplicate-sized group get to use
        # the fallback pose on *this* sheet -- once that many have already accepted it, the rest
        # defer to the next (fresh) sheet instead. Deferring is always safe for a genuine
        # duplicate: a sibling of the identical size fitting the preferred pose (possibly on an
        # earlier sheet, possibly still to come) proves that pose is achievable on some board,
        # so retrying this part on a fresh sheet can never fail outright -- though it can still
        # need its *own* fallback there if that fresh sheet also runs out of the right-shaped
        # space, which is exactly why this is capped per-sheet and searched, not just deferred
        # unconditionally forever.
        if used_fallback_group and not _caps_disabled and group_caps is not None:
            sig = footprint_signature(part)
            cap = group_caps.get(sig)
            if cap is not None and fallback_count_by_sig.get(sig, 0) >= cap:
                unplaced.append(part)
                deferred_this_sheet = True
                continue
            fallback_count_by_sig[sig] = fallback_count_by_sig.get(sig, 0) + 1

        # The original, cruder per-encounter coin flip (pass 25) -- see this function's own
        # docstring for why it's kept alongside group_caps rather than replaced by it.
        # Independent of the group_caps check above: a trial normally drives only one of the
        # two (the other left at its inert default), but nothing stops both being active at
        # once if a future trial design wants to combine them.
        if (
            used_fallback_group and not _caps_disabled and search_rng is not None
            and defer_probability > 0.0 and sig_counts.get(footprint_signature(part), 0) >= 2
            and search_rng.random() < defer_probability
        ):
            unplaced.append(part)
            deferred_this_sheet = True
            continue

        if search_rng is not None and candidate_pool > 1:
            chosen_candidates.sort(key=lambda c: c[0])
            _, rect_idx, rotated, pw, ph = search_rng.choice(chosen_candidates[:candidate_pool])
        else:
            _, rect_idx, rotated, pw, ph = min(chosen_candidates, key=lambda c: c[0])
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

    # Safety net: if deferring left this entire sheet empty (every remaining part happened to
    # be a duplicate we chose to hold back), redo this exact sheet once with deferring turned
    # off so we always make progress when *something* is genuinely placeable -- otherwise
    # nanxing.py's caller would wrongly treat "we deferred everything" the same as "nothing
    # fits at all" and mark every remaining part permanently unplaced.
    if not placed_parts and deferred_this_sheet:
        return place_parts_on_board(
            parts, board, margin, gap, allow_rotation, sheet_index, waste_strategy,
            group_caps, search_rng, candidate_pool, defer_probability, _caps_disabled=True,
        )

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
