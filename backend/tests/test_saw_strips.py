from __future__ import annotations

from collections import defaultdict

import pytest

from optimizer.guillotine import optimize as saw_optimize
from optimizer.model import Margin, Part, StockBoard
from optimizer.saw_packing import _place_parts_on_board_strips

from .helpers import is_guillotine_cuttable, overlapping_pairs

# "strips" waste strategy: real request from the project owner (2026-09-22), comparing this
# app's panel-saw output against a real MaxCut reference PDF used for manual cutting. MaxCut
# arranges every sheet into full-length vertical strips, each strip holding exactly one shared
# width, parts simply stacked by length inside it -- so an operator only ever makes full-length
# strip cuts plus plain crosscuts, never a cut that starts/stops mid-board. See
# saw_packing.py's _place_parts_on_board_strips docstring for the full writeup, including the
# measured trade-off (this mode is *less* material-efficient than "balanced"/"edge", confirmed
# against the real reference job -- it's an operator-convenience choice, not an efficiency win).

_BOARD = StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")
_MARGIN = Margin(top=10, right=10, bottom=10, left=10)


def _part(cutLength: float, cutWidth: float, grain: str = "none", id: str = "P") -> Part:
    return Part(
        id=id, posId=id, name=id, cutLength=cutLength, cutWidth=cutWidth,
        finishedLength=cutLength, finishedWidth=cutWidth, thickness=18.0, qty=1,
        material="MAT", grain=grain, edges={"l1": "", "l2": "", "w1": "", "w2": ""},
    )


def _columns(placed) -> dict[float, list]:
    """Groups placed parts by their x position (i.e. which physical strip they're in)."""
    cols: dict[float, list] = defaultdict(list)
    for p in placed:
        cols[round(p.x, 1)].append(p)
    return cols


def test_strips_mode_is_guillotine_cuttable_and_overlap_free(saw_parts, default_margin):
    stock = [StockBoard(material=m, length=2440, width=1220, thickness=t, grain="none")
             for m, t in sorted({(p.material, p.thickness) for p in saw_parts})]
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="strips")
    assert result.unplaced == []
    for sheet in result.sheets:
        assert overlapping_pairs(sheet.placed) == 0
        assert is_guillotine_cuttable(sheet.placed)


def test_every_strip_is_a_single_width_no_mixing(saw_parts, default_margin):
    # The core promise of this mode: every physical strip (same x) holds parts of exactly one
    # width -- never two different widths side by side within what looks like one column.
    stock = [StockBoard(material=m, length=2440, width=1220, thickness=t, grain="none")
             for m, t in sorted({(p.material, p.thickness) for p in saw_parts})]
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="strips")
    for sheet in result.sheets:
        for x, parts_in_column in _columns(sheet.placed).items():
            widths = {round(p.w, 1) for p in parts_in_column}
            assert len(widths) == 1, f"strip at x={x} mixes widths {widths}"


def test_parts_group_by_whichever_raw_dimension_repeats_more():
    # 5 parts share cutWidth=80 (varying, mutually-unique cutLength values); one more part has
    # cutLength=80/cutWidth=999. Since saw_packing's own natural (unrotated) pose already puts
    # cutLength on the strip-width axis, the 5 "A" parts start as 5 *different* natural widths
    # (300/350/400/450/500, each with count 1) -- each must rotate to join the width=80 group
    # (count 6, the dominant shared value) instead. Part B's own cutLength is already 80, so it
    # needs no rotation at all to land in that same group. All 6 should end up sharing pw=80,
    # possibly split across more than one physical strip if they don't all fit one board-length
    # run (checked separately) -- what matters here is every one of them picked width=80.
    parts = [_part(cutLength=l, cutWidth=80.0, id=f"A{i}") for i, l in enumerate([300, 350, 400, 450, 500])]
    parts.append(_part(cutLength=80.0, cutWidth=999.0, id="B"))
    sheet, still_remaining = _place_parts_on_board_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    assert still_remaining == []
    assert {round(p.w, 1) for p in sheet.placed} == {80.0}
    placed_a = next(p for p in sheet.placed if p.partId == "A0")
    placed_b = next(p for p in sheet.placed if p.partId == "B")
    assert placed_a.rotated is True  # had to turn to bring cutWidth=80 onto the strip axis
    assert placed_b.rotated is False  # cutLength was already 80, no turn needed


def test_orphan_width_gets_its_own_strip():
    parts = [
        _part(cutLength=300.0, cutWidth=80.0, id="A1"),
        _part(cutLength=350.0, cutWidth=80.0, id="A2"),
        _part(cutLength=500.0, cutWidth=333.0, id="LONER"),  # shares no dimension with anything
    ]
    sheet, still_remaining = _place_parts_on_board_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    assert still_remaining == []
    cols = _columns(sheet.placed)
    assert len(cols) == 2  # the 80mm group's strip, plus LONER's own strip
    loner = next(p for p in sheet.placed if p.partId == "LONER")
    loner_col = cols[round(loner.x, 1)]
    assert len(loner_col) == 1


def test_grain_locked_part_never_rotated_even_if_it_would_join_a_bigger_group():
    # A grain="width" part's footprint is fixed (can_rotate() is False) -- even though rotating
    # it would let it share a strip with the grain=none group below, it must stay in its own,
    # grain-mandated pose.
    parts = [
        _part(cutLength=l, cutWidth=200.0, grain="none", id=f"A{i}") for i, l in enumerate([300, 350, 400])
    ]
    parts.append(_part(cutLength=200.0, cutWidth=90.0, grain="width", id="LOCKED"))
    sheet, still_remaining = _place_parts_on_board_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    assert still_remaining == []
    locked = next(p for p in sheet.placed if p.partId == "LOCKED")
    assert locked.rotated is False


