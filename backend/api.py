from __future__ import annotations
from dataclasses import asdict
from typing import Iterator

import sqlite3

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

import storage
from optimizer.export.labels import LabelSettings, render_labels_pdf
from optimizer.export.pdf import render_layout_pdf
from optimizer.export.xml import generate_fcc_xml
from optimizer.guillotine import optimize as saw_optimize
from optimizer.import_xml import InvalidFccXmlError, parse_fcc_xml
from optimizer.model import Margin, OptRequest, OptResult, Part, StockBoard
from optimizer.nanxing import optimize as nanxing_optimize
from optimizer.parser import parse_csv_text

app = FastAPI(title="Nesting Pro Backend")

# The corner a request gets when it omits `placementCorner` entirely -- not the same thing as
# optimizer/placement.py's DEFAULT_PLACEMENT_CORNER ("bottom-left"), which is a structural fact
# about the packer's own native fill origin that mirror_sheet() relies on to know when *not* to
# mirror. This one is purely a UI/API preference and is free to change independently. Set to
# top-right per real-world confirmation: the factory's machine has its job-area datum fixed at
# top-right, and every job run from that corner (both CutOptimizer's and Fin China's own output)
# has come back within normal machining tolerance, while bottom-left was the corner behind the
# original ~6-7mm shortfall (Issues/issues_005.md).
UI_DEFAULT_PLACEMENT_CORNER = "top-right"

# Passes 25-26's time-budgeted search (a 20s multi-trial search in optimizer/nanxing.py,
# threaded through here) was removed in pass 28: it existed specifically to probabilistically
# avoid mismatched-looking dimension labels, a problem pass 27 eliminated entirely at the
# exporter level (CutLength/CutWidth now always follow whichever axis a part actually landed
# on, for any orientation). nanxing_packing.py's place_parts_on_board() now enforces the
# preferred (length-axis) orientation as a hard constraint instead -- deterministic, and fast
# again (no search budget to wait on) -- see that function's own comment for why a hard
# constraint is now warranted (a different, real bug: rotation itself, not any specific
# exported field, correlates with a physical machine's label *placeholder* landing in a
# neighboring part -- CLAUDE.md pass 28).


def get_db() -> Iterator[sqlite3.Connection]:
    conn = storage.get_connection()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/stock-boards")
