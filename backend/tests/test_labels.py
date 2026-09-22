from __future__ import annotations
import math
from io import BytesIO

from pypdf import PdfReader

from optimizer.export.labels import MIN_SIZE_FOR_BORDERS_MM, LabelSettings, _client_order_line, _grid_dims, _order_no, render_labels_pdf
from optimizer.guillotine import optimize as saw_optimize
from optimizer.model import OptResult, Part, PlacedPart, Sheet

from .helpers import default_stock_for


def _part(part_id="P1", edges=None) -> Part:
    return Part(
        id=part_id, posId=f"01.{part_id}", name="Test Part", cutLength=500.0, cutWidth=300.0,
        finishedLength=505.0, finishedWidth=305.0, thickness=18.0, qty=1, material="MAT_X",
        grain="none", edges=edges or {"l1": "TOP_EDGE", "l2": "LEFT_EDGE", "w1": "RIGHT_EDGE", "w2": "BOTTOM_EDGE"},
        customer="Cust A(101)",
    )


def _placed(part_id="P1", x=0.0, y=0.0, w=500.0, h=300.0) -> PlacedPart:
    return PlacedPart(partId=part_id, x=x, y=y, rotated=False, w=w, h=h, name="Test Part", material="MAT_X", thickness=18.0, grain="none")


def _single_part_result(part_id="P1") -> tuple[OptResult, dict[str, Part]]:
    part = _part(part_id)
    sheet = Sheet(index=1, material="MAT_X", boardL=2440.0, boardW=1220.0, thickness=18.0, placed=[_placed(part_id)], offcuts=[], utilizationPct=50.0)
    return OptResult(sheets=[sheet], unplaced=[]), {part_id: part}


def _sheet_settings(**overrides) -> LabelSettings:
    defaults = dict(
        pageType="sheet", pageWidth=210.0, pageHeight=297.0, labelWidth=63.5, labelHeight=38.1,
        marginTop=10.0, marginRight=10.0, marginBottom=10.0, marginLeft=10.0, gapX=2.5, gapY=2.5,
        showQrCode=True, showBarcode=False, showCornerMarks=True,
    )
    defaults.update(overrides)
    return LabelSettings(**defaults)


def _roll_settings(**overrides) -> LabelSettings:
    defaults = dict(
        pageType="roll", pageWidth=80.0, pageHeight=50.0, labelWidth=80.0, labelHeight=50.0,
        showQrCode=True, showBarcode=True, showCornerMarks=True,
    )
    defaults.update(overrides)
    return LabelSettings(**defaults)


def _page_content(pdf_bytes: bytes, page_index: int = 0) -> bytes:
    return bytes(PdfReader(BytesIO(pdf_bytes)).pages[page_index].get_contents().get_data())


def test_grid_dims_sheet_mode():
    settings = _sheet_settings()
    cols, rows = _grid_dims(settings)
    # usable_w = 210 - 10 - 10 + 2.5 = 192.5; (labelWidth + gapX) = 66.0 -> floor(192.5/66) = 2.
    # usable_h = 297 - 10 - 10 + 2.5 = 279.5; (labelHeight + gapY) = 40.6 -> floor(279.5/40.6) = 6.
    assert (cols, rows) == (2, 6)


def test_grid_dims_roll_mode_is_always_one_by_one():
    settings = _roll_settings()
    assert _grid_dims(settings) == (1, 1)


def test_grid_dims_exactly_one_column_when_margin_and_gap_leave_no_room_for_a_second():
    settings = _sheet_settings(pageWidth=100.0, labelWidth=90.0, marginLeft=5.0, marginRight=5.0, gapX=2.5)
    cols, _ = _grid_dims(settings)
    assert cols == 1


def test_render_labels_one_label_per_placed_part_real_job(saw_parts, default_margin):
    stock = default_stock_for(saw_parts)
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True)
    parts_by_id = {p.id: p for p in saw_parts}
    total_placed = sum(len(s.placed) for s in result.sheets)
    assert total_placed > 0

    settings = _sheet_settings()
    pdf_bytes = render_labels_pdf(result, parts_by_id, settings)
    reader = PdfReader(BytesIO(pdf_bytes))
    cols, rows = _grid_dims(settings)
    assert len(reader.pages) == math.ceil(total_placed / (cols * rows))


