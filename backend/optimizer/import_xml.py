from __future__ import annotations
from dataclasses import dataclass

from lxml import etree

from .model import Margin, Offcut, OptResult, Part, PlacedPart, Sheet

# Updates/update_006.md: "Ability to load optimization from existing Nanxing nesting xml file."
# This parser was originally written as backend/tests/fcc_golden.py, a test-only helper that
# fed a real machine-cut FccRoot XML back into generate_fcc_xml to test the *serializer* via
# round-trip (spec §7.1). It's promoted here as the real parser behind the import feature, and
# fcc_golden.py now wraps this module instead of duplicating it — the same geometry/attribute
# understanding backs both the test fixture and the real feature, so a future refinement to one
# automatically benefits the other instead of the two silently drifting apart.

GRAIN_CODE_TO_PART_GRAIN = {"N": "none", "L": "length", "W": "width"}


class InvalidFccXmlError(ValueError):
    """Raised when the uploaded content isn't a recognizable Nanxing FCC nesting XML."""


def _recover_raw_cut_dims(golden_cut_length: float, golden_cut_width: float, rotated: bool, grain: str) -> tuple[float, float]:
    """Real production bug (CLAUDE.md pass 27): a golden file's own CutLength/CutWidth are NOT
    fixed, raw-part values -- they're placement-relative (CutLength always means "whichever raw
    dimension is on the board's length axis for this placement", confirmed across all 1039 real
    golden workpieces with zero exceptions). Copying them straight into Part.cutLength/cutWidth
    (the old behavior) baked the placement into what's supposed to be a raw, orientation-
    independent property -- harmless for a pure round-trip (both sides shared the same wrong
    assumption, see optimizer/export/xml.py's own comment on this), but wrong for anything that
    treats the imported Part as a normal one. This inverts optimizer/nanxing_packing.py's own
    `_footprint()` swap decision (a plain transposition, so applying the identical decision a
    second time recovers the original pair) to recover the true raw (cutLength, cutWidth).
    """
    natural_swap = grain != "width"
    swap = natural_swap != rotated
    if swap:
        return golden_cut_length, golden_cut_width
    return golden_cut_width, golden_cut_length


@dataclass
class ImportedJob:
    parts_by_id: dict[str, Part]
    result: OptResult
    margin: Margin
    tool_diameter: float
    part_spacing: float


def _bounds(points_el) -> tuple[float, float, float, float]:
    xs = [float(p.get("X")) for p in points_el.findall("Point")]
    ys = [float(p.get("Y")) for p in points_el.findall("Point")]
    return min(xs), min(ys), max(xs), max(ys)


