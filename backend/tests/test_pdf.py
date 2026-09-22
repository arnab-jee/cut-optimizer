from __future__ import annotations
from io import BytesIO

from pypdf import PdfReader

from optimizer.export.pdf import (
    _color_for_index,
    _cut_line_bounds,
    _cutting_list,
    _deduplicate_layouts,
    _grain_direction_is_vertical,
    _nominal_dims,
    _sheet_signature,
    _sidebar_bottom_boxes,
    render_layout_pdf,
)
from optimizer.guillotine import optimize as saw_optimize
from optimizer.model import CutInstruction, Margin, OptResult, PlacedPart, Sheet

from .helpers import default_stock_for


def _saw_result(saw_parts, default_margin):
    stock = default_stock_for(saw_parts)
    return saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True)


def _sheet(index=1, placed=None, boardL=2440.0, boardW=1220.0, material="MAT", thickness=18.0) -> Sheet:
    return Sheet(
        index=index, material=material, boardL=boardL, boardW=boardW, thickness=thickness,
        placed=placed or [], offcuts=[], utilizationPct=50.0,
    )


def _part(name="P", x=0.0, y=0.0, w=100.0, h=200.0, rotated=False, grain="none") -> PlacedPart:
    return PlacedPart(partId=name, x=x, y=y, rotated=rotated, w=w, h=h, name=name, material="MAT", thickness=18.0, grain=grain)


def test_renders_one_page_per_unique_layout(saw_parts, default_margin):
    result = _saw_result(saw_parts, default_margin)
    pdf_bytes = render_layout_pdf(result)
    reader = PdfReader(BytesIO(pdf_bytes))
    layouts = _deduplicate_layouts(result.sheets)
    assert len(reader.pages) == len(layouts)


def test_page_text_includes_header_and_stats(saw_parts, default_margin):
    result = _saw_result(saw_parts, default_margin)
    pdf_bytes = render_layout_pdf(result)
    reader = PdfReader(BytesIO(pdf_bytes))
    first_sheet = result.sheets[0]
    text = reader.pages[0].extract_text()
    assert "Job Layout" in text
    assert "Cutting List" in text
    assert "Occurrences" in text
    assert "Grain Direction" in text
    assert first_sheet.material in text
    assert "Job Sheets :" in text
    assert "Sheet Cut Length :" in text


def test_client_name_and_order_no_render_when_provided(saw_parts, default_margin):
    result = _saw_result(saw_parts, default_margin)
    pdf_bytes = render_layout_pdf(result, client_name_override="Acme Furniture", order_no_override="26Y130T3F14B1")
    text = PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "Client Name : Acme Furniture" in text
    assert "Job Reference : 26Y130T3F14B1" in text


def test_client_name_and_order_no_blank_by_default(saw_parts, default_margin):
    # No fabricated values -- same rule as every other contact-info field on this page (M3).
    # Checked against the raw content stream rather than extract_text(): with no override, the
    # label is drawn as its own literal "(Client Name :)" Tj string with nothing appended.
    result = _saw_result(saw_parts, default_margin)
    pdf_bytes = render_layout_pdf(result)
    content = bytes(PdfReader(BytesIO(pdf_bytes)).pages[0].get_contents().get_data()).decode("latin-1")
    assert "(Client Name :) Tj" in content
    assert "(Job Reference :) Tj" in content


def test_empty_result_renders_zero_pages():
    pdf_bytes = render_layout_pdf(OptResult(sheets=[], unplaced=[]))
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 0
    assert pdf_bytes.startswith(b"%PDF")


def test_identical_sheets_deduplicate_with_occurrence_count():
    part_a = _part(name="A", x=0, y=0, w=100, h=200)
    sheet1 = _sheet(index=1, placed=[part_a])
    sheet2 = _sheet(index=2, placed=[_part(name="A", x=0, y=0, w=100, h=200)])
    sheet3 = _sheet(index=3, placed=[_part(name="B", x=0, y=0, w=50, h=50)])
    layouts = _deduplicate_layouts([sheet1, sheet2, sheet3])
    assert len(layouts) == 2
    (rep1, occ1), (rep2, occ2) = layouts
    assert rep1.index == 1 and occ1 == 2
    assert rep2.index == 3 and occ2 == 1


def test_sheet_signature_ignores_which_physical_sheet_it_is():
    sheet1 = _sheet(index=1, placed=[_part(name="A", x=1.0, y=2.0, w=100.0, h=200.0)])
    sheet2 = _sheet(index=99, placed=[_part(name="A", x=1.0, y=2.0, w=100.0, h=200.0)])
    assert _sheet_signature(sheet1) == _sheet_signature(sheet2)