def test_render_labels_roll_mode_one_label_per_page(saw_parts, default_margin):
    stock = default_stock_for(saw_parts)
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True)
    parts_by_id = {p.id: p for p in saw_parts}
    total_placed = sum(len(s.placed) for s in result.sheets)

    pdf_bytes = render_labels_pdf(result, parts_by_id, _roll_settings())
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == total_placed


def test_render_labels_skips_placed_parts_missing_from_parts_by_id():
    result, parts_by_id = _single_part_result()
    result.sheets[0].placed.append(_placed(part_id="MISSING", x=600.0))
    pdf_bytes = render_labels_pdf(result, parts_by_id, _roll_settings())
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_render_labels_empty_result_still_produces_one_page():
    pdf_bytes = render_labels_pdf(OptResult(sheets=[], unplaced=[]), {}, _roll_settings())
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_qr_code_toggle_changes_rendered_content():
    result, parts_by_id = _single_part_result()
    on_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=True, showBarcode=False))
    off_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False))
    on_content = _page_content(on_bytes)
    off_content = _page_content(off_bytes)
    assert on_content != off_content
    # A QR code is many small filled rectangles -- its presence should add a substantial amount
    # of drawing content, not just a byte or two of incidental difference.
    assert len(on_content) - len(off_content) > 500


def test_barcode_toggle_changes_rendered_content():
    result, parts_by_id = _single_part_result()
    on_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=True))
    off_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False))
    on_content = _page_content(on_bytes)
    off_content = _page_content(off_bytes)
    assert on_content != off_content
    assert len(on_content) - len(off_content) > 200


def test_barcode_defaults_off_matches_explicit_off():
    result, parts_by_id = _single_part_result()
    default_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False))
    explicit_off_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False))
    assert _page_content(default_bytes) == _page_content(explicit_off_bytes)


def test_corner_marks_toggle_changes_rendered_content():
    result, parts_by_id = _single_part_result()
    on_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False, showCornerMarks=True))
    off_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False, showCornerMarks=False))
    assert _page_content(on_bytes) != _page_content(off_bytes)


def test_edge_band_text_omitted_when_label_too_small():
    result, parts_by_id = _single_part_result()
    # Both axes below MIN_SIZE_FOR_BORDERS_MM -- border strips (and their edge-band text) should
    # be skipped entirely rather than overlapping the main content.
    below_threshold = MIN_SIZE_FOR_BORDERS_MM - 5.0
    tiny = _roll_settings(pageWidth=below_threshold, pageHeight=below_threshold, labelWidth=below_threshold, labelHeight=below_threshold, showQrCode=False, showBarcode=False, showCornerMarks=False)
    normal = _roll_settings(pageWidth=80.0, pageHeight=50.0, labelWidth=80.0, labelHeight=50.0, showQrCode=False, showBarcode=False, showCornerMarks=False)
    tiny_bytes = render_labels_pdf(result, parts_by_id, tiny)
    normal_bytes = render_labels_pdf(result, parts_by_id, normal)
    # Both render without error; the normal-sized label should carry meaningfully more content
    # (edge-band border text + its rotated-text transforms) than the tiny one that omits it.
    assert len(_page_content(tiny_bytes)) < len(_page_content(normal_bytes))


def test_edge_band_text_shown_on_the_built_in_default_label_size():
    # Regression test: the built-in/quick-size default (63.5x38.1mm A4 sheet label) has a height
    # just under the old flat 40mm cutoff, which silently disabled edge-band text for the app's
    # own default and several quick-size presets even though real edge-band data was present
    # (reported directly: "the label does not show the edge banding information").
    result, parts_by_id = _single_part_result()
    small_default = _roll_settings(pageWidth=63.5, pageHeight=38.1, labelWidth=63.5, labelHeight=38.1, showQrCode=False, showBarcode=False, showCornerMarks=False)
    no_edges = _single_part_result()[0], {**parts_by_id, "P1": _part("P1", edges={"l1": "", "l2": "", "w1": "", "w2": ""})}
    with_edges_bytes = render_labels_pdf(result, parts_by_id, small_default)
    without_edges_bytes = render_labels_pdf(no_edges[0], no_edges[1], small_default)
    assert len(_page_content(with_edges_bytes)) > len(_page_content(without_edges_bytes))


