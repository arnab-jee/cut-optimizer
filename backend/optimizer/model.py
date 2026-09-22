from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional, TypedDict

Grain = Literal["none", "length", "width"]
Orientation = Literal["horizontal", "vertical"]
TargetMachine = Literal["saw", "nanxing"]
# "balanced": current default (shorter-leftover-axis guillotine split) — maximizes fit locally
# per placement, at the cost of leftover space fragmenting into many small pieces scattered
# across the sheet. "edge": always splits along the same fixed axis (vertical), so leftover
# space keeps accumulating into fewer, larger, edge-aligned free rectangles instead of thin
# slivers wedged between parts (Updates/update_003.md, prompted by a real Nanxing layout
# screenshot showing scattered slivers between placed drawer parts). "strips": Panel Saw only
# (optimizer/saw_packing.py) — groups parts by shared width into full-length vertical strips,
# each strip holding only one width, parts simply stacked by length inside it. Trades some
# material efficiency for a layout a human operator can cut with nothing but full-length strip
# cuts plus simple crosscuts — never a cut that starts/stops mid-board. Confirmed against a real
# MaxCut reference PDF the project owner uses for manual panel-saw work (2026-09-22); nanxing
# never dispatches to this (see guillotine_split's own handling of an unrecognized value).
WasteStrategy = Literal["balanced", "edge", "strips"]

class EdgeSet(TypedDict):
    l1: str
    l2: str
    w1: str
    w2: str

@dataclass
class Part:
    id: str
    posId: str
    name: str
    cutLength: float
    cutWidth: float
    finishedLength: float
    finishedWidth: float
    thickness: float
    qty: int
    material: str
    grain: Grain
    edges: EdgeSet
    faceTop: Optional[str] = None
    faceBottom: Optional[str] = None
    core: Optional[str] = None
    customer: Optional[str] = None

    def area(self) -> float:
        return self.cutLength * self.cutWidth

    def can_rotate(self) -> bool:
        return self.grain == "none"

@dataclass
class StockBoard:
    material: str
    length: float
    width: float
    thickness: float
    grain: Grain = "none"

@dataclass
class Margin:
    top: float
    right: float
    bottom: float
    left: float

@dataclass
class OptRequest:
    parts: list[Part]
    stock: list[StockBoard]
    kerf: float
    toolDiameter: float
    partSpacing: float
    margin: Margin
    allowRotation: bool
    target: TargetMachine
    wasteStrategy: WasteStrategy = "balanced"

@dataclass
class PlacedPart:
    partId: str
    x: float
    y: float
    rotated: bool
    w: float
    h: float
    name: str
    material: str
    thickness: float
    grain: Grain

@dataclass
class Offcut:
    x: float
    y: float
    w: float
    h: float

@dataclass
class Sheet:
    index: int
    material: str
    boardL: float
    boardW: float
    thickness: float
    placed: list[PlacedPart]
    offcuts: list[Offcut]
    utilizationPct: float

@dataclass
class CutInstruction:
    orientation: Orientation
    offset: float
    length: float
    sheetIndex: int

@dataclass
class OptResult:
    sheets: list[Sheet]
    unplaced: list[Part]
    cuts: list[CutInstruction] = field(default_factory=list)