def test_cutting_list_groups_by_name_and_nominal_dims_with_running_symbols():
    # Two instances of the same (name, dims) group share symbol 1; a same-named part with
    # different dimensions gets its own symbol, matching the reference's per-(name,dims) grouping.
    sheet = _sheet(placed=[
        _part(name="SHUTTER", x=0, y=0, w=100.0, h=200.0, rotated=False),
        _part(name="SHUTTER", x=100, y=0, w=100.0, h=200.0, rotated=False),
        _part(name="SHUTTER", x=200, y=0, w=50.0, h=80.0, rotated=False),
    ])
    rows, symbol_by_index = _cutting_list(sheet)
    assert symbol_by_index[0] == symbol_by_index[1]
    assert symbol_by_index[2] != symbol_by_index[0]
    assert {r["symbol"]: r["qty"] for r in rows} == {symbol_by_index[0]: 2, symbol_by_index[2]: 1}


def test_cutting_list_uses_nominal_dims_not_rotated_footprint():
    # A part placed rotated has w/h swapped from its nominal cutLength/cutWidth; the cutting
    # list must report the nominal (pre-rotation) Length/Width, matching the reference's
    # cutting-list values which stay constant regardless of how a part landed on the sheet.
    sheet = _sheet(placed=[_part(name="X", w=200.0, h=100.0, rotated=True)])
    rows, _ = _cutting_list(sheet)
    assert rows[0]["length"] == 100.0
    assert rows[0]["width"] == 200.0


def test_nominal_dims_recovers_length_grain_natural_pose():
    # Issues/issues_001.md fix: a grain="length" part's natural (rotated=False) pose has
    # cutWidth on local x (w) and cutLength on local y (h) — the opposite pairing from
    # grain="none"/"width" parts. _nominal_dims must be grain-aware to still recover the true
    # (cutLength, cutWidth) for the cutting list, which groups/reports parts by their
    # orientation-independent nominal size regardless of how any one instance got placed.
    part = _part(name="X", w=556.4, h=1323.4, rotated=False, grain="length")
    length, width = _nominal_dims(part)
    assert (length, width) == (1323.4, 556.4)


def test_nominal_dims_unaffected_for_width_and_none_grain():
    for grain in ("width", "none"):
        part = _part(name="X", w=1323.4, h=556.4, rotated=False, grain=grain)
        assert _nominal_dims(part) == (1323.4, 556.4)


def test_edge_dim_labels_match_actual_placed_footprint_not_nominal_swap():
    # Real bug (reported via a rendered PDF, sheet with two identical grain="none" parts
    # placed at different rotations): the on-box edge labels must always describe the box as
    # actually drawn (part.w along the top, part.h down the side), never the orientation-
    # independent nominal (cutLength, cutWidth) pair _cutting_list uses. Using _nominal_dims
    # here used to get this backwards for exactly one of the two rotation states per grain,
    # since _nominal_dims intentionally normalizes away rotation while the drawn box does not.
    # w != h and rotated=False, grain="none" is the specific case that was mislabeled. (Large
    # enough that both edge labels clear _draw_part_edge_dims's own minimum-size guard once
    # scaled down to fit the page.)
    part = _part(name="X", x=0.0, y=0.0, w=800.0, h=400.0, rotated=False, grain="none")
    sheet = _sheet(placed=[part])
    result = OptResult(sheets=[sheet], unplaced=[])
    pdf_bytes = render_layout_pdf(result)
    content = bytes(PdfReader(BytesIO(pdf_bytes)).pages[0].get_contents().get_data()).decode("latin-1")

    rect_idx = content.index("re B*")
    top_label_idx = content.index("(800 mm)", rect_idx)
    side_label_idx = content.index("(400 mm)", rect_idx)
    # The top label (spanning the box's actual width) is drawn as a plain, unrotated Tj
    # right after the rect; the side label is drawn inside a saveState/rotate/restoreState
    # block ("q ... Q") — so whichever text sits outside that block is the top label.
    rotate_block_start = content.index(" cm\n", rect_idx)
    rotate_block_end = content.index("Q", rotate_block_start)
    assert not (rotate_block_start < top_label_idx < rotate_block_end)
    assert rotate_block_start < side_label_idx < rotate_block_end


def test_grain_direction_mapping():
    # Confirmed with the project owner: length-grain -> vertical arrow (board.length draws
    # vertically in this layout), width-grain -> horizontal, none -> no arrow at all.
    assert _grain_direction_is_vertical("length") is True
    assert _grain_direction_is_vertical("width") is False
    assert _grain_direction_is_vertical("none") is None


def test_sidebar_bottom_boxes_stay_above_the_footer_strip():
    # Regression: the Grain Direction / Occurrences boxes originally extended down to the page
    # frame's bottom edge instead of stopping at the footer strip's top divider, so they visually
    # overlapped the footer text. Both boxes must sit entirely at or above footer_y1.
    footer_y1 = 62.35
    content_y1 = 813.54
    grain_top, occ_bottom, occ_top = _sidebar_bottom_boxes(footer_y1, content_y1)
    assert grain_top > footer_y1
    assert occ_bottom == grain_top
    assert occ_top > occ_bottom
    assert occ_top <= content_y1