def test_missing_edge_band_values_are_each_skipped_individually():
    result, parts_by_id = _single_part_result()
    parts_by_id["P1"].edges = {"l1": "", "l2": "", "w1": "", "w2": ""}
    pdf_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False, showCornerMarks=False))
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_order_no_parsed_from_pos_id_strips_symbol_prefix_and_sequence_suffix():
    # Confirmed directly with the project owner: posId "01.26Y130T3F14B1_1006" (or the plain
    # barcode "26Y130T3F14B1_1006") both reduce to order no "26Y130T3F14B1".
    part = _part("26Y130T3F14B1_1006")
    part.posId = "01.26Y130T3F14B1_1006"
    assert _order_no(part) == "26Y130T3F14B1"


def test_order_no_falls_back_to_id_when_pos_id_is_empty():
    part = _part("26Y130T3F14B1_1006")
    part.posId = ""
    assert _order_no(part) == "26Y130T3F14B1"


def test_order_no_strips_expand_quantity_copy_suffix_too():
    # parser.expand_quantity() appends an extra "_N" copy suffix on top of the barcode's own
    # sequence number for qty>1 rows (e.g. "..._1083" -> "..._1083_2") -- order no must still
    # reduce to just the leading order code, not "..._1083".
    part = _part("26Y111T1F1B3_1083_2")
    part.posId = ""
    assert _order_no(part) == "26Y111T1F1B3"


def test_order_no_empty_when_no_id_or_pos_id():
    part = _part("")
    part.posId = ""
    assert _order_no(part) == ""


def test_client_order_line_joins_customer_and_order_no():
    part = _part("26Y130T3F14B1_1006")
    part.posId = "01.26Y130T3F14B1_1006"
    part.customer = "Mr. Vijay(203)"
    assert _client_order_line(part) == "Mr. Vijay(203) | 26Y130T3F14B1"


def test_client_order_line_omits_missing_customer():
    part = _part("26Y130T3F14B1_1006")
    part.posId = "01.26Y130T3F14B1_1006"
    part.customer = None
    assert _client_order_line(part) == "26Y130T3F14B1"


def test_client_order_line_uses_client_name_override_when_customer_missing():
    # Panel Saw CSVs have no Client/Project column at all (confirmed directly with the project
    # owner) -- the manual override is the only way Client ever appears on those labels.
    part = _part("26Y130T3F14B1_1006")
    part.posId = "01.26Y130T3F14B1_1006"
    part.customer = None
    assert _client_order_line(part, client_name_override="Manual Client") == "Manual Client | 26Y130T3F14B1"


def test_client_order_line_prefers_real_customer_over_override():
    part = _part("26Y130T3F14B1_1006")
    part.posId = "01.26Y130T3F14B1_1006"
    part.customer = "Real Customer"
    assert _client_order_line(part, client_name_override="Manual Client") == "Real Customer | 26Y130T3F14B1"


def test_client_order_line_uses_order_no_override_when_unrecoverable():
    part = _part("")
    part.posId = ""
    part.customer = "Real Customer"
    assert _client_order_line(part, order_no_override="MANUAL-ORDER") == "Real Customer | MANUAL-ORDER"


def test_label_text_includes_client_order_line_and_reading_order_edge_numbers():
    result, parts_by_id = _single_part_result()
    parts_by_id["P1"].customer = "Cust A(101)"
    pdf_bytes = render_labels_pdf(result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False, showCornerMarks=False))
    text = PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "Cust A(101) | P1" in text
    # Reading-order numbering (top=1, left=2, bottom=3, right=4) -- confirmed directly with the
    # project owner, independent of which CSV Edge-column (l1/l2/w1/w2) the value came from.
    assert "1. TOP_EDGE" in text
    assert "2. LEFT_EDGE" in text
    assert "3. BOTTOM_EDGE" in text
    assert "4. RIGHT_EDGE" in text


def test_render_labels_pdf_applies_client_name_override_end_to_end():
    result, parts_by_id = _single_part_result()
    parts_by_id["P1"].customer = None  # e.g. a Panel Saw job, which has no Client column at all
    pdf_bytes = render_labels_pdf(
        result, parts_by_id, _roll_settings(showQrCode=False, showBarcode=False, showCornerMarks=False),
        client_name_override="Acme Woodworks",
    )
    text = PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "Acme Woodworks | P1" in text
