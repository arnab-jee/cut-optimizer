from __future__ import annotations
import random
import time

from .model import Margin, OptResult, Part, Sheet, StockBoard, WasteStrategy
from .nanxing_packing import place_parts_on_board
from .placement import DEFAULT_PLACEMENT_CORNER, PlacementCorner, mirror_sheet

# Candidate-pool sizes tried by the search wrapper below -- 1 always reproduces the plain
# deterministic algorithm's own single best choice, so the search can never do *worse* than
# not searching at all (the plain-algorithm result is always among the candidates it tries).
_CANDIDATE_POOL_CHOICES = (1, 2, 3, 4, 6)


def _run_once(
    request_parts: list[Part], stock: list[StockBoard], margin: Margin, spacing: float,
    waste_strategy: WasteStrategy, placement_corner: PlacementCorner, allow_rotation: bool,
    duplicate_ids_by_group: dict[tuple[str, float, str], frozenset[str]] | None = None,
    search_rng: random.Random | None = None, defer_probability: float = 0.0, candidate_pool: int = 1,
) -> OptResult:
    """One full pass over every board/material/grain group -- exactly what `optimize()` has
    always done. `duplicate_ids_by_group`/`search_rng`/`defer_probability`/`candidate_pool`
    are the same opt-in, no-op-by-default search knobs `place_parts_on_board` takes; this
    function just threads them through its own board/grain loop. Called once, undecorated,
    for the plain deterministic path, and repeatedly (with different `search_rng` draws) by
    the multi-trial search below.
    """
    sheets: list[Sheet] = []
    unplaced: list[Part] = []
    sheet_index = 1
    for board in stock:
        board_parts = [part for part in request_parts if part.material == board.material and part.thickness == board.thickness]
        if not board_parts:
            continue
        # independent nesting job per (material, thickness, grain) — grain-locked parts
        # never share a sheet with a different grain requirement, even on the same board type
        for grain in sorted({part.grain for part in board_parts}):
            remaining = [part for part in board_parts if part.grain == grain]
            duplicate_ids = frozenset()
            if duplicate_ids_by_group is not None:
                duplicate_ids = duplicate_ids_by_group.get((board.material, board.thickness, grain), frozenset())
            while remaining:
                sheet, still_remaining = place_parts_on_board(
                    remaining, board, margin, spacing, allow_rotation, sheet_index, waste_strategy,
                    duplicate_ids, search_rng, defer_probability, candidate_pool,
                )
                if not sheet.placed:
                    unplaced.extend(still_remaining)
                    break
                sheet = mirror_sheet(sheet, board, margin, placement_corner)
                sheets.append(sheet)
                sheet_index += 1
                remaining = still_remaining
    return OptResult(sheets=sheets, unplaced=unplaced)


def _compute_duplicate_ids_by_group(request_parts: list[Part], stock: list[StockBoard]) -> dict[tuple[str, float, str], frozenset[str]]:
    """For every (material, thickness, grain="none") group, flags parts whose exact
    (cutLength, cutWidth) footprint recurs 2+ times in that same group as safe to defer (see
    place_parts_on_board's own docstring for the safety argument). Only grain="none" parts
    have an orientation choice worth deferring on -- grain-locked parts already have exactly
    one usable orientation, so there's nothing to defer.
    """
    result: dict[tuple[str, float, str], frozenset[str]] = {}
    for board in stock:
        board_parts = [p for p in request_parts if p.material == board.material and p.thickness == board.thickness and p.grain == "none"]
        if not board_parts:
            continue
        counts: dict[tuple[float, float], int] = {}
        for p in board_parts:
            sig = tuple(sorted((round(p.cutLength, 1), round(p.cutWidth, 1))))
            counts[sig] = counts.get(sig, 0) + 1
        ids = frozenset(
            p.id for p in board_parts
            if counts[tuple(sorted((round(p.cutLength, 1), round(p.cutWidth, 1))))] >= 2
        )
        if ids:
            result[(board.material, board.thickness, "none")] = ids
    return result


def _mismatch_count(sheets: list[Sheet]) -> int:
    """Grain-free placements NOT in the length-axis-preferred pose -- see
    place_parts_on_board's own docstring. A proxy for "will this part's on-screen label sit
    next to the wrong-looking edge on the real machine" (Issues/issues_003.md and CLAUDE.md
    passes 22-24's real-file investigation)."""
    return sum(1 for s in sheets for p in s.placed if p.grain == "none" and p.rotated)


