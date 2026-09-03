from __future__ import annotations

from lxml import etree

from optimizer.export.xml import generate_fcc_xml
from optimizer.model import Margin, Offcut, OptResult, Part, PlacedPart, Sheet
from optimizer.nanxing import optimize as nanxing_optimize
from optimizer.parser import parse_csv_text

from .conftest import CSV_SAMPLE_DIR, XML_GOLDEN_DIR
from .helpers import default_stock_for

# Issues/issues_002.md: a real Nanxing machine load showed every job crammed into a region no
# bigger than the board's *width*, with the rest of the board's real *length* left empty. Root
# cause: optimizer/export/xml.py wrote PlacedPart.x (the packer's board.width-axis coordinate,
# max ~1205mm on a 2440x1220 board) straight into the XML's X attribute, which the machine
# reads as the board's *length* axis (expects up to ~2430mm) — and PlacedPart.y (board.length-
# axis, max ~2430mm) into Y, which the machine reads as *width* (expects up to ~1205mm). The
# golden-file round-trip test never caught this because its importer (fcc_golden.py) made the
# exact same (backwards) assumption on the way in, so import+export cancelled out — round-trip
# fidelity was preserved even though the axis labeling was wrong for real packer-sourced data.
# These tests exercise the real packer -> exporter path directly, which the round-trip test
# never did.


def _part(id: str, cutLength: float, cutWidth: float) -> Part:
    return Part(
        id=id, posId=id, name=id, cutLength=cutLength, cutWidth=cutWidth,
        finishedLength=cutLength, finishedWidth=cutWidth, thickness=18.0, qty=1,
        material="MAT", grain="none", edges={"l1": "", "l2": "", "w1": "", "w2": ""},
    )


def test_workpiece_x_follows_board_length_axis_not_width():
    # Synthetic, deliberately discriminating case: a part placed at local y=2000 (the packer's
    # length-derived axis) on a 2440x1220 board. y=2000 exceeds the board's width (1220), so if
    # the exporter still wrote placed.y straight into XML Y (the bug), this value could never
    # appear as a valid Y coordinate on a 1220mm-wide board — it must appear as X instead.
    part = _part("P1", cutLength=200.0, cutWidth=100.0)
    placed = PlacedPart(
        partId="P1", x=50.0, y=2000.0, rotated=False, w=200.0, h=100.0,
        name="P1", material="MAT", thickness=18.0, grain="none",
    )
    sheet = Sheet(
        index=1, material="MAT", boardL=2440.0, boardW=1220.0, thickness=18.0,
        placed=[placed], offcuts=[], utilizationPct=50.0,
    )
    result = OptResult(sheets=[sheet], unplaced=[])
    margin = Margin(top=0, right=0, bottom=0, left=0)
    xml_bytes = generate_fcc_xml(result, {"P1": part}, margin, tool_diameter=6.0, part_spacing=6.0)
    root = etree.fromstring(xml_bytes)
    lineament = root.find(".//Workpiece/Lineament")
    assert float(lineament.get("X")) == 2000.0 - 3.0  # placed.y - PRO_OFFSET
    assert float(lineament.get("Y")) == 50.0 - 3.0  # placed.x - PRO_OFFSET


def test_oddment_x_follows_board_length_axis_not_width():
    offcut = Offcut(x=50.0, y=1800.0, w=100.0, h=200.0)
    sheet = Sheet(
        index=1, material="MAT", boardL=2440.0, boardW=1220.0, thickness=18.0,
        placed=[], offcuts=[offcut], utilizationPct=0.0,
    )
    result = OptResult(sheets=[sheet], unplaced=[])
    margin = Margin(top=0, right=0, bottom=0, left=0)
    xml_bytes = generate_fcc_xml(result, {}, margin, tool_diameter=6.0, part_spacing=6.0)
    root = etree.fromstring(xml_bytes)
    lineament = root.find(".//Oddments/Lineament")
    assert float(lineament.get("X")) == 1800.0
    assert float(lineament.get("Y")) == 50.0


