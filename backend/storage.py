from __future__ import annotations
import os
import sqlite3
from dataclasses import dataclass

# Updates/update_004.md, scoped down via discussion before implementing (see CLAUDE.md):
# SQLite, single-tenant, no auth/login yet — this pass only persists Stock Boards and a
# Waste Placement default. Auth/tenancy/machine-availability/schema-template renaming are
# explicitly deferred to a later pass.

_OLD_DB_PATH = os.path.join(os.path.dirname(__file__), "nesting_pro.db")
DB_PATH = os.environ.get(
    "CUTOPTIMIZER_DB_PATH",
    os.environ.get("NESTING_PRO_DB_PATH", os.path.join(os.path.dirname(__file__), "cutoptimizer.db")),
)

# App renamed nesting-pro -> CutOptimizer. A real local DB (stock boards, presets, settings)
# may already exist under the old default filename from before this rename — migrate it in
# place rather than silently starting a fresh, empty database under the new name.
if not os.path.exists(DB_PATH) and os.path.exists(_OLD_DB_PATH) and DB_PATH != _OLD_DB_PATH:
    os.rename(_OLD_DB_PATH, DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material TEXT NOT NULL,
    length REAL NOT NULL,
    width REAL NOT NULL,
    thickness REAL NOT NULL,
    grain TEXT NOT NULL DEFAULT 'none',
    cost REAL NOT NULL DEFAULT 0,
    cost_unit TEXT NOT NULL DEFAULT 'board',
    density REAL NOT NULL DEFAULT 0,
    quantity INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Phase 3 (ROADMAP.md): named margin/kerf/waste-strategy bundles, reusing the exact CRUD
-- pattern stock_boards already established rather than new architecture.
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target TEXT NOT NULL,
    margin_top REAL NOT NULL,
    margin_right REAL NOT NULL,
    margin_bottom REAL NOT NULL,
    margin_left REAL NOT NULL,
    kerf REAL NOT NULL,
    tool_diameter REAL NOT NULL,
    part_spacing REAL NOT NULL,
    allow_rotation INTEGER NOT NULL,
    waste_strategy TEXT NOT NULL
);

