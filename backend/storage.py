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


def _validate_preset_fields(target: str, waste_strategy: str) -> None:
    if target not in VALID_TARGETS:
        raise ValueError(f"invalid target: {target!r} (must be one of {VALID_TARGETS})")
    if waste_strategy not in VALID_WASTE_STRATEGIES:
        raise ValueError(f"invalid waste strategy: {waste_strategy!r} (must be one of {VALID_WASTE_STRATEGIES})")


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
