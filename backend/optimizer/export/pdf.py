from __future__ import annotations
from collections import Counter
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from ..model import CutInstruction, Margin, OptResult, PlacedPart, Sheet
from .xml import fmt_num

# Layout structure follows sample_data/Max Cut Optimization Drawings/max_cut.pdf (Updates/
# update_002.md, 2nd revision of that filename — see CLAUDE.md's "Directory reorg" note on
# reused filenames): a sidebar (material/sheet size, cutting list, occurrence count, grain
# arrow) beside a main drawing area (job header, per-material/per-job stats grid, board
# diagram). This *replaces* the earlier NirvanaTec-style redesign — the update explicitly asks
# to follow this different reference's layout instead, not add a second style. One departure
# from the reference, per the update: panel/part fill colors are kept (the reference itself is
# plain black-and-white line art).
PALETTE: list[tuple[float, float, float]] = [
    (1.00, 0.72, 0.53),
    (0.60, 0.60, 1.00),
    (0.80, 1.00, 0.60),
    (0.80, 0.40, 1.00),
    (0.40, 1.00, 1.00),
    (0.20, 1.00, 0.80),
    (0.20, 0.80, 0.20),
    (0.80, 1.00, 0.20),
    (1.00, 0.88, 0.64),
    (0.40, 0.80, 1.00),
    (0.60, 0.40, 1.00),
    (1.00, 0.53, 0.60),
    (0.40, 1.00, 0.20),
    (1.00, 1.00, 0.40),
    (1.00, 0.60, 1.00),
    (0.65, 1.00, 0.85),
]

PART_STROKE = (0.0, 0.0, 0.0)
NARROW_WIDTH_PT = 40.0
MIN_LABEL_PT = 6.0

FRAME_MARGIN = 10 * mm
PAD = 4 * mm
SIDEBAR_W_FRAC = 0.30
FOOTER_H = 12 * mm
TOP_BLOCK_H = 22 * mm
BOTTOM_BOX_H = 26 * mm
DIM_LABEL_MARGIN = 14 * mm


def _color_for_index(index: int) -> tuple[float, float, float]:
    return PALETTE[index % len(PALETTE)]


def _nominal_dims(p: PlacedPart) -> tuple[float, float]:
    """Recovers the part's original (cutLength, cutWidth) from its placed footprint (w, h) and
    rotated flag. Must mirror optimizer/saw_packing.py's/nanxing_packing.py's _footprint(): for
    grain="length" parts, the natural (rotated=False) pose already has cutLength on the local
    x-axis (w), so p.rotated alone isn't enough — see that function's docstring for why."""
    swapped = (p.grain == "length") != p.rotated
    if swapped:
        return p.h, p.w
    return p.w, p.h