def list_stock_boards(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return [asdict(b) for b in storage.list_stock_boards(db)]


@app.post("/stock-boards")
def create_stock_board(payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        board = storage.create_stock_board(
            db,
            material=payload["material"],
            length=float(payload["length"]),
            width=float(payload["width"]),
            thickness=float(payload["thickness"]),
            grain=payload.get("grain", "none"),
            cost=float(payload.get("cost", 0.0)),
            cost_unit=payload.get("costUnit", storage.DEFAULT_COST_UNIT),
            density=float(payload.get("density", 0.0)),
            quantity=int(payload.get("quantity", 0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    return asdict(board)


@app.put("/stock-boards/{board_id}")
def update_stock_board(board_id: int, payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        board = storage.update_stock_board(
            db,
            board_id,
            material=payload["material"],
            length=float(payload["length"]),
            width=float(payload["width"]),
            thickness=float(payload["thickness"]),
            grain=payload.get("grain", "none"),
            cost=float(payload.get("cost", 0.0)),
            cost_unit=payload.get("costUnit", storage.DEFAULT_COST_UNIT),
            density=float(payload.get("density", 0.0)),
            quantity=int(payload.get("quantity", 0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    if board is None:
        raise HTTPException(status_code=404, detail=[f"stock board {board_id} not found"])
    return asdict(board)


@app.delete("/stock-boards/{board_id}")
def delete_stock_board(board_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    if not storage.delete_stock_board(db, board_id):
        raise HTTPException(status_code=404, detail=[f"stock board {board_id} not found"])
    return {"deleted": True}


@app.get("/settings")
def get_settings(db: sqlite3.Connection = Depends(get_db)) -> dict:
    return {
        "wasteStrategyDefault": storage.get_waste_strategy_default(db),
        "defaultLabelSettingsId": storage.get_default_label_settings_id(db),
    }


@app.put("/settings")
def update_settings(payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    result = {}
    if "wasteStrategyDefault" in payload:
        try:
            result["wasteStrategyDefault"] = storage.set_waste_strategy_default(db, payload["wasteStrategyDefault"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=[str(exc)])
    if "defaultLabelSettingsId" in payload:
        try:
            result["defaultLabelSettingsId"] = storage.set_default_label_settings_id(db, payload["defaultLabelSettingsId"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=[str(exc)])
    return result


# Presets (Phase 3, ROADMAP.md): stored flat in SQLite (storage.py's PersistedPreset), but the
# JSON contract nests margin as {top,right,bottom,left} like everywhere else (OptRequest.margin,
# StockBoard, etc.) — these two helpers are the only place that reshapes between the two.
def _preset_to_dict(p: storage.PersistedPreset) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "target": p.target,
        "margin": {"top": p.marginTop, "right": p.marginRight, "bottom": p.marginBottom, "left": p.marginLeft},
        "kerf": p.kerf,
        "toolDiameter": p.toolDiameter,
        "partSpacing": p.partSpacing,
        "allowRotation": p.allowRotation,
        "wasteStrategy": p.wasteStrategy,
    }


def _preset_kwargs_from_payload(payload: dict) -> dict:
    margin = payload.get("margin", {})
    return {
        "name": payload["name"],
        "target": payload["target"],
        "margin_top": float(margin.get("top", 0.0)),
        "margin_right": float(margin.get("right", 0.0)),
        "margin_bottom": float(margin.get("bottom", 0.0)),
        "margin_left": float(margin.get("left", 0.0)),
        "kerf": float(payload.get("kerf", 0.0)),
        "tool_diameter": float(payload.get("toolDiameter", 0.0)),
        "part_spacing": float(payload.get("partSpacing", 0.0)),
        "allow_rotation": bool(payload.get("allowRotation", True)),
        "waste_strategy": payload.get("wasteStrategy", "balanced"),
    }


@app.get("/presets")
def list_presets(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return [_preset_to_dict(p) for p in storage.list_presets(db)]


@app.post("/presets")
def create_preset(payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        preset = storage.create_preset(db, **_preset_kwargs_from_payload(payload))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    return _preset_to_dict(preset)


@app.put("/presets/{preset_id}")
def update_preset(preset_id: int, payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        preset = storage.update_preset(db, preset_id, **_preset_kwargs_from_payload(payload))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    if preset is None:
        raise HTTPException(status_code=404, detail=[f"preset {preset_id} not found"])
    return _preset_to_dict(preset)


@app.delete("/presets/{preset_id}")
def delete_preset(preset_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    if not storage.delete_preset(db, preset_id):
        raise HTTPException(status_code=404, detail=[f"preset {preset_id} not found"])
    return {"deleted": True}


# Label printing: named page/label-size/toggle bundles, same flat-DB-column <-> nested-JSON
# reshape pattern as presets (see _preset_to_dict/_preset_kwargs_from_payload above) — here the
# JSON contract is already flat (no nested sub-object like `margin`), so the two helpers are
# thinner, but kept for the same reason: one place where the DB shape and the wire shape meet.
def _label_settings_to_dict(s: storage.PersistedLabelSettings) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "pageType": s.pageType,
        "pageWidth": s.pageWidth,
        "pageHeight": s.pageHeight,
        "labelWidth": s.labelWidth,
        "labelHeight": s.labelHeight,
        "marginTop": s.marginTop,
        "marginRight": s.marginRight,
        "marginBottom": s.marginBottom,
        "marginLeft": s.marginLeft,
        "gapX": s.gapX,
        "gapY": s.gapY,
        "showQrCode": s.showQrCode,
        "showBarcode": s.showBarcode,
        "showCornerMarks": s.showCornerMarks,
    }


def _label_settings_kwargs_from_payload(payload: dict) -> dict:
    return {
        "name": payload["name"],
        "page_type": payload["pageType"],
        "page_width": float(payload["pageWidth"]),
        "page_height": float(payload["pageHeight"]),
        "label_width": float(payload["labelWidth"]),
        "label_height": float(payload["labelHeight"]),
        "margin_top": float(payload.get("marginTop", 0.0)),
        "margin_right": float(payload.get("marginRight", 0.0)),
        "margin_bottom": float(payload.get("marginBottom", 0.0)),
        "margin_left": float(payload.get("marginLeft", 0.0)),
        "gap_x": float(payload.get("gapX", 0.0)),
        "gap_y": float(payload.get("gapY", 0.0)),
        "show_qr_code": bool(payload.get("showQrCode", True)),
        "show_barcode": bool(payload.get("showBarcode", False)),
        "show_corner_marks": bool(payload.get("showCornerMarks", True)),
    }


@app.get("/label-settings")
def list_label_settings(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return [_label_settings_to_dict(s) for s in storage.list_label_settings(db)]


@app.post("/label-settings")
def create_label_settings(payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        settings = storage.create_label_settings(db, **_label_settings_kwargs_from_payload(payload))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    return _label_settings_to_dict(settings)


@app.put("/label-settings/{settings_id}")
def update_label_settings(settings_id: int, payload: dict = Body(...), db: sqlite3.Connection = Depends(get_db)) -> dict:
    try:
        settings = storage.update_label_settings(db, settings_id, **_label_settings_kwargs_from_payload(payload))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    if settings is None:
        raise HTTPException(status_code=404, detail=[f"label settings {settings_id} not found"])
    return _label_settings_to_dict(settings)


@app.delete("/label-settings/{settings_id}")
def delete_label_settings(settings_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    if not storage.delete_label_settings(db, settings_id):
        raise HTTPException(status_code=404, detail=[f"label settings {settings_id} not found"])
    return {"deleted": True}

@app.post("/parse")
def parse_csv(csv_text: str = Body(..., embed=True)) -> dict:
    parts, errors = parse_csv_text(csv_text)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    return {"parts": [part.__dict__ for part in parts]}


def _stock_from_sheets(sheets) -> list[dict]:
    # Derives a job stock list from an imported file's sheets, the same (material, thickness)
    # dedup key deriveDefaultStock() uses client-side for a freshly-parsed CSV — grain defaults
    # to "none" since the file records grain per-*part*, not per-board, and a board's own grain
    # isn't otherwise recoverable from the export; cost fields default to 0 ("not entered"), same
    # as any other freshly-derived stock entry.
    seen: dict[tuple[str, float], dict] = {}
    for sheet in sheets:
        key = (sheet.material, sheet.thickness)
        if key not in seen:
            seen[key] = {"material": sheet.material, "length": sheet.boardL, "width": sheet.boardW, "thickness": sheet.thickness, "grain": "none"}
    return list(seen.values())


@app.post("/import/xml")
def import_xml(xml_text: str = Body(..., embed=True)) -> dict:
    # Updates/update_006.md: load an existing Nanxing FCC nesting XML (e.g. one produced by the
    # real machine's own software, or an earlier export from this app) and view it the same way
    # a fresh /optimize result is viewed — the file's own placement is authoritative, nothing is
    # re-nested. Response deliberately omits `parts`/`cuts`: this app doesn't offer re-export of
    # an imported job (see frontend App.tsx's isImported gating) since /export/* re-runs the
    # optimizer from parts+stock+params rather than re-serializing a given result, and doing that
    # against an imported job's data would silently produce a different layout than what was
    # actually loaded.
    try:
        job = parse_fcc_xml(xml_text)
    except InvalidFccXmlError as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
    return {
        "sheets": [
            {
                "index": sheet.index,
                "material": sheet.material,
                "boardL": sheet.boardL,
                "boardW": sheet.boardW,
                "thickness": sheet.thickness,
                "utilizationPct": sheet.utilizationPct,
                "placed": [p.__dict__ for p in sheet.placed],
                "offcuts": [o.__dict__ for o in sheet.offcuts],
            }
            for sheet in job.result.sheets
        ],
        "unplaced": [],
        "cuts": [],
        "margin": {"top": job.margin.top, "right": job.margin.right, "bottom": job.margin.bottom, "left": job.margin.left},
        "toolDiameter": job.tool_diameter,
        "partSpacing": job.part_spacing,
        "stock": _stock_from_sheets(job.result.sheets),
    }

@app.post("/optimize")
def optimize_route(request: dict = Body(...)) -> dict:
    try:
        margin = Margin(**request.get("margin", {}))
        stock = [StockBoard(**s) for s in request.get("stock", [])]
        parts = [Part(**part) for part in request.get("parts", [])]
        waste_strategy = request.get("wasteStrategy", "balanced")
        placement_corner = request.get("placementCorner", UI_DEFAULT_PLACEMENT_CORNER)
        if request.get("target") == "saw":
            result = saw_optimize(parts, stock, margin, kerf=request.get("kerf", 0.0), allow_rotation=request.get("allowRotation", True), waste_strategy=waste_strategy, placement_corner=placement_corner)
        else:
            result = nanxing_optimize(parts, stock, margin, spacing=request.get("partSpacing", request.get("toolDiameter", 6.0)), waste_strategy=waste_strategy, placement_corner=placement_corner, allow_rotation=request.get("allowRotation", True))
        return {
            "sheets": [
                {
                    "index": sheet.index,
                    "material": sheet.material,
                    "boardL": sheet.boardL,
                    "boardW": sheet.boardW,
                    "thickness": sheet.thickness,
                    "utilizationPct": sheet.utilizationPct,
                    "placed": [p.__dict__ for p in sheet.placed],
                    "offcuts": [o.__dict__ for o in sheet.offcuts],
                }
                for sheet in result.sheets
            ],
            "unplaced": [part.__dict__ for part in result.unplaced],
            "cuts": [cut.__dict__ for cut in result.cuts],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])

@app.post("/export/pdf")
def export_pdf(request: dict = Body(...)) -> Response:
    try:
        margin = Margin(**request.get("margin", {}))
        stock = [StockBoard(**s) for s in request.get("stock", [])]
        parts = [Part(**part) for part in request.get("parts", [])]
        waste_strategy = request.get("wasteStrategy", "balanced")
        placement_corner = request.get("placementCorner", UI_DEFAULT_PLACEMENT_CORNER)
        if request.get("target") == "saw":
            result = saw_optimize(parts, stock, margin, kerf=request.get("kerf", 0.0), allow_rotation=request.get("allowRotation", True), waste_strategy=waste_strategy, placement_corner=placement_corner)
        else:
            result = nanxing_optimize(parts, stock, margin, spacing=request.get("partSpacing", request.get("toolDiameter", 6.0)), waste_strategy=waste_strategy, placement_corner=placement_corner, allow_rotation=request.get("allowRotation", True))
        show_cut_lines = request.get("showCutLines", False)
        pdf_data = render_layout_pdf(result, margin, show_cut_lines=show_cut_lines)
        return Response(content=pdf_data, media_type="application/pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])

@app.post("/export/xml")
def export_xml(request: dict = Body(...)) -> Response:
    try:
        margin = Margin(**request.get("margin", {}))
        stock = [StockBoard(**s) for s in request.get("stock", [])]
        parts = [Part(**part) for part in request.get("parts", [])]
        placement_corner = request.get("placementCorner", UI_DEFAULT_PLACEMENT_CORNER)
        result = nanxing_optimize(parts, stock, margin, spacing=request.get("partSpacing", request.get("toolDiameter", 6.0)), waste_strategy=request.get("wasteStrategy", "balanced"), placement_corner=placement_corner, allow_rotation=request.get("allowRotation", True))
        parts_by_id = {part.id: part for part in parts}
        xml_data = generate_fcc_xml(
            result,
            parts_by_id,
            margin,
            tool_diameter=request.get("toolDiameter", 6.0),
            part_spacing=request.get("partSpacing", 6.0),
        )
        return Response(content=xml_data, media_type="application/xml")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])


def _label_settings_from_payload(payload: dict) -> LabelSettings:
    # Falls back to storage.DEFAULT_LABEL_SETTINGS (same built-in A4-sheet default the frontend
    # starts with before any settings are saved) for any field the caller omits, rather than
    # requiring the full object on every /export/labels call.
    defaults = storage.DEFAULT_LABEL_SETTINGS
    return LabelSettings(
        pageType=payload.get("pageType", defaults["pageType"]),
        pageWidth=float(payload.get("pageWidth", defaults["pageWidth"])),
        pageHeight=float(payload.get("pageHeight", defaults["pageHeight"])),
        labelWidth=float(payload.get("labelWidth", defaults["labelWidth"])),
        labelHeight=float(payload.get("labelHeight", defaults["labelHeight"])),
        marginTop=float(payload.get("marginTop", defaults["marginTop"])),
        marginRight=float(payload.get("marginRight", defaults["marginRight"])),
        marginBottom=float(payload.get("marginBottom", defaults["marginBottom"])),
        marginLeft=float(payload.get("marginLeft", defaults["marginLeft"])),
        gapX=float(payload.get("gapX", defaults["gapX"])),
        gapY=float(payload.get("gapY", defaults["gapY"])),
        showQrCode=bool(payload.get("showQrCode", defaults["showQrCode"])),
        showBarcode=bool(payload.get("showBarcode", defaults["showBarcode"])),
        showCornerMarks=bool(payload.get("showCornerMarks", defaults["showCornerMarks"])),
    )


@app.post("/export/labels")
def export_labels(request: dict = Body(...)) -> Response:
    try:
        margin = Margin(**request.get("margin", {}))
        stock = [StockBoard(**s) for s in request.get("stock", [])]
        parts = [Part(**part) for part in request.get("parts", [])]
        waste_strategy = request.get("wasteStrategy", "balanced")
        placement_corner = request.get("placementCorner", UI_DEFAULT_PLACEMENT_CORNER)
        if request.get("target") == "saw":
            result = saw_optimize(parts, stock, margin, kerf=request.get("kerf", 0.0), allow_rotation=request.get("allowRotation", True), waste_strategy=waste_strategy, placement_corner=placement_corner)
        else:
            result = nanxing_optimize(parts, stock, margin, spacing=request.get("partSpacing", request.get("toolDiameter", 6.0)), waste_strategy=waste_strategy, placement_corner=placement_corner, allow_rotation=request.get("allowRotation", True))
        parts_by_id = {part.id: part for part in parts}
        label_settings = _label_settings_from_payload(request.get("labelSettings", {}))
        pdf_data = render_labels_pdf(
            result, parts_by_id, label_settings,
            client_name_override=request.get("clientNameOverride", ""),
            order_no_override=request.get("orderNoOverride", ""),
        )
        return Response(content=pdf_data, media_type="application/pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=[str(exc)])
