from __future__ import annotations
from dataclasses import replace

from .model import Margin, OptResult, Part, Sheet, StockBoard, WasteStrategy
from .nanxing_packing import consolidate_sheets, place_parts_on_board
from .placement import DEFAULT_PLACEMENT_CORNER, PlacementCorner, mirror_sheet


def optimize(
    request_parts: list[Part], stock: list[StockBoard], margin: Margin, spacing: float,
    waste_strategy: WasteStrategy = "balanced", placement_corner: PlacementCorner = DEFAULT_PLACEMENT_CORNER,
    allow_rotation: bool = True,
) -> OptResult:
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
            group_sheets: list[Sheet] = []
            while remaining:
                # index is a placeholder here -- real, sequential indices (across the whole
                # job, not just this group) are assigned below, after consolidation may have
                # dropped some of this group's sheets.
                sheet, still_remaining = place_parts_on_board(remaining, board, margin, spacing, allow_rotation, 0, waste_strategy)
                if not sheet.placed:
                    unplaced.extend(still_remaining)
                    break
                group_sheets.append(sheet)
                remaining = still_remaining
            # Post-process this group's sheets before mirroring/indexing: place_parts_on_board
            # fills sheets one at a time and never revisits an earlier one, so a part that
            # would fit comfortably into an earlier sheet's leftover space can end up stranded
            # on its own sparse later sheet instead -- consolidate_sheets recovers that real
            # material by relocating parts off the least-full sheets when they fully fit
            # elsewhere, dropping sheets it manages to empty entirely (CLAUDE.md pass 29).
            # Runs in the packer's own native (bottom-left) coordinate system, same as
            # place_parts_on_board itself -- mirroring (a pure post-placement reflection) still
            # happens after, per sheet, exactly as before.
            group_sheets = consolidate_sheets(group_sheets, board, margin, spacing, waste_strategy)
            for sheet in group_sheets:
                sheet = replace(sheet, index=sheet_index)
                sheet = mirror_sheet(sheet, board, margin, placement_corner)
                sheets.append(sheet)
                sheet_index += 1
    return OptResult(sheets=sheets, unplaced=unplaced)