def _score(result: OptResult) -> tuple[int, int, int, float]:
    """Lower is better. Priority order matches the project's own stated priorities: never
    leave more parts unplaced, then use fewer boards (real material cost), then fewer
    mismatched-looking labels, then higher utilization as a final tiebreak."""
    placed_area = sum(p.w * p.h for s in result.sheets for p in s.placed)
    board_area = sum(s.boardL * s.boardW for s in result.sheets) or 1.0
    return (len(result.unplaced), len(result.sheets), _mismatch_count(result.sheets), -placed_area / board_area)


def optimize(
    request_parts: list[Part], stock: list[StockBoard], margin: Margin, spacing: float,
    waste_strategy: WasteStrategy = "balanced", placement_corner: PlacementCorner = DEFAULT_PLACEMENT_CORNER,
    allow_rotation: bool = True,
    search_time_budget_s: float = 0.0, search_seed: int = 0,
) -> OptResult:
    """`search_time_budget_s=0` (the default -- every existing caller/test) runs exactly the
    single deterministic pass this function has always run, byte-identical to before this
    parameter existed.

    `search_time_budget_s > 0` opts into a multi-trial search on top of that same, unchanged
    placement engine: many full-job trials, each retrying with a different random defer/
    candidate-pool draw, keeping whichever complete result scores best (see `_score`). The
    plain deterministic result is always the first candidate, so the search can never return
    something worse than not searching at all -- only equal or better.

    Why search instead of a fixed rule: real measurement (CLAUDE.md, 2026-09-03) found no
    single fixed rule closes the gap with Fin China's own optimizer, which takes 30-40+
    seconds on a comparable job (ours: 1-2s) -- strong evidence it explores many candidate
    layouts rather than applying one heuristic. An unconditional "always defer to the
    preferred pose" rule eliminates mismatches but costs real material (measured: 26Y118
    20->22 sheets, BEDROOM 3-4 68->70) -- worse than Fin China's own 19, not closer to it. A
    pure randomized-order multi-start (no deferring) plateaus quickly and leaves some jobs
    completely unimproved (nesting_machine_data.csv: 0/9375 trials changed anything -- those
    mismatches are geometrically unavoidable, not a search problem). Only the combination --
    search *whether* to defer each duplicate, not just *how* to order/choose among what's
    already there -- found genuine, real improvement (BEDROOM 3-4: 68->67 sheets *and*
    72->52 mismatches, at zero placement risk, from the same real trial data).

    `search_seed` makes the search itself deterministic: the same parts+stock+params always
    explore the same trial sequence and return the same result, so /optimize's preview and
    /export/xml's re-run of the identical request-implied optimize() call agree -- this
    project has no mechanism to reuse a previous run's placement decisions across endpoints
    (see api.py), so if the search were allowed to vary run-to-run, a downloaded XML could
    silently differ from the preview the operator just checked.
    """
    baseline = _run_once(request_parts, stock, margin, spacing, waste_strategy, placement_corner, allow_rotation)
    if search_time_budget_s <= 0:
        return baseline

    best = baseline
    best_score = _score(baseline)
    # duplicate_ids_by_group can legitimately be empty (a job with no repeated part sizes) --
    # the search still runs in that case, since randomized candidate-pool selection alone
    # (always safe: it only reorders which already-fitting free rectangle gets used) can find
    # improvement on its own; defer_probability simply becomes a no-op with no duplicate ids
    # to ever match against.
    duplicate_ids_by_group = _compute_duplicate_ids_by_group(request_parts, stock)
    master_rng = random.Random(search_seed)
    deadline = time.perf_counter() + search_time_budget_s
    while time.perf_counter() < deadline:
        trial_rng = random.Random(master_rng.random())
        defer_probability = trial_rng.random()
        candidate_pool = trial_rng.choice(_CANDIDATE_POOL_CHOICES)
        result = _run_once(
            request_parts, stock, margin, spacing, waste_strategy, placement_corner, allow_rotation,
            duplicate_ids_by_group, trial_rng, defer_probability, candidate_pool,
        )
        score = _score(result)
        if score < best_score:
            best_score = score
            best = result
    return best
