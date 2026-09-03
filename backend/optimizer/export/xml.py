from __future__ import annotations
from itertools import count
from xml.sax.saxutils import escape as xml_escape

from ..model import Margin, OptResult, Offcut, Part, PlacedPart, Sheet

# Appendix A.4: a workpiece needs extra holding when its shorter side is <= ~265mm.
SMALL_WORKPIECE_THRESHOLD = 265.0
# Appendix A.2 constants (verbatim from 391 real parts across 3 machine-cut files).
SLOPE_LEN = 70
UNROTATED_FACE_ORDER = (1, 2, 3, 4)
ROTATED_FACE_ORDER = (3, 4, 2, 1)
# Appendix A.2 lists Workpiece.Grain="N" as a constant, but that only holds for
# grain-free parts — verified against a golden file with grain-directional stock (207/648
# workpieces) that grain-locked parts carry "L" (length-grain) instead. No "W" example was
# observed in the samples, but it follows the same length/width-initial convention.
WORKPIECE_GRAIN_CODE = {"none": "N", "length": "L", "width": "W"}
# Appendix A.2's Lineament.ProOffsetX/Y=3 is not just a cosmetic attribute: verified against
# golden data that every Workpiece.Lineament polygon spans (CutLength + 2*offset) x
# (CutWidth + 2*offset), not the raw cut rectangle — Lineament is the router's tool-path
# envelope around the cut line, not the cut line itself. Oddments' Lineament has no such
# offset (confirmed 0/60 samples carry ProOffset attrs) and spans the raw free rectangle.
PRO_OFFSET = 3.0

# Fields deliberately NOT reproduced here, because their derivation isn't specified by
# instructions.md/Appendix A and guessing risks a file the machine's parser mis-trusts:
#   - Workpiece.CutInfos ToolPoint — always emitted as "0" (Appendix A.5's own stated
#     default). No rule for which of the 4 lead-in points is actually best was found in the
#     golden data; a machine dry-run is needed before trusting a non-default choice.
#   - Workpiece.IsIntersectsWith — observed on <2% of real workpieces with no documented
#     trigger condition.
#
# Workpiece.Info1/Info2 WERE on this list (their relationship to Length/Width/CutLength/
# CutWidth didn't hold consistently) until a real physical workpiece label (photographed and
# reported directly, 2026-09-02) showed a blank "F.S." (Final Size) field — re-investigated by
# cross-referencing Info1/Info2 directly
# against a real CSV's own Lenght/Width columns instead of against other XML fields, which
# turned out to be the actual relationship: exact match, 55/55 real barcodes, no rotation
# dependency. See _workpiece_element's own comment for the fix and what's still deliberately
# left alone (Length/Width don't match real data either, but nothing depends on them yet).


