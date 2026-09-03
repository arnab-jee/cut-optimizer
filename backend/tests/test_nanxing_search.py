from __future__ import annotations

import random

from optimizer.model import Margin, Part, StockBoard
from optimizer.nanxing import (
    _mismatch_count,
    _sample_group_caps,
    _score,
    optimize as nanxing_optimize,
)
from optimizer.nanxing_packing import footprint_signature, place_parts_on_board

from .conftest import CSV_SAMPLE_DIR, SAMPLE_DATA_DIR
from .helpers import default_stock_for
from optimizer.parser import parse_csv_text

# Real side-by-side evidence for this whole search mechanism (CLAUDE.md passes 23-26): nesting-
# pro's own XML export vs. Fin China's own optimizer, same real job, same CSV.
REAL_26Y118_CSV = SAMPLE_DATA_DIR.parent / "results" / "26Y118_data" / "26Y118_(Nesting_Machine) - Sheet1.csv"

# CLAUDE.md passes 25-26 (2026-09-03): real measurement found no *fixed rule* on top of
# place_parts_on_board's existing free-rectangle engine closes the gap with Fin China's own
# optimizer (which spends 30-40+ seconds on a comparable job, vs. ours at 1-2s) — an
# unconditional hard preference eliminates mismatches but costs real material (26Y118: 20->22
# sheets), and a pure randomized-order multi-start plateaus with zero improvement on some real
# jobs. This module's `optimize(..., search_time_budget_s=...)` is a genuinely different,
# opt-in mechanism: run many full-job trials, each trying one of two "defer a duplicate to a
# fresh sheet" moves (a per-sheet group_caps, pass 26's more "principled" joint decision about
# a whole duplicate-sized group; or defer_probability, pass 25's cruder per-part-encounter coin
# flip) — kept alongside each other rather than one replacing the other, since real measurement
# found they aren't strictly ordered (group_caps sometimes does *worse*, most likely because
# splitting a fixed time budget between two moves gives each less search depth) — keeping
# whichever complete result scores best (unplaced, then sheets, then mismatched-looking labels,
# then utilization) — the plain deterministic result is always the first candidate, so it can
# never do worse than not searching at all.


def _part(cutLength: float, cutWidth: float, grain: str = "none", id: str = "P") -> Part:
    return Part(
        id=id, posId=id, name=id, cutLength=cutLength, cutWidth=cutWidth,
        finishedLength=cutLength, finishedWidth=cutWidth, thickness=18.0, qty=1,
        material="MAT", grain=grain, edges={"l1": "", "l2": "", "w1": "", "w2": ""},
    )


def test_zero_budget_is_byte_identical_to_omitting_the_parameter():
    # search_time_budget_s=0 (the default -- every pre-existing caller) must reproduce exactly
    # what optimize() has always returned, since it's the first thing this project's own real
    # callers (api.py's HTTP tests) rely on staying fast and unchanged.
    parts = [_part(1013.8, 528.8, id="26Y118T1F1A1_1246")]
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=10, right=10, bottom=10, left=10)
    without_param = nanxing_optimize(parts, stock, margin, spacing=6.0)
    with_zero = nanxing_optimize(parts, stock, margin, spacing=6.0, search_time_budget_s=0.0)
    assert without_param.sheets == with_zero.sheets
    assert without_param.unplaced == with_zero.unplaced


def test_sample_group_caps_only_flags_repeated_grain_free_footprints():
    parts = [
        _part(900.0, 300.0, id="dup1"),
        _part(900.0, 300.0, id="dup2"),  # same footprint as dup1 -> a real duplicate group
        _part(500.0, 200.0, id="unique"),  # occurs once -> no group, nothing to cap
        _part(300.0, 900.0, grain="length", id="grain-locked-dup"),  # grain != "none" -> ignored
        _part(300.0, 900.0, grain="length", id="grain-locked-dup2"),
    ]
    caps = _sample_group_caps(parts, random.Random(0))
    dup_sig = footprint_signature(parts[0])
    assert set(caps.keys()) == {dup_sig}
    assert 0 <= caps[dup_sig] <= 2  # sampled cap must stay within the group's actual size


def test_sample_group_caps_stays_within_bounds_across_many_seeds():
    # Property check, not a single fixed-seed assertion: whatever the roll, a cap must never
    # exceed how many members of the group are actually present to cap.
    parts = [_part(900.0, 300.0, id=f"d{i}") for i in range(5)]
    sig = footprint_signature(parts[0])
    for seed in range(50):
        caps = _sample_group_caps(parts, random.Random(seed))
        assert 0 <= caps[sig] <= 5


def test_search_never_scores_worse_than_the_deterministic_baseline_on_real_data():
    # The real reported job (results/26Y118_data/) -- even a small, fast budget must find a
    # genuine, strict improvement here (measured directly: 0.2s reliably drops mismatches
    # 13->11 with the default seed), while never regressing sheets or leaving anything
    # unplaced that the baseline placed.
    text = REAL_26Y118_CSV.read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=0, right=10, bottom=10, left=5)

    baseline = nanxing_optimize(parts, stock, margin, spacing=6.1, waste_strategy="edge")
    searched = nanxing_optimize(parts, stock, margin, spacing=6.1, waste_strategy="edge", search_time_budget_s=0.5)

    assert len(searched.unplaced) == len(baseline.unplaced) == 0
    assert len(searched.sheets) <= len(baseline.sheets)
    assert _score(searched) <= _score(baseline)
    assert _mismatch_count(searched.sheets) < _mismatch_count(baseline.sheets)