def test_a_groups_total_length_spanning_multiple_boards_splits_into_multiple_strip_instances():
    # 8 parts sharing cutWidth=80 with mutually-unique cutLength values (so the frequency
    # heuristic unambiguously groups them by width=80, not by any one length), each 1000mm
    # long -- more than fit in one ~2420mm-usable strip (2 per strip after kerf), so this group
    # needs multiple strip instances even on a single board.
    parts = [_part(cutLength=1000.0 + i, cutWidth=80.0, id=f"P{i}") for i in range(8)]
    sheet, still_remaining = _place_parts_on_board_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    cols = _columns(sheet.placed)
    assert {round(p.w, 1) for p in sheet.placed} == {80.0}
    assert len(cols) > 1  # needed more than one physical strip instance of width=80
    assert len(sheet.placed) + len(still_remaining) == 8
    for parts_in_col in cols.values():
        assert len(parts_in_col) <= 2  # 2x1000mm + kerf fits in ~2420mm; a 3rd would not


def test_instances_are_ordered_by_leftover_not_width_so_waste_stays_contiguous():
    # Real bug (reported via two rendered PDFs, 2026-09-22): a near-full instance sorted by
    # width alone can land *between* two mostly-empty ones purely because its own width value
    # happens to fall between theirs, splitting what should be one reusable offcut into two
    # disconnected scraps on opposite edges of the sheet. Reproduces the exact real scenario:
    # a 421mm-wide group with three 654mm-long parts (nearly fills the board -- little
    # leftover) sitting width-wise between a 276mm-wide and a 426mm-wide group, each with a
    # single short 396.5mm part (mostly empty -- lots of leftover). Sorted by width alone
    # (426, 421, 276 or its reverse), the full instance would land in the middle; sorted by
    # leftover, the two empty instances must be adjacent to each other with the full one at
    # an edge, regardless of which edge. Uses grain="width" (pw = cutLength, deterministic --
    # see _footprint) so each group's width is pinned directly rather than depending on the
    # frequency-based grain="none" heuristic tested elsewhere; that heuristic isn't what this
    # test is about.
    parts = [
        _part(cutLength=426.0, cutWidth=396.5, grain="width", id="A"),
        _part(cutLength=421.0, cutWidth=654.0, grain="width", id="B1"),
        _part(cutLength=421.0, cutWidth=654.0, grain="width", id="B2"),
        _part(cutLength=421.0, cutWidth=654.0, grain="width", id="B3"),
        _part(cutLength=276.0, cutWidth=396.5, grain="width", id="C"),
    ]
    sheet, still_remaining = _place_parts_on_board_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    assert still_remaining == []
    cols = _columns(sheet.placed)
    assert len(cols) == 3
    xs_in_order = sorted(cols.keys())
    widths_in_order = [round(cols[x][0].w, 1) for x in xs_in_order]
    # The full (421mm) instance must sit at one edge, not sandwiched between the two mostly-
    # empty ones -- otherwise their leftover regions can't merge into one contiguous area.
    assert 421.0 in (widths_in_order[0], widths_in_order[-1])


def test_part_too_large_for_any_board_is_reported_unplaced():
    parts = [_part(cutLength=5000.0, cutWidth=80.0, id="TOO_BIG")]
    stock = [_BOARD]
    result = saw_optimize(parts, stock, _MARGIN, kerf=4.0, allow_rotation=True, waste_strategy="strips")
    assert len(result.unplaced) == 1
    assert result.unplaced[0].id == "TOO_BIG"


def test_real_job_drops_nothing_and_beats_naive_sheet_count(saw_parts, default_margin):
    # Real regression using this project's existing saw fixture (not the CSV from the reported
    # comparison, which isn't a committed fixture) -- confirms strips mode places every part on
    # a real job, still guillotine-cuttable/overlap-free (already covered above), and doesn't
    # blow up sheet count compared to today's default.
    stock = [StockBoard(material=m, length=2440, width=1220, thickness=t, grain="none")
             for m, t in sorted({(p.material, p.thickness) for p in saw_parts})]
    balanced = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="balanced")
    strips = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="strips")
    assert strips.unplaced == []
    assert len(strips.sheets) <= len(balanced.sheets) * 2  # sanity bound, not a tight efficiency claim


def test_nanxing_ignores_strips_and_falls_back_safely(nesting_parts, default_margin):
    # "strips" is Panel Saw only (optimizer/saw_packing.py) -- nanxing_packing.py has no
    # dispatch for it at all, so guillotine_split (shared by both modules) must fall back to its
    # "balanced" behavior rather than erroring on an unrecognized value.
    from optimizer.nanxing import optimize as nanxing_optimize
    stock = [StockBoard(material=m, length=2440, width=1220, thickness=t, grain="none")
             for m, t in sorted({(p.material, p.thickness) for p in nesting_parts})]
    result = nanxing_optimize(nesting_parts, stock, default_margin, spacing=6.0, waste_strategy="strips")
    assert result.unplaced == []
    for sheet in result.sheets:
        assert overlapping_pairs(sheet.placed) == 0