def parse_fcc_xml(xml_content: str | bytes) -> ImportedJob:
    """Parse a real machine-cut FccRoot XML directly into the same shapes /optimize returns —
    sheets, placed parts, computed utilization — bypassing the optimizer entirely, since the
    layout in the file is already final. Nothing in the file is ever "unplaced": a machine
    export only ever records what it actually nested, not what it rejected."""
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")
    try:
        root = etree.fromstring(xml_content)
    except etree.XMLSyntaxError as exc:
        raise InvalidFccXmlError(f"not a valid XML file: {exc}") from exc

    if root.tag != "FccRoot":
        raise InvalidFccXmlError("not a Nanxing FCC nesting XML (missing <FccRoot> root element)")

    parts: list[Part] = []
    sheets: list[Sheet] = []
    margin: Margin | None = None
    tool_diameter = 6.0
    part_spacing = 6.1
    sheet_index = 1

    try:
        for patterns_el in root.findall("Patterns"):
            material = patterns_el.get("Name")
            board_length = float(patterns_el.get("Length"))
            board_width = float(patterns_el.get("Width"))
            thickness = float(patterns_el.get("Thickness"))

            for pattern_el in patterns_el.findall("Pattern"):
                tool_diameter = float(pattern_el.get("ToolDiameter"))
                part_spacing = float(pattern_el.get("WorkpieceSpace"))
                top, right, bottom, left = (float(v) for v in pattern_el.get("Margin").split(","))
                margin = Margin(top=top, right=right, bottom=bottom, left=left)

                placed_parts: list[PlacedPart] = []
                for wp in pattern_el.findall("Workpieces/Workpiece"):
                    edges = {
                        "l1": wp.get("EBL1", ""),
                        "l2": wp.get("EBL2", ""),
                        "w1": wp.get("EBW1", ""),
                        "w2": wp.get("EBW2", ""),
                    }
                    rotated = wp.get("RotateAngle") == "90"
                    grain = GRAIN_CODE_TO_PART_GRAIN.get(wp.get("Grain"), "none")
                    cut_length, cut_width = _recover_raw_cut_dims(
                        float(wp.get("CutLength")), float(wp.get("CutWidth")), rotated, grain,
                    )
                    part = Part(
                        id=wp.get("WorkpieceId"),
                        posId=wp.get("ProdutionNo"),
                        name=wp.get("Name"),
                        cutLength=cut_length,
                        cutWidth=cut_width,
                        finishedLength=float(wp.get("Length")),
                        finishedWidth=float(wp.get("Width")),
                        thickness=float(wp.get("Thickness")),
                        qty=1,
                        material=wp.get("Material"),
                        grain=grain,
                        edges=edges,
                        customer=wp.get("Customer") or None,
                    )
                    parts.append(part)

                    lineament_el = wp.find("Lineament")
                    minx, miny, maxx, maxy = _bounds(lineament_el.find("Points"))
                    # Lineament spans the tool-path envelope (cut rect inflated by ProOffset on
                    # each side, see optimizer/export/xml.py) — deflate back to the true cut
                    # rect. The XML's X/Y follow the machine's own convention (X=board length
                    # axis, Y=board width axis); PlacedPart.x/y follow the packer's convention
                    # (x=width axis, y=length axis) — the opposite — so this is transposed on the
                    # way in, mirroring the transpose optimizer/export/xml.py applies on the way
                    # out (M11, Issues/issues_002.md).
                    offset_x = float(lineament_el.get("ProOffsetX", 3.0))
                    offset_y = float(lineament_el.get("ProOffsetY", 3.0))
                    placed_parts.append(
                        PlacedPart(
                            partId=part.id,
                            x=miny + offset_y,
                            y=minx + offset_x,
                            rotated=rotated,
                            w=(maxy - miny) - 2 * offset_y,
                            h=(maxx - minx) - 2 * offset_x,
                            name=part.name,
                            material=part.material,
                            thickness=part.thickness,
                            grain=part.grain,
                        )
                    )

                offcuts: list[Offcut] = []
                for odd in pattern_el.findall("OddmentsList/Oddments"):
                    minx, miny, maxx, maxy = _bounds(odd.find("Lineament/Points"))
                    offcuts.append(Offcut(x=miny, y=minx, w=maxy - miny, h=maxx - minx))

                # Mirrors optimizer/saw_packing.py's and nanxing_packing.py's own utilization
                # formula (usable area = board dims minus margin, not the raw board area) so an
                # imported sheet's % reads the same way as one this app placed itself.
                usable_w = board_width - margin.left - margin.right
                usable_h = board_length - margin.top - margin.bottom
                board_area = usable_w * usable_h
                placed_area = sum(p.w * p.h for p in placed_parts)
                utilization = 0.0 if board_area <= 0 else round(placed_area / board_area * 100.0, 2)

                sheets.append(
                    Sheet(
                        index=sheet_index,
                        material=material,
                        boardL=board_length,
                        boardW=board_width,
                        thickness=thickness,
                        placed=placed_parts,
                        offcuts=offcuts,
                        utilizationPct=utilization,
                    )
                )
                sheet_index += 1
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidFccXmlError(f"could not read this as a Nanxing FCC nesting XML: {exc}") from exc

    # A well-formed but empty <FccRoot/> is a legitimate degenerate case (M5, spec Appendix
    # A.8) — zero Patterns means zero sheets, not an error, so margin just defaults rather than
    # raising.
    if margin is None:
        margin = Margin(top=0.0, right=0.0, bottom=0.0, left=0.0)

    parts_by_id = {p.id: p for p in parts}
    result = OptResult(sheets=sheets, unplaced=[])
    return ImportedJob(parts_by_id=parts_by_id, result=result, margin=margin, tool_diameter=tool_diameter, part_spacing=part_spacing)