def _shrink_to_fit(canvas: Canvas, text: str, max_width: float, start_size: float, min_size: float = 4.0, font: str = "Helvetica-Bold") -> float:
    size = start_size
    while size > min_size and canvas.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def _wrap_to_lines(canvas: Canvas, text: str, max_width: float, font: str, size: float, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    remaining = text
    while remaining and len(lines) < max_lines:
        if canvas.stringWidth(remaining, font, size) <= max_width:
            lines.append(remaining)
            remaining = ""
            break
        cut = len(remaining)
        while cut > 1 and canvas.stringWidth(remaining[:cut], font, size) > max_width:
            cut -= 1
        lines.append(remaining[:cut])
        remaining = remaining[cut:]
    return lines


def _sheet_signature(sheet: Sheet) -> tuple:
    parts_sig = tuple(sorted(
        (round(p.x, 1), round(p.y, 1), round(p.w, 1), round(p.h, 1), p.name)
        for p in sheet.placed
    ))
    return (sheet.material, round(sheet.thickness, 2), round(sheet.boardL, 1), round(sheet.boardW, 1), parts_sig)


def _deduplicate_layouts(sheets: list[Sheet]) -> list[tuple[Sheet, int]]:
    """Groups sheets with an identical arrangement (same board/material and the same set of
    placed-part positions) into one printed layout with an occurrence count — mirrors the
    reference's 'Occurrences: xN' badge, which prints one page for N physically-identical
    boards instead of N near-duplicate pages. Physical sheet/part counts used elsewhere (Job
    Sheets, Job Panels, etc.) are computed from the un-deduplicated `sheets` list."""
    seen: dict[tuple, int] = {}
    layouts: list[list] = []
    for sheet in sheets:
        sig = _sheet_signature(sheet)
        if sig in seen:
            layouts[seen[sig]][1] += 1
        else:
            seen[sig] = len(layouts)
            layouts.append([sheet, 1])
    return [(s, c) for s, c in layouts]


def _cutting_list(sheet: Sheet) -> tuple[list[dict], dict[int, int]]:
    """Groups placed parts by (name, nominal length, nominal width) in order of first
    appearance, assigning each group a running 'Symbol' number — the reference labels every
    instance of a group in the drawing as '{symbol}.{name}' and lists Name/Length/Width/Qty
    once per group in the sidebar cutting list, rather than repeating a full label per
    instance. Grouping includes name deliberately: two differently-named parts that happen to
    share a size are still two different physical pieces an operator needs to tell apart (e.g.
    a left/right pair) — confirmed directly with the project owner (2026-09-22). The sidebar's
    own Name column (added the same pass) is what makes the distinction visible, rather than
    two rows with identical Length/Width and no visible reason they're different."""
    groups: dict[tuple, int] = {}
    counts: dict[int, int] = {}
    dims: dict[int, tuple[str, float, float]] = {}
    symbol_by_index: dict[int, int] = {}
    next_symbol = 1
    for i, p in enumerate(sheet.placed):
        length, width = _nominal_dims(p)
        key = (p.name, round(length, 1), round(width, 1))
        if key not in groups:
            groups[key] = next_symbol
            dims[next_symbol] = (p.name, length, width)
            counts[next_symbol] = 0
            next_symbol += 1
        symbol = groups[key]
        counts[symbol] += 1
        symbol_by_index[i] = symbol
    rows = [
        {"symbol": s, "name": dims[s][0], "length": dims[s][1], "width": dims[s][2], "qty": counts[s]}
        for s in sorted(counts)
    ]
    return rows, symbol_by_index


def _draw_double_arrow(canvas: Canvas, cx: float, cy: float, length: float, vertical: bool) -> None:
    half = length / 2
    head = 5.0
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(1.2)
    if vertical:
        canvas.line(cx, cy - half, cx, cy + half)
        canvas.line(cx, cy + half, cx - head, cy + half - head)
        canvas.line(cx, cy + half, cx + head, cy + half - head)
        canvas.line(cx, cy - half, cx - head, cy - half + head)
        canvas.line(cx, cy - half, cx + head, cy - half + head)
    else:
        canvas.line(cx - half, cy, cx + half, cy)
        canvas.line(cx + half, cy, cx + half - head, cy - head)
        canvas.line(cx + half, cy, cx + half - head, cy + head)
        canvas.line(cx - half, cy, cx - half + head, cy - head)
        canvas.line(cx - half, cy, cx - half + head, cy + head)


def _grain_direction_is_vertical(grain: str) -> bool | None:
    """None means 'no arrow' (grain == 'none'). Confirmed with the project owner: grain
    == 'length' (part length parallel to grain, grain_logic.md) maps to a vertical arrow since
    board.length draws vertically in this layout; grain == 'width' maps to horizontal."""
    if grain == "length":
        return True
    if grain == "width":
        return False
    return None


def _draw_dim_line_horizontal(canvas: Canvas, x0: float, x1: float, y: float, label: str) -> None:
    tick = 3.0
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.line(x0, y, x1, y)
    canvas.line(x0, y - tick, x0, y + tick)
    canvas.line(x1, y - tick, x1, y + tick)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString((x0 + x1) / 2, y + 3, label)


def _draw_dim_line_vertical(canvas: Canvas, y0: float, y1: float, x: float, label: str) -> None:
    tick = 3.0
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.line(x, y0, x, y1)
    canvas.line(x - tick, y0, x + tick, y0)
    canvas.line(x - tick, y1, x + tick, y1)
    canvas.saveState()
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica", 8)
    canvas.translate(x - 6, (y0 + y1) / 2)
    canvas.rotate(90)
    canvas.drawCentredString(0, 0, label)
    canvas.restoreState()


def _draw_part_edge_dims(canvas: Canvas, x: float, y: float, w: float, h: float, width_mm: float, length_mm: float) -> None:
    # h must leave room for both this top-edge label and the independently-drawn, vertically
    # centered symbol/name label below it (_draw_part_symbol_label) without the two colliding —
    # a block short enough to fail this still gets the symbol label on its own smaller guard.
    if w < 20 or h < 26:
        return
    canvas.setFillColorRGB(0, 0, 0)
    width_text = f"{fmt_num(width_mm)} mm"
    wsize = _shrink_to_fit(canvas, width_text, w - 4, 7.0, min_size=4.0, font="Helvetica")
    canvas.setFont("Helvetica", wsize)
    canvas.drawCentredString(x + w / 2, y + h - wsize - 2, width_text)
    if h > 16:
        length_text = f"{fmt_num(length_mm)} mm"
        lsize = _shrink_to_fit(canvas, length_text, h - 4, 7.0, min_size=4.0, font="Helvetica")
        canvas.saveState()
        canvas.translate(x + lsize + 1, y + 2)
        canvas.rotate(90)
        canvas.setFont("Helvetica", lsize)
        canvas.drawString(0, 0, length_text)
        canvas.restoreState()


def _draw_part_symbol_label(canvas: Canvas, x: float, y: float, w: float, h: float, label: str) -> None:
    if w < MIN_LABEL_PT or h < MIN_LABEL_PT:
        return
    canvas.setFillColorRGB(0, 0, 0)
    narrow = w < h and w < NARROW_WIDTH_PT
    if narrow:
        size = _shrink_to_fit(canvas, label, h - 8, 7.0)
        canvas.saveState()
        canvas.translate(x + w / 2 + size / 2, y + h / 2)
        canvas.rotate(90)
        canvas.setFont("Helvetica-Bold", size)
        canvas.drawCentredString(0, 0, label)
        canvas.restoreState()
    else:
        size = _shrink_to_fit(canvas, label, w - 6, 7.0)
        canvas.setFont("Helvetica-Bold", size)
        canvas.drawCentredString(x + w / 2, y + h / 2 - size / 3, label)


def _draw_sidebar_top(canvas: Canvas, sheet: Sheet, x0: float, x1: float, top_y: float) -> None:
    width = x1 - x0
    canvas.setFillColorRGB(0, 0, 0)
    name_lines = _wrap_to_lines(canvas, sheet.material, width - 2 * PAD, "Helvetica-Bold", 10)
    canvas.setFont("Helvetica-Bold", 10)
    y = top_y - 12
    for line in name_lines:
        canvas.drawCentredString((x0 + x1) / 2, y, line)
        y -= 11
    canvas.setFont("Helvetica", 8)
    size_text = f"Sheet Size : {fmt_num(sheet.boardL)} mm x {fmt_num(sheet.boardW)} mm x {fmt_num(sheet.thickness)} mm"
    for line in _wrap_to_lines(canvas, size_text, width - 2 * PAD, "Helvetica", 8):
        canvas.drawCentredString((x0 + x1) / 2, y, line)
        y -= 10


def _cutting_list_columns(x0: float, x1: float) -> tuple[float, float, float, float, float]:
    """Returns (col_symbol, col_name, col_length, col_width, col_qty) — the x position of each
    Cutting List column, factored out so the gaps between them (does "Sym"'s header, or a
    2-digit symbol number, actually fit before col_name starts? does "340.5 mm" fit before
    col_qty?) can be checked directly in a test rather than only by eyeballing a rendered page.
    Real bug caught this way (2026-09-22): the Name column originally started right after
    col_symbol with almost no gap, so a rendered PDF showed "1Bed Bottom" and a "Symbol"/"Name"
    header running together with no space. col_symbol starts at x0+PAD (not a fraction of the
    sidebar width like the others), matching every other sidebar element's own left inset."""
    col_symbol = x0 + PAD
    col_name = x0 + (x1 - x0) * 0.175
    col_length = x0 + (x1 - x0) * 0.485
    col_width = x0 + (x1 - x0) * 0.683
    col_qty = x0 + (x1 - x0) * 0.893
    return col_symbol, col_name, col_length, col_width, col_qty


def _draw_cutting_list(canvas: Canvas, rows: list[dict], x0: float, x1: float, top_y: float, bottom_y: float) -> None:
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawCentredString((x0 + x1) / 2, top_y - 10, "Cutting List")
    header_y = top_y - 24
    col_symbol, col_name, col_length, col_width, col_qty = _cutting_list_columns(x0, x1)
    name_w = col_length - col_name - 2.0
    canvas.setFont("Helvetica-Bold", 7)
    # "Sym" not "Symbol" -- the column itself only ever holds a 1-2 digit number (matching the
    # already-abbreviated "Qty"); the full word doesn't fit before col_name without either
    # overlapping it or squeezing the new Name column uncomfortably narrow.
    canvas.drawString(col_symbol, header_y, "Sym")
    canvas.drawString(col_name, header_y, "Name")
    canvas.drawString(col_length, header_y, "Length")
    canvas.drawString(col_width, header_y, "Width")
    canvas.drawString(col_qty, header_y, "Qty")

    available = header_y - 6 - bottom_y
    if not rows:
        return
    row_h = min(12.0, max(6.0, available / len(rows)))
    y = header_y - 12
    for row in rows:
        if y < bottom_y:
            break
        canvas.setFont("Helvetica", 7)
        canvas.drawString(col_symbol, y, str(row["symbol"]))
        canvas.drawString(col_length, y, f"{fmt_num(row['length'])} mm")
        canvas.drawString(col_width, y, f"{fmt_num(row['width'])} mm")
        canvas.drawString(col_qty, y, str(row["qty"]))
        name_size = _shrink_to_fit(canvas, row["name"], name_w, 7.0, min_size=4.5, font="Helvetica")
        canvas.setFont("Helvetica", name_size)
        canvas.drawString(col_name, y, row["name"])
        y -= row_h


def _draw_occurrence_box(canvas: Canvas, count: int, x0: float, x1: float, y0: float, y1: float) -> None:
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.rect(x0, y0, x1 - x0, y1 - y0, fill=0, stroke=1)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(x0 + PAD, y1 - 12, "Occurrences")
    canvas.setFont("Helvetica-Bold", 24)
    canvas.drawCentredString((x0 + x1) / 2, y0 + (y1 - y0) / 2 - 14, f"x{count}")


def _draw_grain_box(canvas: Canvas, grain: str, x0: float, x1: float, y0: float, y1: float) -> None:
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.rect(x0, y0, x1 - x0, y1 - y0, fill=0, stroke=1)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(x0 + PAD, y1 - 12, "Grain Direction")
    vertical = _grain_direction_is_vertical(grain)
    if vertical is None:
        return
    cx = (x0 + x1) / 2
    cy = y0 + (y1 - y0) / 2 - 10
    icon_len = min(x1 - x0, y1 - y0 - 24) * 0.6
    _draw_double_arrow(canvas, cx, cy, icon_len, vertical)


def _draw_main_header(
    canvas: Canvas, sheet: Sheet, layout_index: int, layout_total: int, occurrence: int,
    job_stats: dict, x0: float, x1: float, top_y: float,
    client_name: str = "", order_no: str = "",
) -> float:
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawCentredString((x0 + x1) / 2, top_y - 16, "Job Layout")

    y = top_y - 34
    canvas.setFont("Helvetica", 9)
    summary = (
        f"Layout {layout_index} of {layout_total} (x{occurrence}) - {sheet.material} "
        f"({fmt_num(sheet.boardL)} mm x {fmt_num(sheet.boardW)} mm)"
    )
    canvas.drawString(x0, y, summary)
    y -= 16

    col1 = x0
    col2 = x0 + (x1 - x0) * 0.36
    col3 = x0 + (x1 - x0) * 0.68
    row_h = 15.0
    canvas.setFont("Helvetica", 8)
    # Client Name/Job Reference come from the same job-level clientNameOverride/orderNoOverride
    # the labels export already uses (Panel Saw CSVs have no Client/Project column at all, so
    # these are manual entries, not derived from part data) — left blank when not provided,
    # same "no fabricated values" rule as before. Phone/Fax/Cell No still have no data source
    # anywhere in this app (contact-info fields the reference itself also leaves blank in every
    # sample page) — labels only. Date Required is likewise left blank (no "requested-by"
    # concept exists here); the generation timestamp is in the page footer instead.
    rows = [
        (f"Client Name : {client_name}" if client_name else "Client Name :", f"Job Reference : {order_no}" if order_no else "Job Reference :"),
        ("Date Required :", f"Sheets of this Material : {job_stats['sheets_of_material']}", f"Job Sheets : {job_stats['job_sheets']}"),
        ("Phone Number :", f"Sheet Panels : {len(sheet.placed)}", f"Job Panels : {job_stats['job_panels']}"),
        ("Fax Number :", f"Layout Wastage : {100 - sheet.utilizationPct:.2f}%", f"Job Wastage : {job_stats['job_wastage']:.2f}%"),
        ("Cell No :", f"Sheet Cut Length : {fmt_num(job_stats['sheet_cut_length'])} mm", f"Job Cut Length : {fmt_num(job_stats['job_cut_length'])} mm"),
    ]
    col_widths = [col2 - col1 - PAD, col3 - col2 - PAD, x1 - col3]
    cols = [col1, col2, col3]
    # Client Name/Job Reference are free-text user input (unlike every other cell here, which is
    # always a short label or computed number) and could overflow their column — shrink-to-fit
    # guards every cell uniformly rather than special-casing just those two.
    for row in rows:
        for i, text in enumerate(row):
            size = _shrink_to_fit(canvas, text, col_widths[i], 8.0, min_size=5.0, font="Helvetica")
            canvas.setFont("Helvetica", size)
            canvas.drawString(cols[i], y, text)
        y -= row_h
    return y


def _cut_line_bounds(cut: CutInstruction, margin: Margin) -> tuple[float, float, float, float]:
    """Returns (x1, y1, x2, y2) in absolute board mm, pre-scale/origin, for one guillotine cut.
    CutInstruction.offset already has margin.left/.top baked in (optimizer/guillotine.py's
    build_cuts_for_sheet adds it when the instruction is built) — but .length is only a span,
    not an end coordinate, so the line's start along the *other* axis has to come from margin
    here; there's nowhere else to get it once offset/length alone are on hand."""
    if cut.orientation == "vertical":
        return cut.offset, margin.top, cut.offset, margin.top + cut.length
    return margin.left, cut.offset, margin.left + cut.length, cut.offset


def _draw_cut_lines(canvas: Canvas, cuts: list[CutInstruction], margin: Margin, origin_x: float, origin_y: float, scale: float) -> None:
    if not cuts:
        return
    canvas.saveState()
    canvas.setStrokeColorRGB(0.85, 0.1, 0.1)
    canvas.setLineWidth(0.6)
    canvas.setDash([3, 2])
    for cut in cuts:
        x1, y1, x2, y2 = _cut_line_bounds(cut, margin)
        canvas.line(origin_x + x1 * scale, origin_y + y1 * scale, origin_x + x2 * scale, origin_y + y2 * scale)
    canvas.restoreState()


def _draw_board_drawing(
    canvas: Canvas, sheet: Sheet, symbol_by_index: dict[int, int], cuts: list[CutInstruction], margin: Margin,
    x0: float, x1: float, top_y: float, bottom_y: float,
) -> None:
    avail_w = (x1 - x0) - DIM_LABEL_MARGIN
    avail_h = (top_y - bottom_y) - DIM_LABEL_MARGIN
    scale = min(avail_w / sheet.boardW, avail_h / sheet.boardL) if sheet.boardW and sheet.boardL else 1.0
    board_w_pts = sheet.boardW * scale
    board_h_pts = sheet.boardL * scale
    origin_x = x0 + DIM_LABEL_MARGIN
    origin_y = bottom_y

    _draw_dim_line_horizontal(canvas, origin_x, origin_x + board_w_pts, origin_y + board_h_pts + 10, f"{fmt_num(sheet.boardW)} mm")
    _draw_dim_line_vertical(canvas, origin_y, origin_y + board_h_pts, origin_x - 10, f"{fmt_num(sheet.boardL)} mm")

    for i, part in enumerate(sheet.placed):
        x = origin_x + part.x * scale
        y = origin_y + part.y * scale
        w = part.w * scale
        h = part.h * scale
        canvas.setFillColorRGB(*_color_for_index(i))
        canvas.setStrokeColorRGB(*PART_STROKE)
        canvas.setLineWidth(0.75)
        canvas.rect(x, y, w, h, fill=1, stroke=1)
        _draw_part_edge_dims(canvas, x, y, w, h, part.w, part.h)
        symbol = symbol_by_index[i]
        _draw_part_symbol_label(canvas, x, y, w, h, f"{symbol}.{part.name}")

    _draw_cut_lines(canvas, cuts, margin, origin_x, origin_y, scale)

    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(1.2)
    canvas.rect(origin_x, origin_y, board_w_pts, board_h_pts, fill=0, stroke=1)


def _sidebar_bottom_boxes(footer_y1: float, content_y1: float) -> tuple[float, float, float]:
    """Returns (grain_box_top, occurrence_box_bottom, occurrence_box_top) for the two boxes
    stacked directly above the footer strip: Grain Direction sits on [footer_y1, grain_box_top],
    Occurrences sits on [occurrence_box_bottom, occurrence_box_top]. The boxes' bottom edge
    must be footer_y1 (not the page/frame bottom) — they previously extended past the footer's
    top divider line into the footer strip itself."""
    grain_top = footer_y1 + BOTTOM_BOX_H
    occ_bottom = grain_top
    occ_top = occ_bottom + BOTTOM_BOX_H if occ_bottom + BOTTOM_BOX_H <= content_y1 else content_y1
    return grain_top, occ_bottom, occ_top


def _draw_footer(canvas: Canvas, x0: float, x1: float, y0: float, y1: float) -> None:
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.line(x0, y1, x1, y1)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(x0, (y0 + y1) / 2 - 3, "Generated using Nesting Pro")
    timestamp = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
    canvas.drawRightString(x1, (y0 + y1) / 2 - 3, timestamp)


def render_layout_pdf(
    result: OptResult, margin: Margin | None = None, output_path: str | None = None, show_cut_lines: bool = False,
    client_name_override: str = "", order_no_override: str = "",
) -> bytes:
    # margin defaults to zero rather than being required: only the cut-line overlay needs it
    # (saw jobs only — result.cuts is always empty for Nanxing, see optimizer/nanxing.py), and
    # existing callers/tests that only care about parts/board geometry shouldn't have to supply
    # a margin that doesn't affect anything else on the page.
    margin = margin if margin is not None else Margin(top=0, right=0, bottom=0, left=0)
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    frame_x0, frame_y0 = FRAME_MARGIN, FRAME_MARGIN
    frame_x1, frame_y1 = page_width - FRAME_MARGIN, page_height - FRAME_MARGIN
    sidebar_x1 = frame_x0 + (frame_x1 - frame_x0) * SIDEBAR_W_FRAC
    footer_y1 = frame_y0 + FOOTER_H
    content_y1 = frame_y1

    layouts = _deduplicate_layouts(result.sheets)
    layout_total = len(layouts)

    material_counts = Counter(s.material for s in result.sheets)
    job_panels = sum(len(s.placed) for s in result.sheets)
    job_cut_length = sum(c.length for c in result.cuts)
    board_area_total = sum(s.boardL * s.boardW for s in result.sheets)
    job_wastage = (
        100 - sum(s.utilizationPct * s.boardL * s.boardW for s in result.sheets) / board_area_total
        if board_area_total > 0 else 0.0
    )
    cuts_by_sheet: dict[int, list[CutInstruction]] = {}
    for c in result.cuts:
        cuts_by_sheet.setdefault(c.sheetIndex, []).append(c)

    for layout_index, (sheet, occurrence) in enumerate(layouts, start=1):
        canvas.setStrokeColorRGB(0, 0, 0)
        canvas.setLineWidth(0.75)
        canvas.rect(frame_x0, frame_y0, frame_x1 - frame_x0, frame_y1 - frame_y0, fill=0, stroke=1)
        canvas.line(sidebar_x1, footer_y1, sidebar_x1, frame_y1)

        _draw_sidebar_top(canvas, sheet, frame_x0, sidebar_x1, content_y1)
        cutting_rows, symbol_by_index = _cutting_list(sheet)
        grain_top, occ_bottom, occ_top = _sidebar_bottom_boxes(footer_y1, content_y1)
        _draw_cutting_list(canvas, cutting_rows, frame_x0, sidebar_x1, content_y1 - TOP_BLOCK_H, occ_bottom)
        _draw_occurrence_box(canvas, occurrence, frame_x0, sidebar_x1, occ_bottom, occ_top)
        sheet_grain = sheet.placed[0].grain if sheet.placed else "none"
        _draw_grain_box(canvas, sheet_grain, frame_x0, sidebar_x1, footer_y1, grain_top)

        job_stats = {
            "sheets_of_material": material_counts[sheet.material],
            "job_sheets": len(result.sheets),
            "job_panels": job_panels,
            "job_wastage": job_wastage,
            "sheet_cut_length": sum(c.length for c in cuts_by_sheet.get(sheet.index, [])),
            "job_cut_length": job_cut_length,
        }
        header_bottom = _draw_main_header(
            canvas, sheet, layout_index, layout_total, occurrence, job_stats,
            sidebar_x1 + PAD, frame_x1 - PAD, content_y1,
            client_name_override, order_no_override,
        )
        canvas.setStrokeColorRGB(0.6, 0.6, 0.6)
        canvas.setLineWidth(0.4)
        canvas.line(sidebar_x1 + PAD, header_bottom, frame_x1 - PAD, header_bottom)

        # show_cut_lines only suppresses the drawn overlay (Issues/issues_003.md: the dashed
        # lines confused panel-saw operators on some layouts) — the numeric "Cut Length" stats
        # above stay derived from the full cuts_by_sheet regardless, since those aren't the
        # confusing part.
        _draw_board_drawing(
            canvas, sheet, symbol_by_index, cuts_by_sheet.get(sheet.index, []) if show_cut_lines else [], margin,
            sidebar_x1 + PAD, frame_x1 - PAD, header_bottom - PAD, footer_y1 + PAD,
        )
        _draw_footer(canvas, frame_x0, frame_x1, frame_y0, footer_y1)
        canvas.showPage()

    canvas.save()
    content = buffer.getvalue()
    if output_path:
        with open(output_path, "wb") as f:
            f.write(content)
    return content