def test_real_job_workpiece_coordinates_stay_within_declared_board_bounds():
    # Integration-level sanity check against a real CSV: every workpiece's XML Points must fall
    # within [0, Patterns.Length] x [0, Patterns.Width] (plus a small tolerance for the
    # tool-path envelope offset), and — the actually discriminating assertion — at least one
    # workpiece's X extent on the busiest sheet must exceed the board's Width, proving the
    # board's real Length axis is genuinely being used, not just coincidentally never exercised.
    text = (CSV_SAMPLE_DIR / "nesting_machine_data.csv").read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=10, right=10, bottom=10, left=5)
    result = nanxing_optimize(parts, stock, margin, spacing=6.0)
    parts_by_id = {p.id: p for p in parts}
    xml_bytes = generate_fcc_xml(result, parts_by_id, margin, tool_diameter=6.0, part_spacing=6.0)
    root = etree.fromstring(xml_bytes)

    saw_x_exceed_width = False
    for patterns_el in root.findall("Patterns"):
        board_length = float(patterns_el.get("Length"))
        board_width = float(patterns_el.get("Width"))
        for wp in patterns_el.findall(".//Workpiece"):
            for point in wp.findall("Lineament/Points/Point"):
                x, y = float(point.get("X")), float(point.get("Y"))
                assert -5 <= x <= board_length + 5, f"{wp.get('WorkpieceId')} X={x} outside [0,{board_length}]"
                assert -5 <= y <= board_width + 5, f"{wp.get('WorkpieceId')} Y={y} outside [0,{board_width}]"
                if x > board_width:
                    saw_x_exceed_width = True
    assert saw_x_exceed_width, "expected at least one workpiece to use X beyond the board's width — otherwise this test can't tell a correct axis mapping from a swapped one"


# Real physical workpiece label (photographed and reported directly, 2026-09-02): the machine's
# printed "F.S." (Final Size) field — used by the edge-banding team downstream of cutting, not
# by the machine itself — came out blank. Root cause: the machine reads F.S. from the
# Workpiece.Info1/Info2 attributes, which optimizer/export/xml.py never emitted at all (an
# earlier pass had looked for a relationship between Info1/Info2 and Length/Width/CutLength/
# CutWidth, found none, and left them out — the real relationship turned out to be with a field
# outside the XML entirely: the source CSV's own Lenght/Width columns). Verified by
# cross-referencing all 55 barcodes shared between nesting_machine_data.csv and its real
# machine-cut golden XML: golden Info1/Info2 match the CSV's Lenght/Width columns exactly, no
# rotation dependency. CutLength/CutWidth/Length/Width are deliberately untouched by this fix —
# cutting was already correct and nothing reported depends on them.
def test_real_job_info1_info2_match_golden_finished_size():
    text = (CSV_SAMPLE_DIR / "nesting_machine_data.csv").read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=10, right=10, bottom=10, left=10)
    result = nanxing_optimize(parts, stock, margin, spacing=5.0)
    parts_by_id = {p.id: p for p in parts}
    xml_bytes = generate_fcc_xml(result, parts_by_id, margin, tool_diameter=6.0, part_spacing=5.0)
    regen_root = etree.fromstring(xml_bytes)
    regen_by_id = {wp.get("WorkpieceId"): wp for wp in regen_root.findall(".//Workpiece")}

    golden_file = XML_GOLDEN_DIR / "26Y111T1F1 (1 FLOOR BEDROOM)-FccForNesting-FccPattern.xml"
    golden_root = etree.parse(str(golden_file)).getroot()

    matched = 0
    for golden_wp in golden_root.findall(".//Workpiece"):
        wid = golden_wp.get("WorkpieceId")
        regen_wp = regen_by_id.get(wid)
        if regen_wp is None:
            continue
        matched += 1
        assert regen_wp.get("Info1") is not None, f"{wid}: Info1 missing from export"
        assert regen_wp.get("Info2") is not None, f"{wid}: Info2 missing from export"
        assert abs(float(golden_wp.get("Info1")) - float(regen_wp.get("Info1"))) <= 0.01, f"{wid}.Info1"
        assert abs(float(golden_wp.get("Info2")) - float(regen_wp.get("Info2"))) <= 0.01, f"{wid}.Info2"
    assert matched == 55, f"expected all 55 shared barcodes to be found, got {matched}"