-- Label printing (QR code panel labels): named paper/size/toggle bundles, same flat-column CRUD
-- pattern as presets. page_type "sheet" (a fixed page tiled with a grid of labels, e.g. A4) or
-- "roll" (continuous roll paper -- each page IS one label, see optimizer/export/labels.py).
CREATE TABLE IF NOT EXISTS label_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    page_type TEXT NOT NULL,
    page_width REAL NOT NULL,
    page_height REAL NOT NULL,
    label_width REAL NOT NULL,
    label_height REAL NOT NULL,
    margin_top REAL NOT NULL,
    margin_right REAL NOT NULL,
    margin_bottom REAL NOT NULL,
    margin_left REAL NOT NULL,
    gap_x REAL NOT NULL,
    gap_y REAL NOT NULL,
    show_qr INTEGER NOT NULL,
    show_barcode INTEGER NOT NULL,
    show_corner_marks INTEGER NOT NULL
);
"""

WASTE_STRATEGY_DEFAULT_KEY = "wasteStrategyDefault"
DEFAULT_WASTE_STRATEGY = "balanced"
VALID_WASTE_STRATEGIES = ("balanced", "edge")
VALID_TARGETS = ("saw", "nanxing")
# ₹/board and ₹/sqft for now (currency is always ₹) — a plain, extensible tuple rather than an
# enum/Literal so a future unit (e.g. ₹/sqm) is a one-line addition here and in the frontend's
# matching CostUnit union, not a schema change.
VALID_COST_UNITS = ("board", "sqft")
DEFAULT_COST_UNIT = "board"

VALID_LABEL_PAGE_TYPES = ("sheet", "roll")
DEFAULT_LABEL_SETTINGS_ID_KEY = "defaultLabelSettingsId"
# Built-in fallback used until the user saves/picks a label-settings preset: A4 sheet, a 3x8 grid
# of 63.5x38.1mm labels -- a widely-used real label-sheet size (e.g. Avery 5160-class), 10mm page
# margins, 2.5mm gaps. QR on, barcode off (per the feature request: barcode is an option, not
# needed by default), corner marks on (matches the real reference label).
DEFAULT_LABEL_SETTINGS = {
    "pageType": "sheet",
    "pageWidth": 210.0,
    "pageHeight": 297.0,
    "labelWidth": 63.5,
    "labelHeight": 38.1,
    "marginTop": 10.0,
    "marginRight": 10.0,
    "marginBottom": 10.0,
    "marginLeft": 10.0,
    "gapX": 2.5,
    "gapY": 2.5,
    "showQrCode": True,
    "showBarcode": False,
    "showCornerMarks": True,
}


def _validate_preset_fields(target: str, waste_strategy: str) -> None:
    if target not in VALID_TARGETS:
        raise ValueError(f"invalid target: {target!r} (must be one of {VALID_TARGETS})")
    if waste_strategy not in VALID_WASTE_STRATEGIES:
        raise ValueError(f"invalid waste strategy: {waste_strategy!r} (must be one of {VALID_WASTE_STRATEGIES})")


def _validate_label_settings_fields(page_type: str) -> None:
    if page_type not in VALID_LABEL_PAGE_TYPES:
        raise ValueError(f"invalid page type: {page_type!r} (must be one of {VALID_LABEL_PAGE_TYPES})")


def _validate_cost_unit(cost_unit: str) -> None:
    if cost_unit not in VALID_COST_UNITS:
        raise ValueError(f"invalid cost unit: {cost_unit!r} (must be one of {VALID_COST_UNITS})")


def _migrate(conn: sqlite3.Connection) -> None:
    # A pre-existing local DB file (created before this pass) won't retroactively get columns
    # added to CREATE TABLE IF NOT EXISTS — SQLite doesn't rerun CREATE against an existing
    # table. Two starting states are possible for an existing file: no cost column at all (older
    # than the original Phase 3 pass), or the original single-currency `cost_per_board` column
    # (from that pass, before ₹/unit support) that needs renaming rather than re-adding.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stock_boards)")}
    if "cost" not in columns:
        if "cost_per_board" in columns:
            conn.execute("ALTER TABLE stock_boards RENAME COLUMN cost_per_board TO cost")
        else:
            conn.execute("ALTER TABLE stock_boards ADD COLUMN cost REAL NOT NULL DEFAULT 0")
        conn.commit()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stock_boards)")}
    if "cost_unit" not in columns:
        conn.execute(f"ALTER TABLE stock_boards ADD COLUMN cost_unit TEXT NOT NULL DEFAULT '{DEFAULT_COST_UNIT}'")
        conn.commit()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stock_boards)")}
    if "density" not in columns:
        conn.execute("ALTER TABLE stock_boards ADD COLUMN density REAL NOT NULL DEFAULT 0")
        conn.commit()
    if "quantity" not in columns:
        conn.execute("ALTER TABLE stock_boards ADD COLUMN quantity INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    # check_same_thread=False: api.py's get_db() is a sync generator dependency, and FastAPI runs
    # sync dependencies/endpoints via anyio's threadpool, which does not guarantee the generator
    # setup and the endpoint call land on the same OS worker thread — surfaced as a real,
    # intermittent `sqlite3.ProgrammingError` under Phase 3 verification (GET /presets, but any
    # endpoint using this connection is equally exposed). Safe here because each connection is
    # still only ever driven by one thread at a time in sequence (never concurrently) — it's
    # created fresh per request and closed at the end of that same request.
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


@dataclass
class PersistedStockBoard:
    id: int
    material: str
    length: float
    width: float
    thickness: float
    grain: str
    cost: float = 0.0
    costUnit: str = DEFAULT_COST_UNIT
    density: float = 0.0
    quantity: int = 0


def list_stock_boards(conn: sqlite3.Connection) -> list[PersistedStockBoard]:
    rows = conn.execute(
        "SELECT id, material, length, width, thickness, grain, cost, cost_unit AS costUnit, "
        "density, quantity FROM stock_boards ORDER BY id"
    ).fetchall()
    return [PersistedStockBoard(**dict(row)) for row in rows]


def create_stock_board(
    conn: sqlite3.Connection, material: str, length: float, width: float, thickness: float,
    grain: str = "none", cost: float = 0.0, cost_unit: str = DEFAULT_COST_UNIT,
    density: float = 0.0, quantity: int = 0,
) -> PersistedStockBoard:
    _validate_cost_unit(cost_unit)
    cur = conn.execute(
        "INSERT INTO stock_boards (material, length, width, thickness, grain, cost, cost_unit, density, quantity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (material, length, width, thickness, grain, cost, cost_unit, density, quantity),
    )
    conn.commit()
    return PersistedStockBoard(
        id=cur.lastrowid, material=material, length=length, width=width, thickness=thickness,
        grain=grain, cost=cost, costUnit=cost_unit, density=density, quantity=quantity,
    )


def update_stock_board(
    conn: sqlite3.Connection, board_id: int, material: str, length: float, width: float, thickness: float,
    grain: str, cost: float = 0.0, cost_unit: str = DEFAULT_COST_UNIT,
    density: float = 0.0, quantity: int = 0,
) -> PersistedStockBoard | None:
    _validate_cost_unit(cost_unit)
    cur = conn.execute(
        "UPDATE stock_boards SET material=?, length=?, width=?, thickness=?, grain=?, cost=?, cost_unit=?, "
        "density=?, quantity=? WHERE id=?",
        (material, length, width, thickness, grain, cost, cost_unit, density, quantity, board_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return PersistedStockBoard(
        id=board_id, material=material, length=length, width=width, thickness=thickness,
        grain=grain, cost=cost, costUnit=cost_unit, density=density, quantity=quantity,
    )


def delete_stock_board(conn: sqlite3.Connection, board_id: int) -> bool:
    cur = conn.execute("DELETE FROM stock_boards WHERE id=?", (board_id,))
    conn.commit()
    return cur.rowcount > 0


def get_waste_strategy_default(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (WASTE_STRATEGY_DEFAULT_KEY,)).fetchone()
    return row["value"] if row else DEFAULT_WASTE_STRATEGY


def set_waste_strategy_default(conn: sqlite3.Connection, value: str) -> str:
    if value not in VALID_WASTE_STRATEGIES:
        raise ValueError(f"invalid waste strategy: {value!r} (must be one of {VALID_WASTE_STRATEGIES})")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (WASTE_STRATEGY_DEFAULT_KEY, value),
    )
    conn.commit()
    return value


@dataclass
class PersistedPreset:
    id: int
    name: str
    target: str
    marginTop: float
    marginRight: float
    marginBottom: float
    marginLeft: float
    kerf: float
    toolDiameter: float
    partSpacing: float
    allowRotation: bool
    wasteStrategy: str


_PRESET_SELECT = (
    "SELECT id, name, target, margin_top AS marginTop, margin_right AS marginRight, "
    "margin_bottom AS marginBottom, margin_left AS marginLeft, kerf, "
    "tool_diameter AS toolDiameter, part_spacing AS partSpacing, "
    "allow_rotation AS allowRotation, waste_strategy AS wasteStrategy FROM presets"
)


def _row_to_preset(row: sqlite3.Row) -> PersistedPreset:
    data = dict(row)
    data["allowRotation"] = bool(data["allowRotation"])
    return PersistedPreset(**data)


def list_presets(conn: sqlite3.Connection) -> list[PersistedPreset]:
    rows = conn.execute(f"{_PRESET_SELECT} ORDER BY id").fetchall()
    return [_row_to_preset(row) for row in rows]


def create_preset(
    conn: sqlite3.Connection, name: str, target: str, margin_top: float, margin_right: float,
    margin_bottom: float, margin_left: float, kerf: float, tool_diameter: float, part_spacing: float,
    allow_rotation: bool, waste_strategy: str,
) -> PersistedPreset:
    _validate_preset_fields(target, waste_strategy)
    cur = conn.execute(
        "INSERT INTO presets (name, target, margin_top, margin_right, margin_bottom, margin_left, "
        "kerf, tool_diameter, part_spacing, allow_rotation, waste_strategy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, target, margin_top, margin_right, margin_bottom, margin_left, kerf, tool_diameter, part_spacing, int(allow_rotation), waste_strategy),
    )
    conn.commit()
    return PersistedPreset(
        id=cur.lastrowid, name=name, target=target, marginTop=margin_top, marginRight=margin_right,
        marginBottom=margin_bottom, marginLeft=margin_left, kerf=kerf, toolDiameter=tool_diameter,
        partSpacing=part_spacing, allowRotation=allow_rotation, wasteStrategy=waste_strategy,
    )


def update_preset(
    conn: sqlite3.Connection, preset_id: int, name: str, target: str, margin_top: float, margin_right: float,
    margin_bottom: float, margin_left: float, kerf: float, tool_diameter: float, part_spacing: float,
    allow_rotation: bool, waste_strategy: str,
) -> PersistedPreset | None:
    _validate_preset_fields(target, waste_strategy)
    cur = conn.execute(
        "UPDATE presets SET name=?, target=?, margin_top=?, margin_right=?, margin_bottom=?, margin_left=?, "
        "kerf=?, tool_diameter=?, part_spacing=?, allow_rotation=?, waste_strategy=? WHERE id=?",
        (name, target, margin_top, margin_right, margin_bottom, margin_left, kerf, tool_diameter, part_spacing, int(allow_rotation), waste_strategy, preset_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return PersistedPreset(
        id=preset_id, name=name, target=target, marginTop=margin_top, marginRight=margin_right,
        marginBottom=margin_bottom, marginLeft=margin_left, kerf=kerf, toolDiameter=tool_diameter,
        partSpacing=part_spacing, allowRotation=allow_rotation, wasteStrategy=waste_strategy,
    )


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> bool:
    cur = conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    conn.commit()
    return cur.rowcount > 0


@dataclass
class PersistedLabelSettings:
    id: int
    name: str
    pageType: str
    pageWidth: float
    pageHeight: float
    labelWidth: float
    labelHeight: float
    marginTop: float
    marginRight: float
    marginBottom: float
    marginLeft: float
    gapX: float
    gapY: float
    showQrCode: bool
    showBarcode: bool
    showCornerMarks: bool


_LABEL_SETTINGS_SELECT = (
    "SELECT id, name, page_type AS pageType, page_width AS pageWidth, page_height AS pageHeight, "
    "label_width AS labelWidth, label_height AS labelHeight, margin_top AS marginTop, "
    "margin_right AS marginRight, margin_bottom AS marginBottom, margin_left AS marginLeft, "
    "gap_x AS gapX, gap_y AS gapY, show_qr AS showQrCode, show_barcode AS showBarcode, "
    "show_corner_marks AS showCornerMarks FROM label_settings"
)


def _row_to_label_settings(row: sqlite3.Row) -> PersistedLabelSettings:
    data = dict(row)
    data["showQrCode"] = bool(data["showQrCode"])
    data["showBarcode"] = bool(data["showBarcode"])
    data["showCornerMarks"] = bool(data["showCornerMarks"])
    return PersistedLabelSettings(**data)


def list_label_settings(conn: sqlite3.Connection) -> list[PersistedLabelSettings]:
    rows = conn.execute(f"{_LABEL_SETTINGS_SELECT} ORDER BY id").fetchall()
    return [_row_to_label_settings(row) for row in rows]


def create_label_settings(
    conn: sqlite3.Connection, name: str, page_type: str, page_width: float, page_height: float,
    label_width: float, label_height: float, margin_top: float, margin_right: float, margin_bottom: float,
    margin_left: float, gap_x: float, gap_y: float, show_qr_code: bool, show_barcode: bool, show_corner_marks: bool,
) -> PersistedLabelSettings:
    _validate_label_settings_fields(page_type)
    cur = conn.execute(
        "INSERT INTO label_settings (name, page_type, page_width, page_height, label_width, label_height, "
        "margin_top, margin_right, margin_bottom, margin_left, gap_x, gap_y, show_qr, show_barcode, "
        "show_corner_marks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, page_type, page_width, page_height, label_width, label_height, margin_top, margin_right,
         margin_bottom, margin_left, gap_x, gap_y, int(show_qr_code), int(show_barcode), int(show_corner_marks)),
    )
    conn.commit()
    return PersistedLabelSettings(
        id=cur.lastrowid, name=name, pageType=page_type, pageWidth=page_width, pageHeight=page_height,
        labelWidth=label_width, labelHeight=label_height, marginTop=margin_top, marginRight=margin_right,
        marginBottom=margin_bottom, marginLeft=margin_left, gapX=gap_x, gapY=gap_y, showQrCode=show_qr_code,
        showBarcode=show_barcode, showCornerMarks=show_corner_marks,
    )


def update_label_settings(
    conn: sqlite3.Connection, settings_id: int, name: str, page_type: str, page_width: float, page_height: float,
    label_width: float, label_height: float, margin_top: float, margin_right: float, margin_bottom: float,
    margin_left: float, gap_x: float, gap_y: float, show_qr_code: bool, show_barcode: bool, show_corner_marks: bool,
) -> PersistedLabelSettings | None:
    _validate_label_settings_fields(page_type)
    cur = conn.execute(
        "UPDATE label_settings SET name=?, page_type=?, page_width=?, page_height=?, label_width=?, "
        "label_height=?, margin_top=?, margin_right=?, margin_bottom=?, margin_left=?, gap_x=?, gap_y=?, "
        "show_qr=?, show_barcode=?, show_corner_marks=? WHERE id=?",
        (name, page_type, page_width, page_height, label_width, label_height, margin_top, margin_right,
         margin_bottom, margin_left, gap_x, gap_y, int(show_qr_code), int(show_barcode), int(show_corner_marks),
         settings_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return PersistedLabelSettings(
        id=settings_id, name=name, pageType=page_type, pageWidth=page_width, pageHeight=page_height,
        labelWidth=label_width, labelHeight=label_height, marginTop=margin_top, marginRight=margin_right,
        marginBottom=margin_bottom, marginLeft=margin_left, gapX=gap_x, gapY=gap_y, showQrCode=show_qr_code,
        showBarcode=show_barcode, showCornerMarks=show_corner_marks,
    )


def delete_label_settings(conn: sqlite3.Connection, settings_id: int) -> bool:
    cur = conn.execute("DELETE FROM label_settings WHERE id=?", (settings_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    if deleted and get_default_label_settings_id(conn) == settings_id:
        # Don't leave the default pointer dangling at a row that no longer exists.
        conn.execute("DELETE FROM settings WHERE key=?", (DEFAULT_LABEL_SETTINGS_ID_KEY,))
        conn.commit()
    return deleted


def get_default_label_settings_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (DEFAULT_LABEL_SETTINGS_ID_KEY,)).fetchone()
    return int(row["value"]) if row else None


def set_default_label_settings_id(conn: sqlite3.Connection, settings_id: int | None) -> int | None:
    if settings_id is not None:
        exists = conn.execute("SELECT 1 FROM label_settings WHERE id=?", (settings_id,)).fetchone()
        if exists is None:
            raise ValueError(f"label settings {settings_id} not found")
    if settings_id is None:
        conn.execute("DELETE FROM settings WHERE key=?", (DEFAULT_LABEL_SETTINGS_ID_KEY,))
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (DEFAULT_LABEL_SETTINGS_ID_KEY, str(settings_id)),
        )
    conn.commit()
    return settings_id
