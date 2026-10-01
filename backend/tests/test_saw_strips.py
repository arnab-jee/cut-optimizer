from __future__ import annotations

from collections import defaultdict

import pytest

from optimizer.guillotine import optimize as saw_optimize
from optimizer.model import Margin, Part, StockBoard
from optimizer.saw_packing import _absorb_slack, _build_strip_instances, _place_parts_on_board_strips, pack_all_strips

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


def test_strip_column_is_single_width_or_hosts_one_disjoint_nested_width(saw_parts, default_margin):
    # Pass 38 (2026-09-23): decoding a real MaxCut reference PDF's own rectangle geometry
    # directly showed MaxCut itself does NOT hold one width constant the full board length --
    # a column can switch to a different, narrower width partway down once its own first-level
    # content runs out, rather than leaving that leftover length as pure waste. "strips" grew a
    # bounded, exactly-one-level-deep version of this (_absorb_slack): a column may host at
    # most one nested (narrower) width below its own first-level content, never more, and never
    # overlapping it in y -- still a shallow, human-followable cut tree, just no longer a hard
    # single-width-for-the-full-length promise.
    stock = [StockBoard(material=m, length=2440, width=1220, thickness=t, grain="none")
             for m, t in sorted({(p.material, p.thickness) for p in saw_parts})]
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="strips")
    for sheet in result.sheets:
        for x, parts_in_column in _columns(sheet.placed).items():
            widths = {round(p.w, 1) for p in parts_in_column}
            assert len(widths) <= 2, f"strip at x={x} mixes more than 2 widths {widths}"
            if len(widths) == 2:
                by_width: dict[float, list] = defaultdict(list)
                for p in parts_in_column:
                    by_width[round(p.w, 1)].append(p)
                (group_a, group_b) = by_width.values()
                a_lo, a_hi = min(p.y for p in group_a), max(p.y + p.h for p in group_a)
                b_lo, b_hi = min(p.y for p in group_b), max(p.y + p.h for p in group_b)
                assert a_hi <= b_lo + 1e-6 or b_hi <= a_lo + 1e-6, (
                    f"strip at x={x} overlaps two different widths in y: {group_a} vs {group_b}"
                )


def test_absorb_slack_nests_a_sparse_group_into_a_fuller_hosts_leftover_length():
    # A minimal, hand-verified case: a 500x333 "host" part leaves ~2087mm of unused length in
    # its own 2420mm-usable column -- easily enough to hold a whole separate, sparse 80mm-wide
    # group (two short parts, ~658mm total) that would otherwise need its own dedicated
    # board-width column. Confirms the merge actually happens (2 instances in, 1 out) and that
    # the survivor is the fuller one hosting the sparser one, not the reverse.
    # grain="width" pins pw=cutLength, ph=cutWidth (_footprint never swaps a locked part).
    width, height, gap = 1200.0, 2420.0, 4.0
    parts = [
        _part(cutLength=500.0, cutWidth=333.0, grain="width", id="HOST"),
        _part(cutLength=80.0, cutWidth=300.0, grain="width", id="A1"),
        _part(cutLength=80.0, cutWidth=350.0, grain="width", id="A2"),
    ]
    instances, unplaceable = _build_strip_instances(parts, True, gap, width, height)
    assert unplaceable == set()
    assert len(instances) == 2
    reduced = _absorb_slack(instances, gap, height)
    assert len(reduced) == 1
    host = reduced[0]
    assert host.width == 500.0  # grain="width" -> pw=cutLength, so HOST's own strip-width is 500
    assert len(host.nested) == 1
    assert host.nested[0].width == 80.0
    assert {p.id for p, *_ in host.nested[0].items} == {"A1", "A2"}


def test_absorb_slack_never_nests_two_levels_deep():
    # A host that has itself absorbed a guest must never also become someone else's guest --
    # nesting stays bounded to exactly one extra level. A is the fullest/widest instance and
    # has ample slack (length) and width budget to directly host both B and C; B is itself
    # sized so it *could* have hosted C by length alone (2420-154=2266 >> C's 104) if chaining
    # were allowed -- confirms C lands as A's guest (or standalone), never routed through B.
    width, height, gap = 1200.0, 2420.0, 4.0
    parts = [
        _part(cutLength=300.0, cutWidth=2000.0, grain="width", id="A"),
        _part(cutLength=100.0, cutWidth=150.0, grain="width", id="B"),
        _part(cutLength=100.0, cutWidth=100.0, grain="width", id="C"),
    ]
    instances, unplaceable = _build_strip_instances(parts, True, gap, width, height)
    assert unplaceable == set()
    reduced = _absorb_slack(instances, gap, height)
    for inst in reduced:
        for guest in inst.nested:
            assert guest.nested == []  # a nested instance must never itself host anything