def test_search_is_deterministic_for_a_fixed_seed():
    text = (CSV_SAMPLE_DIR / "nesting_machine_data.csv").read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=10, right=10, bottom=10, left=10)

    first = nanxing_optimize(parts, stock, margin, spacing=5.0, search_time_budget_s=0.3, search_seed=42)
    second = nanxing_optimize(parts, stock, margin, spacing=5.0, search_time_budget_s=0.3, search_seed=42)
    assert [s.placed for s in first.sheets] == [s.placed for s in second.sheets]
    assert first.unplaced == second.unplaced


def test_group_cap_safety_net_never_leaves_a_sheet_spuriously_empty():
    # Construct a sheet where EVERY remaining part belongs to a duplicate group capped at 0
    # (i.e. none of them may use their fallback pose here) -- without the safety net in
    # place_parts_on_board, this would defer every single part, return an empty sheet, and
    # nanxing.py's caller would wrongly mark all of them permanently unplaced instead of
    # retrying on a fresh board. Board is deliberately small (just barely fits one part in the
    # fallback pose, and not at all in the preferred pose) so every part in this batch is
    # forced into "fallback or defer."
    part_a = _part(400.0, 900.0, id="A")
    part_b = _part(400.0, 900.0, id="B")
    board = StockBoard(material="MAT", length=1000.0, width=500.0, thickness=18.0, grain="none")
    margin = Margin(top=0, right=0, bottom=0, left=0)
    group_caps = {footprint_signature(part_a): 0}

    sheet, unplaced = place_parts_on_board(
        [part_a, part_b], board, margin, gap=0.0, allow_rotation=True, sheet_index=1,
        waste_strategy="balanced", group_caps=group_caps,
        search_rng=random.Random(0), candidate_pool=1,
    )
    assert len(sheet.placed) >= 1, "safety net should force at least one placement, not defer everything"


def test_defer_probability_never_defers_a_non_duplicate_part():
    # Regression test for a real gap found and fixed while implementing this (pass 26):
    # defer_probability's per-encounter check must scope to genuine duplicates the same way
    # group_caps' keys are always pre-scoped -- deferring is only *safe* when some sibling of
    # the identical footprint proves the preferred pose is achievable elsewhere. A single
    # unique-sized part with no sibling at all must never be deferred, even with
    # defer_probability=1.0 and an rng that would always say "defer." A second, unrelated
    # square "filler" part is included specifically so the safety net (which forces a retry
    # only when a sheet ends up *entirely* empty) can't mask the bug by rescuing an
    # otherwise-empty sheet -- a square part never needs its fallback pose at all (both
    # orientations equally satisfy the length-axis preference), so it always places
    # regardless, keeping the sheet non-empty and letting the real assertion -- that the
    # *target* part specifically was never deferred -- actually bite.
    target = _part(400.0, 900.0, id="only-one")  # no sibling anywhere in this call's parts
    filler = _part(50.0, 50.0, id="filler")  # square -> never needs a fallback pose at all
    board = StockBoard(material="MAT", length=1000.0, width=500.0, thickness=18.0, grain="none")
    margin = Margin(top=0, right=0, bottom=0, left=0)

    sheet, unplaced = place_parts_on_board(
        [target, filler], board, margin, gap=0.0, allow_rotation=True, sheet_index=1,
        waste_strategy="balanced", search_rng=random.Random(0), defer_probability=1.0,
    )
    assert unplaced == [], "a non-duplicate part must place via its fallback, never defer"
    assert {p.partId for p in sheet.placed} == {"only-one", "filler"}


def test_defer_probability_safety_net_never_leaves_a_sheet_spuriously_empty():
    # Same adversarial scenario as the group_caps safety-net test above, but driving the
    # *other* defer move (defer_probability=1.0 with an rng that always defers) -- both moves
    # share the same `_caps_disabled` retry-once-with-deferring-off safety net, so this
    # confirms it actually covers both code paths, not just the group_caps one.
    part_a = _part(400.0, 900.0, id="A")
    part_b = _part(400.0, 900.0, id="B")
    board = StockBoard(material="MAT", length=1000.0, width=500.0, thickness=18.0, grain="none")
    margin = Margin(top=0, right=0, bottom=0, left=0)

    sheet, unplaced = place_parts_on_board(
        [part_a, part_b], board, margin, gap=0.0, allow_rotation=True, sheet_index=1,
        waste_strategy="balanced", search_rng=random.Random(0), defer_probability=1.0,
    )
    assert len(sheet.placed) >= 1, "safety net should force at least one placement, not defer everything"


def test_search_result_has_no_overlaps_or_out_of_margin_placements():
    # Reuse this project's own standing geometry-invariant checks (see helpers.py) against a
    # searched (not just baseline) result, since the search chooses among genuinely different
    # candidate placements -- worth confirming those invariants hold for whichever one wins,
    # not just the deterministic path already covered elsewhere.
    from .helpers import overlapping_pairs

    text = (CSV_SAMPLE_DIR / "nesting_machine_data.csv").read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=10, right=10, bottom=10, left=10)
    result = nanxing_optimize(parts, stock, margin, spacing=5.0, search_time_budget_s=0.3)

    for sheet in result.sheets:
        assert overlapping_pairs(sheet.placed) == 0
        usable_w = sheet.boardW - margin.left - margin.right
        usable_h = sheet.boardL - margin.top - margin.bottom
        for p in sheet.placed:
            assert -1e-6 <= p.x and p.x + p.w <= usable_w + 1e-6
            assert -1e-6 <= p.y and p.y + p.h <= usable_h + 1e-6
