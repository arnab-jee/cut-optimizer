from __future__ import annotations
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from ..model import OptResult, Part
from .pdf import _shrink_to_fit
from .xml import fmt_num

# Updates: "label printing with QR code" — one printed label per placed part instance (unplaced
# parts were never cut, so they get no label), following the real physical label
# sample_data/sample_labels/panel_qr_label.jpg. Field/edge-position mapping confirmed directly
# with the project owner (edge numbers e1=top, e2=left, e3=right, e4=bottom, matching
# parser.py's existing Edge 1..4 -> l1/l2/w1/w2 column mapping), not guessed from the photo alone.
LabelPageType = Literal["sheet", "roll"]

CONTENT_PAD = 1.2 * mm
BORDER_STRIP_W = 3.0 * mm
# Border strip scales down (rather than a hard on/off cutoff) so edge-band text still shows on
# the app's own default/quick-size labels -- 63.5x38.1mm (the built-in default) has a 38.1mm
# height, which a flat "skip below 40mm" threshold silently disabled entirely, even though real
# edge-band data was present. Only labels too small to hold any meaningful strip skip it.
BORDER_STRIP_FRACTION = 0.12
MIN_SIZE_FOR_BORDERS_MM = 15.0
QR_FRACTION = 0.32
CORNER_MARK_LEN = 2.5 * mm


@dataclass
class LabelSettings:
    pageType: LabelPageType
    pageWidth: float  # mm
    pageHeight: float  # mm
    labelWidth: float  # mm
    labelHeight: float  # mm
    marginTop: float = 10.0  # mm, "sheet" only
    marginRight: float = 10.0
    marginBottom: float = 10.0
    marginLeft: float = 10.0
    gapX: float = 2.5  # mm, "sheet" only
    gapY: float = 2.5
    showQrCode: bool = True
    showBarcode: bool = False
    showCornerMarks: bool = True


_LEADING_SYMBOL_PREFIX = re.compile(r"^\d+\.")


def _order_no(part: Part) -> str:
    """Recovers the shop's own order code from posId/id (confirmed directly with the project
    owner, not guessed): e.g. posId "01.26Y130T3F14B1_1006" or id "26Y130T3F14B1_1006" both
    reduce to order no "26Y130T3F14B1" -- strip posId's leading "NN." symbol prefix (id never has
    one), then take everything before the first "_" (the barcode's own sequence number, plus any
    "_N" copy suffix expand_quantity() appends for qty>1 rows, are both past that first "_")."""
    source = part.posId or part.id
    source = _LEADING_SYMBOL_PREFIX.sub("", source)
    return source.split("_", 1)[0]


def _client_order_line(part: Part, client_name_override: str = "", order_no_override: str = "") -> str:
    # Panel Saw CSVs have no Client/Project column at all (confirmed directly with the project
    # owner) -- overrides are job-level manual entries the user types in once when the CSV can't
    # supply this data, not something derivable from Part itself.
    client = part.customer or client_name_override
    order_no = _order_no(part) or order_no_override
    pieces = [p for p in (client, order_no) if p]
    return " | ".join(pieces)