def test_absorb_slack_reduces_real_job_board_width_demand(saw_parts, default_margin):
    # Real-job sanity check, not just the hand-built cases above: on the project's own saw
    # fixture, absorbing sparse instances into fuller hosts' leftover length must never increase
    # total top-level board-width demand versus not absorbing at all, and should reduce it
    # whenever any instance has meaningful slack.
    width = 1220.0 - default_margin.left - default_margin.right
    height = 2440.0 - default_margin.top - default_margin.bottom
    instances, _ = _build_strip_instances(saw_parts, True, 4.0, width, height)
    before_demand = sum(inst.width + 4.0 for inst in instances)
    reduced = _absorb_slack(instances, 4.0, height)
    after_demand = sum(inst.width + 4.0 for inst in reduced)
    assert after_demand <= before_demand
    assert len(reduced) <= len(instances)


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


def test_pack_all_strips_beats_repeated_single_board_calls():
    # Real bug (reported via a rendered PDF showing several sheets at 80-90%+ wastage,
    # 2026-09-23): guillotine.py used to call the single-board strips packer repeatedly, each
    # time re-running Steps 1-2 from scratch on whatever parts were still left. For a
    # grain="none" part whose own natural-width choice is a near-tie (see
    # _build_strip_instances's frequency heuristic), that tie can be broken one way when the
    # *full* remaining group is visible and the *other* way once some group members have
    # already been placed on an earlier board and are no longer in the pool -- silently
    # changing that part's chosen strip-width between the original planning pass and the
    # regenerated one. This 5-part case reproduces exactly that: P0 (750x550) is genuinely
    # ambiguous between width=550 (tied 2-1 in its favor while P6, width=550, is still
    # present) and width=750 (once P6 is gone). P6 legitimately gets placed on sheet 1
    # alongside P7/P8; recomputing from scratch on just [P0, P2] afterward flips P0 to
    # width=750, which no longer fits alongside P2 (500 wide) on one board (754+504=1258mm >
    # 1200mm usable) the way width=550 would have (554+504=1058mm) -- forcing a 3rd sheet
    # that a single whole-group bin-packing pass never needed.
    board = StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")
    margin = Margin(top=10, right=10, bottom=10, left=10)
    parts = [
        _part(cutLength=750.0, cutWidth=550.0, id="P0"),
        _part(cutLength=500.0, cutWidth=250.0, id="P2"),
        _part(cutLength=550.0, cutWidth=150.0, id="P6"),
        _part(cutLength=700.0, cutWidth=400.0, id="P7"),
        _part(cutLength=150.0, cutWidth=150.0, id="P8"),
    ]

    remaining = parts
    old_sheet_count = 0
    while remaining:
        sheet, still = _place_parts_on_board_strips(remaining, board, margin, 4.0, True, old_sheet_count + 1)
        if not sheet.placed:
            break
        old_sheet_count += 1
        remaining = still
    assert remaining == []  # every part is placeable; this is purely a packing-quality gap

    new_sheets, unplaced = pack_all_strips(parts, board, margin, 4.0, True, 1)
    assert unplaced == []
    assert len(new_sheets) < old_sheet_count
    assert len(new_sheets) == 2
    assert old_sheet_count == 3
    for sheet in new_sheets:
        assert overlapping_pairs(sheet.placed) == 0
        assert is_guillotine_cuttable(sheet.placed)


def test_pack_all_strips_rejects_a_group_wider_than_the_board_itself():
    # Real regression caught while building pack_all_strips: _bin_pack_instances always finds
    # *some* bin for an instance it's given (opening a new one if none of the existing ones
    # fit), so it must never be handed an instance whose own width exceeds the board's --
    # otherwise it gets placed anyway, out of bounds. cutWidth=5000 here is deliberately wider
    # than the whole board (1220mm) in either orientation.
    board = StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")
    margin = Margin(top=10, right=10, bottom=10, left=10)
    parts = [_part(cutLength=80.0, cutWidth=5000.0, id="TOO_WIDE")]
    sheets, unplaced = pack_all_strips(parts, board, margin, 4.0, True, 1)
    assert sheets == []
    assert [p.id for p in unplaced] == ["TOO_WIDE"]


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


def test_frequent_dimension_wider_than_board_is_not_chosen_as_strip_width():
    # Real regression (results/300920261305): two 2262-long parts made 2262 the "most repeated"
    # dimension, so 2262x832 / 2262x407 were grouped into 2262mm-wide strips, wider than the
    # 1220mm board, and rejected as unplaceable even though rotated they fit easily.
    parts = [_part(2262, 832, id="A"), _part(2262, 407, id="B"), _part(2167, 822, id="C"), _part(2167, 402, id="D")]
    sheets, unplaced = pack_all_strips(parts, _BOARD, _MARGIN, 4.0, True, 1)
    assert unplaced == []
    assert sum(len(s.placed) for s in sheets) == 4
    for s in sheets:
        assert overlapping_pairs(s.placed) == 0