def fmt_num(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    text = f"{rounded:.2f}"
    return text.rstrip("0").rstrip(".")


class _El:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, children: list["_El"] | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = children or []


def _render(el: _El, depth: int) -> list[str]:
    pad = "  " * depth
    attr_str = "".join(f' {k}="{xml_escape(str(v))}"' for k, v in el.attrs.items())
    if not el.children:
        return [f"{pad}<{el.tag}{attr_str} />"]
    lines = [f"{pad}<{el.tag}{attr_str}>"]
    for child in el.children:
        lines.extend(_render(child, depth + 1))
    lines.append(f"{pad}</{el.tag}>")
    return lines


def _serialize(root: _El) -> bytes:
    lines = ['<?xml version="1.0"?>'] + _render(root, 0)
    return "\r\n".join(lines).encode("utf-8")


def _rect_corners(minx: float, miny: float, maxx: float, maxy: float, shifted: bool = False) -> list[tuple[float, float]]:
    """The 4 corners of a rectangle, starting bottom-left and winding counter-clockwise —
    or, when `shifted`, starting one corner later (bottom-right). Every golden workpiece
    with CutWidth > CutLength winds its Lineament/Lineament2/FccOutline polygons — and its
    ToolPointList lead-in points — starting one corner later than everything else; verified
    against real data, not documented in Appendix A.
    """
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    if shifted:
        corners = corners[1:] + corners[:1]
    return corners


def _rect_points(minx: float, miny: float, maxx: float, maxy: float, shifted: bool = False) -> list[tuple[float, float]]:
    corners = _rect_corners(minx, miny, maxx, maxy, shifted)
    return corners + [corners[0]]


def _points_element(points: list[tuple[float, float]]) -> _El:
    children = [
        _El("Point", {"Index": str(i + 1), "X": fmt_num(x), "Y": fmt_num(y), "Angle": "0"})
        for i, (x, y) in enumerate(points)
    ]
    return _El("Points", children=children)


def _tool_points(minx: float, miny: float, maxx: float, maxy: float, shifted: bool) -> list[tuple[float, float]]:
    """Appendix A.5's 4 lead-in ramp candidates, each SlopeLen from a corner along an edge.
    When an edge is shorter than SlopeLen itself, golden data clamps both of that edge's
    candidates to its midpoint instead of the raw corner-offset formula — verified against
    several golden strips on both axes: spans >= SlopeLen use the raw (sometimes
    corner-crossing, once the span is under 2*SlopeLen) formula unclamped, spans <
    SlopeLen always clamp. When the same `shifted` flag that reorders the polygon winding
    applies, the idx->corner assignment shifts by the same one position (verified against 3
    golden workpieces spanning both RotateAngle values).
    """
    sl = SLOPE_LEN
    width, height = maxx - minx, maxy - miny
    mid_x, mid_y = (minx + maxx) / 2, (miny + maxy) / 2
    x_from_right = mid_x if width < sl else maxx - sl
    x_from_left = mid_x if width < sl else minx + sl
    y_from_top = mid_y if height < sl else maxy - sl
    y_from_bottom = mid_y if height < sl else miny + sl
    candidates = [
        (x_from_right, miny),
        (maxx, y_from_top),
        (x_from_left, maxy),
        (minx, y_from_bottom),
    ]
    if shifted:
        candidates = candidates[1:] + candidates[:1]
    return candidates


def _cut_infos(small_flag: bool, tool_points: list[tuple[float, float]] | None = None) -> _El:
    attrs = {"SamllWorkpieceFlg": "true" if small_flag else "false"}
    if tool_points is not None:
        # Appendix A.5: default ToolPoint=0, refine after a test cut — no rule found for
        # which idx is actually best; all four are geometrically valid entry points.
        attrs["ToolPoint"] = "0"
        attrs["ToolPointList"] = ";".join(f"{fmt_num(x)},{fmt_num(y)},{i}$" for i, (x, y) in enumerate(tool_points))
    cut_info = _El("CutInfo", {"CutNo": "1", "ToolDirection": "0", "SlopeLen": str(SLOPE_LEN)})
    return _El("CutInfos", attrs, children=[cut_info])


def _edge_group(rotated: bool) -> _El:
    order = ROTATED_FACE_ORDER if rotated else UNROTATED_FACE_ORDER
    edges = [
        _El("Edge", {"Face": str(face), "Thickness": "0", "Pre_Milling": "0", "X": "0", "Y": "0", "CentralAngle": "0"})
        for face in order
    ]
    return _El("EdgeGroup", {"X1": "0", "Y1": "0"}, children=edges)


def _workpiece_element(part: Part, placed: PlacedPart, workpiece_id: int, cutting_order_no: int) -> _El:
    # placed.x/w run along the packer's local x axis (board.width-derived); placed.y/h run
    # along local y (board.length-derived) — see optimizer/saw_packing.py's
    # place_parts_on_board. The machine's own FCC XML convention is the opposite: X follows
    # the board's *length* axis, Y follows *width* — confirmed against real golden data (M10)
    # and against an actual machine load (Issues/issues_002.md: every real job loaded with
    # everything crammed into the board's width-sized region and the rest of the true 2440mm
    # length left empty). Transposed here for export: XML X/its extent come from placed.y/h,
    # XML Y/its extent come from placed.x/w. tests/fcc_golden.py's importer applies the same
    # transpose in reverse so the golden-file round-trip test stays self-consistent.
    minx, miny = placed.y - PRO_OFFSET, placed.x - PRO_OFFSET
    maxx, maxy = placed.y + placed.h + PRO_OFFSET, placed.x + placed.w + PRO_OFFSET
    rotated = placed.rotated
    # drives both the secondary winding shift (Lineament.RotationAngle) and the MachiningPoint=7
    # variant — verified deterministic against real data, not documented as such in Appendix A.
    # Grain-locked parts (Grain="L"/"W") never shift even when CutWidth>CutLength, confirmed
    # against a golden file with 207 grain-directional workpieces (all unrotated, so this
    # doesn't interact with the MachiningPoint=7 case, which only fires when rotated).
    shifted = part.cutWidth > part.cutLength and part.grain == "none"
    board_points = _rect_points(minx, miny, maxx, maxy, shifted)
    small = min(part.cutLength, part.cutWidth) <= SMALL_WORKPIECE_THRESHOLD
    if rotated:
        machining_point = "7" if shifted else "3"
    else:
        machining_point = "1"

    attrs = {
        "ID": str(workpiece_id),
        "WorkpieceId": part.id,
        "CuttingOrderNo": str(cutting_order_no),
        "Qty": "1",
        "Name": part.name,
        "Material": part.material,
        "Length": fmt_num(part.finishedLength),
        "Width": fmt_num(part.finishedWidth),
        "Thickness": fmt_num(part.thickness),
        "CutLength": fmt_num(part.cutLength),
        "CutWidth": fmt_num(part.cutWidth),
        "MachiningPoint": machining_point,
        "Grain": WORKPIECE_GRAIN_CODE.get(part.grain, "N"),
        "ProdutionNo": part.posId,
        "ProductionName": part.name,
        "Customer": part.customer or "",
        "HasFace5": "false",
        "HasFace6": "false",
        "OnlyHasFace6": "false",
        "EBL1": part.edges.get("l1", ""),
        "EBL2": part.edges.get("l2", ""),
        "EBW1": part.edges.get("w1", ""),
        "EBW2": part.edges.get("w2", ""),
        "RotateAngle": "90" if rotated else "0",
        # Real physical workpiece label (photographed and reported directly, 2026-09-02): the
        # machine's own printed label has a "F.S." (Final Size) field for the edge-banding
        # team — verified against real golden data (cross-
        # referencing all 55 shared barcodes between a real CSV and its real machine-cut XML)
        # that Info1/Info2, not Length/Width, hold the CSV's own Lenght/Width columns exactly
        # (no rotation dependency, 55/55 exact matches). Length/Width instead carry something
        # unrelated to F.S. (CutLength/CutWidth + a fixed 6mm, confirmed across all 1039 real
        # workpieces) — deliberately left alone here since cutting is already correct and
        # nothing reported relies on it; only Info1/Info2 were missing and needed for F.S.
        "Info1": fmt_num(part.finishedLength),
        "Info2": fmt_num(part.finishedWidth),
    }
    tool_points = _tool_points(minx, miny, maxx, maxy, shifted)
    lineament = _El(
        "Lineament",
        {
            "RotationAngle": "-90" if shifted else "0",
            "X": fmt_num(minx),
            "Y": fmt_num(miny),
            "ProOffsetX": "3",
            "ProOffsetY": "3",
        },
        children=[_points_element(board_points), _cut_infos(small, tool_points)],
    )
    lineament2 = _El("Lineament2", children=[_points_element(board_points)])
    outline_points = _rect_points(0.0, 0.0, part.cutLength, part.cutWidth, shifted)
    fcc_outline = _El(
        "FccOutline",
        children=[
            _El("FccOutlinePoint", {"X": fmt_num(x), "Y": fmt_num(y), "Angle": "0", "EdgeThickness": "0", "EdgePreMilling": "0"})
            for x, y in outline_points
        ],
    )
    benchmark = _El("BenchmarkInfo", {"ProLength": fmt_num(part.cutLength), "ProWidth": fmt_num(part.cutWidth)})
    return _El("Workpiece", attrs, children=[_edge_group(rotated), lineament, lineament2, fcc_outline, benchmark])


def _oddments_element(offcut: Offcut, index: int, tool_diameter: float) -> _El:
    # Same x<->y / w<->h transpose as _workpiece_element (see its comment) — offcut.x/w are
    # board.width-axis, offcut.y/h are board.length-axis; the XML's X/Length follow the
    # machine's length axis, Y/Width follow its width axis.
    minx, miny = offcut.y, offcut.x
    maxx, maxy = offcut.y + offcut.h, offcut.x + offcut.w
    points = _rect_points(minx, miny, maxx, maxy)
    # the machine reports usable oddment size net of the tool clearance needed to cut
    # it back out, not the raw free-rectangle bounding box (verified against golden data);
    # SamllWorkpieceFlg is keyed off that same *usable* size, not the raw bounds either
    usable_length = offcut.h - tool_diameter
    usable_width = offcut.w - tool_diameter
    small = min(usable_length, usable_width) <= SMALL_WORKPIECE_THRESHOLD
    lineament = _El(
        "Lineament",
        {"RotationAngle": "0", "X": fmt_num(minx), "Y": fmt_num(miny)},
        children=[_points_element(points), _cut_infos(small)],
    )
    return _El(
        "Oddments",
        {"Index": str(index), "Type": "0", "Length": fmt_num(usable_length), "Width": fmt_num(usable_width)},
        children=[lineament],
    )


def _pattern_element(
    sheet: Sheet,
    parts_by_id: dict[str, Part],
    margin: Margin,
    tool_diameter: float,
    part_spacing: float,
    pattern_index: int,
    workpiece_ids: "count[int]",
) -> _El:
    workpieces = [
        _workpiece_element(parts_by_id[placed.partId], placed, next(workpiece_ids), cutting_order_no)
        for cutting_order_no, placed in enumerate(sheet.placed, start=1)
    ]
    oddments = [
        _oddments_element(offcut, index, tool_diameter) for index, offcut in enumerate(sheet.offcuts, start=1)
    ]
    margin_str = ",".join(fmt_num(v) for v in (margin.top, margin.right, margin.bottom, margin.left))
    attrs = {
        "Index": str(pattern_index),
        "LayoutOrigin": "2",
        "WorkpieceSpace": fmt_num(part_spacing),
        "X": "0",
        "Y": "0",
        "ToolName": "80",
        "ToolDiameter": fmt_num(tool_diameter),
        "CutOddmentsFlg": "2",
        "Margin": margin_str,
    }
    children = [_El("Workpieces", children=workpieces), _El("OddmentsList", children=oddments)]
    return _El("Pattern", attrs, children=children)


def generate_fcc_xml(
    result: OptResult,
    parts_by_id: dict[str, Part],
    margin: Margin,
    tool_diameter: float,
    part_spacing: float,
) -> bytes:
    root_attrs = {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        "Version": "2",
        "DataValid": "5",
        "CreateG": "false",
        "RefreshCuttingOrder": "false",
        "OptCreateFile": "true",
    }
    if not result.sheets:
        # Appendix A.8: a valid empty job is a self-closed root, not malformed XML.
        return _serialize(_El("FccRoot", root_attrs))

    materials: list[str] = []
    sheets_by_material: dict[str, list[Sheet]] = {}
    for sheet in result.sheets:
        if sheet.material not in sheets_by_material:
            materials.append(sheet.material)
            sheets_by_material[sheet.material] = []
        sheets_by_material[sheet.material].append(sheet)

    workpiece_ids = count(1)
    patterns_children: list[_El] = []
    for patterns_index, material in enumerate(materials, start=1):
        sheets = sheets_by_material[material]
        pattern_children = [
            _pattern_element(sheet, parts_by_id, margin, tool_diameter, part_spacing, pattern_index, workpiece_ids)
            for pattern_index, sheet in enumerate(sheets, start=1)
        ]
        first_sheet = sheets[0]
        patterns_attrs = {
            "Index": str(patterns_index),
            "ID": str(patterns_index),
            "Name": material,
            "Length": fmt_num(first_sheet.boardL),
            "Width": fmt_num(first_sheet.boardW),
            "Thickness": fmt_num(first_sheet.thickness),
            "Grain": "L",
        }
        patterns_children.append(_El("Patterns", patterns_attrs, children=pattern_children))

    return _serialize(_El("FccRoot", root_attrs, children=patterns_children))
