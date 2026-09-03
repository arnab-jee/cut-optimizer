from __future__ import annotations
import random
import time
from collections import Counter

from .model import Margin, OptResult, Part, Sheet, StockBoard, WasteStrategy
from .nanxing_packing import footprint_signature, place_parts_on_board
from .placement import DEFAULT_PLACEMENT_CORNER, PlacementCorner, mirror_sheet

# Candidate-pool sizes tried by the search wrapper below -- 1 always reproduces the plain
# deterministic algorithm's own single best choice, so the search can never do *worse* than
# not searching at all (the plain-algorithm result is always among the candidates it tries).
_CANDIDATE_POOL_CHOICES = (1, 2, 3, 4, 6)

# Only worth capping a duplicate-sized group at all once it has at least this many members
# still remaining when a sheet opens -- a group of 1 has nothing to spread across sheets.
_MIN_GROUP_SIZE_TO_CAP = 2

# Fraction of search trials that use group_caps instead of defer_probability -- see the
# comment at its one use site in optimize() for why this favors defer_probability so heavily.
_GROUP_CAPS_TRIAL_PROBABILITY = 0.15


def _sample_group_caps(remaining: list[Part], rng: random.Random) -> dict[tuple[float, float], int]:
    """For every duplicate-sized (grain="none") footprint still present in `remaining` at
    this exact sheet-opening, sample a fresh per-sheet cap on how many of that group may use
    their non-preferred (mismatched-looking) pose here -- see place_parts_on_board's own
    `group_caps` docstring for what the cap actually does. Recomputed fresh every time a new
    sheet opens (not once per trial) because the group's remaining count shrinks as earlier
    sheets place some of its members, and "how many of what's left to commit here" is
    inherently a per-sheet decision, not a whole-job one.
    """
    counts = Counter(footprint_signature(p) for p in remaining if p.grain == "none")
    return {
        sig: rng.randint(0, count)
        for sig, count in counts.items()
        if count >= _MIN_GROUP_SIZE_TO_CAP
    }


def _run_once(
    request_parts: list[Part], stock: list[StockBoard], margin: Margin, spacing: float,
    waste_strategy: WasteStrategy, placement_corner: PlacementCorner, allow_rotation: bool,
    search_rng: random.Random | None = None, candidate_pool: int = 1,
    use_group_caps: bool = False, defer_probability: float = 0.0,
) -> OptResult:
    """One full pass over every board/material/grain group -- exactly what `optimize()` has
    always done. `search_rng`/`candidate_pool` are the same opt-in, no-op-by-default search
    knobs `place_parts_on_board` takes; this function just threads them through its own
    board/grain loop. `use_group_caps` (sample a fresh per-sheet `group_caps`, see
    `_sample_group_caps`) and `defer_probability` (the per-encounter coin flip) are the two
    coexisting "defer a duplicate to a fresh sheet" moves `place_parts_on_board` supports --
    see `optimize()`'s own docstring for why both are offered rather than picking one.
    Called once, undecorated, for the plain deterministic path, and repeatedly (with a fresh
    `search_rng` each time, and each trial choosing which move(s) to exercise) by the
    multi-trial search below.
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
            while remaining:
                group_caps = _sample_group_caps(remaining, search_rng) if (search_rng is not None and use_group_caps) else None
                sheet, still_remaining = place_parts_on_board(
                    remaining, board, margin, spacing, allow_rotation, sheet_index, waste_strategy,
                    group_caps, search_rng, candidate_pool, defer_probability,
                )
                if not sheet.placed:
                    unplaced.extend(still_remaining)
                    break
                sheet = mirror_sheet(sheet, board, margin, placement_corner)
                sheets.append(sheet)
                sheet_index += 1
                remaining = still_remaining
    return OptResult(sheets=sheets, unplaced=unplaced)


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
    placement engine: many full-job trials, keeping whichever complete result scores best
    (see `_score`). The plain deterministic result is always the first candidate, so the
    search can never return something worse than not searching at all -- only equal or
    better.

    Why search instead of a fixed rule: real measurement (CLAUDE.md pass 25, 2026-09-03)
    found no single fixed rule closes the gap with Fin China's own optimizer, which takes
    30-40+ seconds on a comparable job (ours: 1-2s) -- strong evidence it explores many
    candidate layouts rather than applying one heuristic. An unconditional "always defer to
    the preferred pose" rule eliminates mismatches but costs real material (measured: 26Y118
    20->22 sheets, BEDROOM 3-4 68->70) -- worse than Fin China's own 19, not closer to it. A
    pure randomized-order multi-start (no deferring at all) plateaus quickly and leaves some
    jobs completely unimproved (nesting_machine_data.csv: 0/9375 trials changed anything --
    those mismatches are geometrically unavoidable, not a search problem).

    Each trial picks one of two "defer a duplicate to a fresh sheet" moves (see
    place_parts_on_board's own docstring for both): a per-sheet `group_caps` (pass 26) --
    motivated directly by real evidence that Fin China's own output *spreads* identical-sized
    parts across sheets (2/2/2/1 across 4 sheets, not 6+1 on one), a joint per-sheet decision
    about a whole group -- or the cruder per-part-encounter coin flip (pass 25's original
    version). **Both are kept, not just the more "principled" one**: real measurement found
    they aren't strictly ordered -- group_caps sometimes does *worse* than the plain coin
    flip (most likely because re-rolling a fresh independent choice at every single encounter
    explores more distinct candidate layouts per trial than a single deterministic-once cap
    does), and which one wins varies by job. Letting each trial pick lets the search use
    whichever move actually helps for the job at hand instead of this function guessing.

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
    master_rng = random.Random(search_seed)
    deadline = time.perf_counter() + search_time_budget_s
    while time.perf_counter() < deadline:
        trial_rng = random.Random(master_rng.random())
        candidate_pool = trial_rng.choice(_CANDIDATE_POOL_CHOICES)
        # Real measurement (CLAUDE.md pass 26) found defer_probability alone consistently
        # matches or beats group_caps across every real job tried -- most likely because a
        # fixed time budget split between two moves gives each less search depth than either
        # would get alone, and defer_probability's cheaper, more diverse per-encounter
        # randomness makes better use of that depth. Biased rather than dropped entirely: a
        # different job's structure could still favor group_caps, and this costs little.
        use_group_caps = trial_rng.random() < _GROUP_CAPS_TRIAL_PROBABILITY
        defer_probability = 0.0 if use_group_caps else trial_rng.random()
        result = _run_once(
            request_parts, stock, margin, spacing, waste_strategy, placement_corner, allow_rotation,
            trial_rng, candidate_pool, use_group_caps, defer_probability,
        )
        score = _score(result)
        if score < best_score:
            best_score = score
            best = result
    return best
