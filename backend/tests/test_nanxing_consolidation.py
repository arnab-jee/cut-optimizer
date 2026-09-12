from __future__ import annotations

from optimizer.model import Margin, Offcut, PlacedPart, Sheet, StockBoard
from optimizer.nanxing_packing import consolidate_sheets

# CLAUDE.md pass 29: place_parts_on_board fills sheets greedily, one at a time, and never
# revisits an earlier one -- a real annotated machine screenshot showed exactly the resulting
# waste (a 37.9%-full sheet sitting next to others with real unused width). consolidate_sheets
# is a post-processing pass: relocate a sparse sheet's parts into other sheets' real leftover
# free space, dropping the sheet entirely when every one of its parts finds a new home.


def _placed(part_id: str, x: float, y: float, w: float, h: float) -> PlacedPart:
    return PlacedPart(partId=part_id, x=x, y=y, rotated=False, w=w, h=h, name=part_id, material="MAT", thickness=18.0, grain="none")


def _board() -> StockBoard:
    return StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")


_NO_MARGIN = Margin(top=0, right=0, bottom=0, left=0)


def test_dissolves_a_sparse_sheet_that_fully_fits_elsewhere():
    sheet1 = Sheet(
        index=1, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("BIG", 0, 0, 1000, 2000)],
        offcuts=[Offcut(x=1000, y=0, w=310, h=2440)],  # comfortably fits a 300x300 part
        utilizationPct=67.0,
    )
    sheet2 = Sheet(
        index=2, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("SMALL", 0, 0, 300, 300)],
        offcuts=[Offcut(x=300, y=0, w=920, h=2440), Offcut(x=0, y=300, w=1220, h=2140)],
        utilizationPct=3.0,
    )
    result = consolidate_sheets([sheet1, sheet2], _board(), _NO_MARGIN, gap=0.0, waste_strategy="balanced")
    assert len(result) == 1
    assert {p.partId for p in result[0].placed} == {"BIG", "SMALL"}


def test_all_or_nothing_when_not_every_part_on_the_sparse_sheet_fits_elsewhere():
    # Sheet 2's SMALL (300x300) would fit sheet 1's leftover space, but HUGE (900x900)
    # wouldn't -- the whole migration must be rejected, leaving BOTH sheets exactly as they
    # were (SMALL must NOT get relocated on its own, orphaning HUGE on a still-existing
    # sheet 2 -- that would silently change the layout for no actual sheet-count benefit).
    sheet1 = Sheet(
        index=1, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("BIG", 0, 0, 1000, 2000)],
        offcuts=[Offcut(x=1000, y=0, w=310, h=2440)],  # fits SMALL, NOT HUGE (width 310 < 900)
        utilizationPct=67.0,
    )
    sheet2 = Sheet(
        index=2, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("SMALL", 0, 0, 300, 300), _placed("HUGE", 300, 0, 900, 900)],
        offcuts=[Offcut(x=0, y=900, w=1220, h=1540)],
        utilizationPct=31.0,
    )
    result = consolidate_sheets([sheet1, sheet2], _board(), _NO_MARGIN, gap=0.0, waste_strategy="balanced")
    assert len(result) == 2
    by_index = {s.index: {p.partId for p in s.placed} for s in result}
    assert by_index == {1: {"BIG"}, 2: {"SMALL", "HUGE"}}


def test_preserves_original_relative_order_of_surviving_sheets():
    # Regression test for a real bug found while implementing this: the internal "try the
    # least-full sheet first" ranking must never leak into the final sheet order -- callers
    # (nanxing.py) assign real, sequential sheet numbers based on list position, so a
    # reordered result would silently relabel which physical sheet is "1" vs "2".
    sheet1 = Sheet(
        index=1, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("A", 0, 0, 1000, 2000)],
        offcuts=[Offcut(x=1000, y=0, w=220, h=2440)],  # too narrow for anything below
        utilizationPct=67.0,
    )
    sheet2 = Sheet(
        index=2, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("B", 0, 0, 300, 300)],
        offcuts=[Offcut(x=300, y=0, w=920, h=2440), Offcut(x=0, y=300, w=1220, h=2140)],
        utilizationPct=3.0,
    )
    sheet3 = Sheet(
        index=3, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("C", 0, 0, 900, 900)],
        offcuts=[Offcut(x=900, y=0, w=320, h=2440), Offcut(x=0, y=900, w=1220, h=1540)],
        utilizationPct=27.0,
    )
    # sheet2 is the least full and should dissolve into sheet3's leftover space (900x1540
    # comfortably fits 300x300); sheet1 and sheet3 must come back in their original order.
    result = consolidate_sheets([sheet1, sheet2, sheet3], _board(), _NO_MARGIN, gap=0.0, waste_strategy="balanced")
    assert len(result) == 2
    assert [s.index for s in result] == [1, 3]
    assert {p.partId for p in result[0].placed} == {"A"}
    assert {p.partId for p in result[1].placed} == {"C", "B"}


def test_never_changes_orientation_or_footprint_of_a_migrated_part():
    # Migration relocates a part's x/y only -- it must never re-decide rotated/w/h, since the
    # part was already placed via place_parts_on_board's own hard-constraint-correct
    # orientation logic on its original sheet.
    sheet1 = Sheet(
        index=1, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[_placed("BIG", 0, 0, 1000, 2000)],
        offcuts=[Offcut(x=1000, y=0, w=310, h=2440)],
        utilizationPct=67.0,
    )
    rotated_small = PlacedPart(partId="ROT", x=0, y=0, rotated=True, w=300, h=250, name="ROT", material="MAT", thickness=18.0, grain="none")
    sheet2 = Sheet(
        index=2, material="MAT", boardL=2440, boardW=1220, thickness=18.0,
        placed=[rotated_small],
        offcuts=[Offcut(x=300, y=0, w=920, h=2440)],
        utilizationPct=3.0,
    )
    result = consolidate_sheets([sheet1, sheet2], _board(), _NO_MARGIN, gap=0.0, waste_strategy="balanced")
    assert len(result) == 1
    migrated = next(p for p in result[0].placed if p.partId == "ROT")
    assert migrated.rotated is True
    assert (migrated.w, migrated.h) == (300, 250)