def test_color_cycles_through_palette_by_placement_order():
    colors = [_color_for_index(i) for i in range(16)]
    assert len(set(colors)) == 16
    assert _color_for_index(0) == _color_for_index(16)


def test_cut_line_bounds_vertical_uses_margin_top_as_start():
    # offset already has margin.left baked in (build_cuts_for_sheet); .length is only a span,
    # so the missing start point along Y has to come from margin.top here.
    margin = Margin(top=5.0, right=10.0, bottom=10.0, left=5.0)
    cut = CutInstruction(orientation="vertical", offset=305.0, length=1200.0, sheetIndex=1)
    x1, y1, x2, y2 = _cut_line_bounds(cut, margin)
    assert (x1, x2) == (305.0, 305.0)
    assert (y1, y2) == (5.0, 1205.0)


def test_cut_line_bounds_horizontal_uses_margin_left_as_start():
    margin = Margin(top=5.0, right=10.0, bottom=10.0, left=5.0)
    cut = CutInstruction(orientation="horizontal", offset=610.0, length=2400.0, sheetIndex=1)
    x1, y1, x2, y2 = _cut_line_bounds(cut, margin)
    assert (y1, y2) == (610.0, 610.0)
    assert (x1, x2) == (5.0, 2405.0)


def test_render_layout_pdf_accepts_margin_and_draws_cut_lines_without_crashing(saw_parts, default_margin):
    # Panel Saw is the only path that ever produces non-empty cuts (nanxing.py never populates
    # OptResult.cuts) — this exercises render_layout_pdf's cut-line overlay end to end on a real
    # saw job, asserting it doesn't crash and still yields a valid, non-empty PDF. show_cut_lines
    # defaults to False (Issues/issues_004.md follow-up), so it's passed explicitly here to
    # actually exercise the overlay-drawing path this test is named for.
    result = _saw_result(saw_parts, default_margin)
    assert len(result.cuts) > 0
    pdf_bytes = render_layout_pdf(result, default_margin, show_cut_lines=True)
    assert pdf_bytes.startswith(b"%PDF")
    reader = PdfReader(BytesIO(pdf_bytes))
    layouts = _deduplicate_layouts(result.sheets)
    assert len(reader.pages) == len(layouts)


def test_show_cut_lines_false_suppresses_the_drawn_overlay(saw_parts, default_margin):
    # Issues/issues_003.md: the dashed cut-line overlay confused panel-saw operators on some
    # layouts, so it needs to be toggleable, defaulting on. _draw_cut_lines sets a [3, 2] dash
    # pattern before drawing any cut line — its literal PDF content-stream operator ("[3 2] 0 d")
    # is a precise, unambiguous signal that lines were actually drawn, not just that the page
    # rendered without crashing.
    result = _saw_result(saw_parts, default_margin)
    on_bytes = render_layout_pdf(result, default_margin, show_cut_lines=True)
    off_bytes = render_layout_pdf(result, default_margin, show_cut_lines=False)
    on_content = bytes(PdfReader(BytesIO(on_bytes)).pages[0].get_contents().get_data())
    off_content = bytes(PdfReader(BytesIO(off_bytes)).pages[0].get_contents().get_data())
    assert b"[3 2] 0 d" in on_content
    assert b"[3 2] 0 d" not in off_content


def test_show_cut_lines_defaults_to_false(saw_parts, default_margin):
    # The overlay defaults off (flipped after Issues/issues_004.md's dense-layout fix landed) —
    # a caller that omits show_cut_lines entirely must get the same "no lines drawn" behavior as
    # explicitly passing False, not the old opt-out default.
    result = _saw_result(saw_parts, default_margin)
    default_bytes = render_layout_pdf(result, default_margin)
    off_bytes = render_layout_pdf(result, default_margin, show_cut_lines=False)
    default_content = bytes(PdfReader(BytesIO(default_bytes)).pages[0].get_contents().get_data())
    off_content = bytes(PdfReader(BytesIO(off_bytes)).pages[0].get_contents().get_data())
    assert b"[3 2] 0 d" not in default_content
    assert default_content == off_content


def test_show_cut_lines_false_still_reports_cut_length_stats(saw_parts, default_margin):
    # Hiding the visual overlay shouldn't hide the numeric "Cut Length" stats — those aren't the
    # confusing part per the reported issue, only the drawn dashed lines are.
    result = _saw_result(saw_parts, default_margin)
    pdf_bytes = render_layout_pdf(result, default_margin, show_cut_lines=False)
    text = PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "Sheet Cut Length :" in text


def test_render_layout_pdf_without_margin_still_works():
    # margin is optional (defaults to a zero margin) so existing callers that only care about
    # part/board geometry aren't forced to supply one.
    pdf_bytes = render_layout_pdf(OptResult(sheets=[], unplaced=[]))
    assert pdf_bytes.startswith(b"%PDF")