# Real production bug (CLAUDE.md pass 27, 2026-09-03), present since M5/M6: a real physical
# workpiece label printed a dimension number next to the wrong-looking edge whenever a part got
# rotated. Root cause: Fin China's own CutLength/CutWidth attributes are NOT fixed, raw-CSV
# values -- they're redefined per placement to mean "whichever raw dimension ended up on the
# board's length axis / width axis for THIS placement" (confirmed directly against all 1039 real
# golden workpieces: Xspan-6 == CutLength and Yspan-6 == CutWidth, zero exceptions, even where
# that disagrees with the raw CSV's own column order — 39/55, even 71%, of real parts in the
# established golden set). optimizer/export/xml.py had always written the raw, unrotated
# part.cutLength/part.cutWidth regardless of `placed.rotated`. The round-trip test never caught
# this because import_xml.py copied a golden file's own (already placement-relative) CutLength/
# CutWidth straight into Part.cutLength/cutWidth, so re-exporting an imported part shared the
# same wrong assumption on both sides — the exact same structural blind spot M11 hit.
def test_rotated_part_cutlength_cutwidth_follow_the_placed_axis_not_the_raw_part():
    # Synthetic, deliberately discriminating case: a part whose raw cutLength (900) is the
    # larger dimension, placed *rotated* so cutWidth (200) ends up on the board's length axis
    # instead. If the exporter still wrote the raw part.cutLength/cutWidth unconditionally (the
    # bug), CutLength would say 900 even though 200 is what's actually running along the length
    # axis in this placement.
    part = _part("P1", cutLength=900.0, cutWidth=200.0)
    placed = PlacedPart(
        partId="P1", x=50.0, y=50.0, rotated=True, w=900.0, h=200.0,
        name="P1", material="MAT", thickness=18.0, grain="none",
    )
    sheet = Sheet(
        index=1, material="MAT", boardL=2440.0, boardW=1220.0, thickness=18.0,
        placed=[placed], offcuts=[], utilizationPct=50.0,
    )
    result = OptResult(sheets=[sheet], unplaced=[])
    margin = Margin(top=0, right=0, bottom=0, left=0)
    xml_bytes = generate_fcc_xml(result, {"P1": part}, margin, tool_diameter=6.0, part_spacing=6.0)
    root = etree.fromstring(xml_bytes)
    wp = root.find(".//Workpiece")
    # placed.h (=200) is what actually lands on the XML's length axis (X) for a rotated=True
    # placement here -- see _workpiece_element's own comment for the full axis derivation.
    assert float(wp.get("CutLength")) == 200.0
    assert float(wp.get("CutWidth")) == 900.0


def test_real_job_exported_cutlength_cutwidth_always_match_the_actual_placed_geometry():
    # Every workpiece's CutLength/CutWidth must equal its own Lineament polygon's actual X/Y
    # span (minus the tool-path envelope offset), for *every* orientation the packer picks --
    # not just the ones that happen to go unrotated. Runs the real sample CSV through the real
    # packer (not a synthetic single-part case) specifically so this is checked against genuine,
    # varied rotation decisions, not a hand-picked one.
    text = (CSV_SAMPLE_DIR / "nesting_machine_data.csv").read_text(encoding="utf-8-sig")
    parts, errors = parse_csv_text(text)
    assert errors == []
    stock = default_stock_for(parts)
    margin = Margin(top=10, right=10, bottom=10, left=10)
    result = nanxing_optimize(parts, stock, margin, spacing=5.0)
    parts_by_id = {p.id: p for p in parts}
    xml_bytes = generate_fcc_xml(result, parts_by_id, margin, tool_diameter=6.0, part_spacing=5.0)
    root = etree.fromstring(xml_bytes)

    checked = 0
    saw_a_rotated_part = False
    for wp in root.findall(".//Workpiece"):
        cl, cw = float(wp.get("CutLength")), float(wp.get("CutWidth"))
        pts = wp.findall("Lineament/Points/Point")
        xs = [float(p.get("X")) for p in pts]
        ys = [float(p.get("Y")) for p in pts]
        xspan, yspan = max(xs) - min(xs), max(ys) - min(ys)
        checked += 1
        if wp.get("RotateAngle") == "90":
            saw_a_rotated_part = True
        assert abs(xspan - (cl + 6)) < 0.5, f"{wp.get('WorkpieceId')}: CutLength doesn't match the actual length-axis span"
        assert abs(yspan - (cw + 6)) < 0.5, f"{wp.get('WorkpieceId')}: CutWidth doesn't match the actual width-axis span"
    assert checked == 55
    assert saw_a_rotated_part, "expected at least one rotated part -- otherwise this test can't tell the fix from the old bug"
