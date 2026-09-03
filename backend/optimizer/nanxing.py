from __future__ import annotations

from .model import Margin, OptResult, Part, Sheet, StockBoard, WasteStrategy
from .nanxing_packing import place_parts_on_board
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
            while remaining:
                sheet, still_remaining = place_parts_on_board(remaining, board, margin, spacing, allow_rotation, sheet_index, waste_strategy)
                if not sheet.placed:
                    unplaced.extend(still_remaining)
                    break
                sheet = mirror_sheet(sheet, board, margin, placement_corner)
                sheets.append(sheet)
                sheet_index += 1
                remaining = still_remaining
    return OptResult(sheets=sheets, unplaced=unplaced)