def _grid_dims(settings: LabelSettings) -> tuple[int, int]:
    """How many label cells fit per page. Roll paper is always a single label per page (each
    physical label is its own cut/tear point on the roll) -- grid math only applies to sheet
    paper, where multiple labels tile across one fixed page like an A4 label sheet."""
    if settings.pageType == "roll":
        return 1, 1
    if settings.labelWidth <= 0 or settings.labelHeight <= 0:
        return 1, 1
    usable_w = settings.pageWidth - settings.marginLeft - settings.marginRight + settings.gapX
    usable_h = settings.pageHeight - settings.marginTop - settings.marginBottom + settings.gapY
    cols = max(1, int(usable_w // (settings.labelWidth + settings.gapX)))
    rows = max(1, int(usable_h // (settings.labelHeight + settings.gapY)))
    return cols, rows


def _draw_qr(canvas: Canvas, value: str, x: float, y: float, size: float) -> None:
    widget = qr.QrCodeWidget(value, barLevel="M")
    bx0, by0, bx1, by1 = widget.getBounds()
    bw, bh = bx1 - bx0, by1 - by0
    if bw <= 0 or bh <= 0:
        return
    scale = size / max(bw, bh)
    drawing = Drawing(size, size, transform=[scale, 0, 0, scale, -bx0 * scale, -by0 * scale])
    drawing.add(widget)
    renderPDF.draw(drawing, canvas, x, y)


def _draw_barcode(canvas: Canvas, value: str, x: float, y: float, w: float, h: float) -> None:
    barcode = code128.Code128(value, barHeight=h, barWidth=0.5)
    if barcode.width <= 0:
        return
    scale_x = min(1.0, w / barcode.width)
    canvas.saveState()
    canvas.translate(x, y)
    canvas.scale(scale_x, 1.0)
    barcode.drawOn(canvas, 0, 0)
    canvas.restoreState()


def _draw_corner_marks(canvas: Canvas, x: float, y: float, w: float, h: float) -> None:
    length = CORNER_MARK_LEN
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.6)
    for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        canvas.line(cx, cy, cx + dx * length, cy)
        canvas.line(cx, cy, cx, cy + dy * length)


def _draw_edge_text(canvas: Canvas, edges: dict, x: float, y: float, w: float, h: float, border: float) -> None:
    """Draws each edge-band code along the label border it belongs to. Physical position is by
    edge number (confirmed with the project owner): l1=top, l2=left (vertical), w1=right
    (vertical), w2=bottom -- matches parser.py's existing CSV "Edge 1..4" -> l1/l2/w1/w2 mapping.
    The "N." prefix each border is drawn with is a *different*, independently confirmed
    numbering: a plain reading-order sequence (top=1, left=2, bottom=3, right=4), not the
    source CSV column index -- so top(l1)="1.", left(l2)="2.", bottom(w2)="3.", right(w1)="4.",
    even though l1/l2/w1/w2 as *data* stay exactly where they always have."""
    canvas.setFillColorRGB(0, 0, 0)
    pad = 0.6 * mm
    max_size = border * 0.72

    top = edges.get("l1")
    if top:
        text = f"1. {top}"
        size = _shrink_to_fit(canvas, text, w - 2 * pad, max_size, min_size=3.0, font="Helvetica")
        canvas.setFont("Helvetica", size)
        canvas.drawCentredString(x + w / 2, y + h - border + (border - size) / 2, text)

    bottom = edges.get("w2")
    if bottom:
        text = f"3. {bottom}"
        size = _shrink_to_fit(canvas, text, w - 2 * pad, max_size, min_size=3.0, font="Helvetica")
        canvas.setFont("Helvetica", size)
        canvas.drawCentredString(x + w / 2, y + (border - size) / 2, text)

    left = edges.get("l2")
    if left:
        text = f"2. {left}"
        size = _shrink_to_fit(canvas, text, h - 2 * pad, max_size, min_size=3.0, font="Helvetica")
        canvas.saveState()
        canvas.translate(x + border / 2 + size * 0.35, y + h / 2)
        canvas.rotate(90)
        canvas.setFont("Helvetica", size)
        canvas.drawCentredString(0, 0, text)
        canvas.restoreState()

    right = edges.get("w1")
    if right:
        text = f"4. {right}"
        size = _shrink_to_fit(canvas, text, h - 2 * pad, max_size, min_size=3.0, font="Helvetica")
        canvas.saveState()
        canvas.translate(x + w - border / 2 + size * 0.35, y + h / 2)
        canvas.rotate(90)
        canvas.setFont("Helvetica", size)
        canvas.drawCentredString(0, 0, text)
        canvas.restoreState()


def _draw_label(
    canvas: Canvas, x: float, y: float, w: float, h: float, part: Part, settings: LabelSettings,
    client_name_override: str = "", order_no_override: str = "",
) -> None:
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.5)
    canvas.rect(x, y, w, h, fill=0, stroke=1)

    small = min(w, h) < MIN_SIZE_FOR_BORDERS_MM * mm
    border = 0.0 if small else min(BORDER_STRIP_W, min(w, h) * BORDER_STRIP_FRACTION)
    if not small:
        _draw_edge_text(canvas, part.edges, x, y, w, h, border)
        canvas.setStrokeColorRGB(0.6, 0.6, 0.6)
        canvas.setLineWidth(0.3)
        canvas.rect(x + border, y + border, w - 2 * border, h - 2 * border, fill=0, stroke=1)

    content_x0 = x + border + CONTENT_PAD
    content_y0 = y + border + CONTENT_PAD
    content_x1 = x + w - border - CONTENT_PAD
    content_y1 = y + h - border - CONTENT_PAD
    content_w = max(content_x1 - content_x0, 1.0)
    content_h = max(content_y1 - content_y0, 1.0)

    qr_size = 0.0
    if settings.showQrCode and content_w > 10 * mm and content_h > 10 * mm:
        qr_size = min(content_w * QR_FRACTION, content_h * 0.8)
        _draw_qr(canvas, part.id, content_x1 - qr_size, content_y1 - qr_size, qr_size)

    barcode_h = 0.0
    if settings.showBarcode and content_h > 14 * mm:
        barcode_h = min(6.0 * mm, content_h * 0.22)

    text_w = content_w - (qr_size + 1.5 * mm if qr_size else 0.0)
    text_top = content_y1
    text_bottom = content_y0 + (barcode_h + 1.0 * mm if barcode_h else 0.0)
    text_area_h = max(text_top - text_bottom, 1.0)

    lines = [
        (_client_order_line(part, client_name_override, order_no_override), "Helvetica", 7.0),
        (part.name or "", "Helvetica-Bold", 9.5),
        (part.posId or part.id, "Helvetica", 7.5),
        (f"C.S: {fmt_num(part.cutLength)} x {fmt_num(part.cutWidth)} x {fmt_num(part.thickness)}", "Helvetica", 7.5),
        (f"F.S: {fmt_num(part.finishedLength)} x {fmt_num(part.finishedWidth)}", "Helvetica", 7.5),
        (part.material or "", "Helvetica", 7.0),
    ]
    lines = [line for line in lines if line[0]] or [("", "Helvetica", 7.0)]
    row_h = text_area_h / len(lines)
    cursor_y = text_top
    canvas.setFillColorRGB(0, 0, 0)
    for text, font, base_size in lines:
        size = _shrink_to_fit(canvas, text, text_w, min(base_size, row_h * 0.8), min_size=3.5, font=font)
        canvas.setFont(font, size)
        canvas.drawString(content_x0, cursor_y - size, text)
        cursor_y -= row_h

    if barcode_h:
        _draw_barcode(canvas, part.id, content_x0, content_y0, content_w, barcode_h)

    if settings.showCornerMarks:
        _draw_corner_marks(canvas, x, y, w, h)


def render_labels_pdf(
    result: OptResult, parts_by_id: dict[str, Part], settings: LabelSettings,
    client_name_override: str = "", order_no_override: str = "",
) -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(settings.pageWidth * mm, settings.pageHeight * mm))
    cols, rows = _grid_dims(settings)
    per_page = cols * rows

    label_w_pt = settings.labelWidth * mm
    label_h_pt = settings.labelHeight * mm
    # Roll paper has no page margin/gap concept -- the page *is* the label, back-to-back on the
    # roll -- so margin/gap fields (which default to a nonzero sheet-oriented value) must not be
    # applied here, or the label gets pushed partly off the page.
    is_roll = settings.pageType == "roll"
    gap_x_pt = 0.0 if is_roll else settings.gapX * mm
    gap_y_pt = 0.0 if is_roll else settings.gapY * mm
    margin_left_pt = 0.0 if is_roll else settings.marginLeft * mm
    margin_top_pt = 0.0 if is_roll else settings.marginTop * mm
    page_h_pt = settings.pageHeight * mm

    index_on_page = 0
    any_drawn = False
    for sheet in result.sheets:
        for placed in sheet.placed:
            part = parts_by_id.get(placed.partId)
            if part is None:
                continue
            if index_on_page == per_page:
                canvas.showPage()
                index_on_page = 0
            col = index_on_page % cols
            row = index_on_page // cols
            label_x = margin_left_pt + col * (label_w_pt + gap_x_pt)
            label_y = page_h_pt - margin_top_pt - (row + 1) * label_h_pt - row * gap_y_pt
            _draw_label(canvas, label_x, label_y, label_w_pt, label_h_pt, part, settings, client_name_override, order_no_override)
            index_on_page += 1
            any_drawn = True

    if index_on_page > 0 or not any_drawn:
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
