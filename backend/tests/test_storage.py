from __future__ import annotations

import threading

import pytest

import storage


@pytest.fixture
def conn():
    connection = storage.get_connection(db_path=":memory:")
    yield connection
    connection.close()


def test_connection_usable_from_a_different_thread_than_it_was_created_on(tmp_path):
    # Real bug found during Phase 3 verification: api.py's get_db() is a sync generator
    # dependency, and FastAPI runs sync dependencies/endpoints via anyio's threadpool, which does
    # not guarantee the connection is created and used on the same OS worker thread — this
    # intermittently raised `sqlite3.ProgrammingError: SQLite objects created in a thread can
    # only be used in that same thread` on a real GET /presets call. Needs a real file (not
    # :memory:, which is only visible to the connection that created it) so a second thread's
    # query against the *same connection object* actually exercises cross-thread access.
    db_path = str(tmp_path / "thread_test.db")
    conn = storage.get_connection(db_path=db_path)
    errors: list[Exception] = []

    def query_from_other_thread():
        try:
            storage.list_stock_boards(conn)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=query_from_other_thread)
    thread.start()
    thread.join()
    conn.close()
    assert errors == [], f"cross-thread query raised: {errors}"


def test_list_stock_boards_starts_empty(conn):
    assert storage.list_stock_boards(conn) == []


def test_create_and_list_stock_board(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18, grain="none")
    assert board.id is not None
    listed = storage.list_stock_boards(conn)
    assert listed == [board]


def test_create_stock_board_defaults_grain_to_none(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18)
    assert board.grain == "none"


def test_update_stock_board(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18)
    updated = storage.update_stock_board(conn, board.id, material="MDF", length=2440, width=1220, thickness=25, grain="length")
    assert updated.thickness == 25
    assert updated.grain == "length"
    assert storage.list_stock_boards(conn) == [updated]


def test_update_nonexistent_stock_board_returns_none(conn):
    assert storage.update_stock_board(conn, 999, material="MDF", length=1, width=1, thickness=1, grain="none") is None


def test_delete_stock_board(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18)
    assert storage.delete_stock_board(conn, board.id) is True
    assert storage.list_stock_boards(conn) == []


def test_delete_nonexistent_stock_board_returns_false(conn):
    assert storage.delete_stock_board(conn, 999) is False


def test_waste_strategy_default_starts_balanced(conn):
    assert storage.get_waste_strategy_default(conn) == "balanced"


def test_set_and_get_waste_strategy_default(conn):
    assert storage.set_waste_strategy_default(conn, "edge") == "edge"
    assert storage.get_waste_strategy_default(conn) == "edge"


def test_set_waste_strategy_default_twice_overwrites(conn):
    storage.set_waste_strategy_default(conn, "edge")
    storage.set_waste_strategy_default(conn, "balanced")
    assert storage.get_waste_strategy_default(conn) == "balanced"


def test_set_invalid_waste_strategy_raises(conn):
    with pytest.raises(ValueError):
        storage.set_waste_strategy_default(conn, "not-a-real-strategy")


def test_create_stock_board_defaults_cost_to_zero_and_board_unit(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18)
    assert board.cost == 0.0
    assert board.costUnit == "board"


def test_create_and_update_stock_board_cost(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18, cost=45.5, cost_unit="sqft")
    assert board.cost == 45.5
    assert board.costUnit == "sqft"
    updated = storage.update_stock_board(conn, board.id, material="MDF", length=2440, width=1220, thickness=18, grain="none", cost=60.0, cost_unit="board")
    assert updated.cost == 60.0
    assert updated.costUnit == "board"
    assert storage.list_stock_boards(conn) == [updated]


def test_create_stock_board_invalid_cost_unit_raises(conn):
    with pytest.raises(ValueError):
        storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18, cost_unit="sqm")


def test_create_stock_board_defaults_density_and_quantity_to_zero(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18)
    assert board.density == 0.0
    assert board.quantity == 0


def test_create_and_update_stock_board_density_and_quantity(conn):
    board = storage.create_stock_board(conn, material="MDF", length=2440, width=1220, thickness=18, density=720.0, quantity=15)
    assert board.density == 720.0
    assert board.quantity == 15
    updated = storage.update_stock_board(
        conn, board.id, material="MDF", length=2440, width=1220, thickness=18, grain="none", density=680.0, quantity=8,
    )
    assert updated.density == 680.0
    assert updated.quantity == 8
    assert storage.list_stock_boards(conn) == [updated]


def test_list_presets_starts_empty(conn):
    assert storage.list_presets(conn) == []


def _make_preset(conn, name="Standard Panel Saw run"):
    return storage.create_preset(
        conn, name=name, target="saw", margin_top=0, margin_right=10, margin_bottom=10, margin_left=5,
        kerf=4.0, tool_diameter=6.0, part_spacing=6.1, allow_rotation=True, waste_strategy="balanced",
    )


def test_create_and_list_preset(conn):
    preset = _make_preset(conn)
    assert preset.id is not None
    assert preset.name == "Standard Panel Saw run"
    assert preset.marginRight == 10
    assert preset.allowRotation is True
    assert storage.list_presets(conn) == [preset]


def test_update_preset(conn):
    preset = _make_preset(conn)
    updated = storage.update_preset(
        conn, preset.id, name="Renamed", target="nanxing", margin_top=1, margin_right=2, margin_bottom=3,
        margin_left=4, kerf=5.0, tool_diameter=6.0, part_spacing=7.0, allow_rotation=False, waste_strategy="edge",
    )
    assert updated.name == "Renamed"
    assert updated.target == "nanxing"
    assert updated.allowRotation is False
    assert storage.list_presets(conn) == [updated]


def test_update_nonexistent_preset_returns_none(conn):
    assert storage.update_preset(
        conn, 999, name="X", target="saw", margin_top=0, margin_right=0, margin_bottom=0, margin_left=0,
        kerf=0, tool_diameter=0, part_spacing=0, allow_rotation=True, waste_strategy="balanced",
    ) is None


def test_delete_preset(conn):
    preset = _make_preset(conn)
    assert storage.delete_preset(conn, preset.id) is True
    assert storage.list_presets(conn) == []


def test_delete_nonexistent_preset_returns_false(conn):
    assert storage.delete_preset(conn, 999) is False


def test_create_preset_invalid_target_raises(conn):
    with pytest.raises(ValueError):
        storage.create_preset(
            conn, name="X", target="laser-cutter", margin_top=0, margin_right=0, margin_bottom=0, margin_left=0,
            kerf=0, tool_diameter=0, part_spacing=0, allow_rotation=True, waste_strategy="balanced",
        )


def test_create_preset_invalid_waste_strategy_raises(conn):
    with pytest.raises(ValueError):
        storage.create_preset(
            conn, name="X", target="saw", margin_top=0, margin_right=0, margin_bottom=0, margin_left=0,
            kerf=0, tool_diameter=0, part_spacing=0, allow_rotation=True, waste_strategy="not-a-real-strategy",
        )


def test_list_label_settings_starts_empty(conn):
    assert storage.list_label_settings(conn) == []


def _make_label_settings(conn, name="A4 sheet 3x8", page_type="sheet"):
    return storage.create_label_settings(
        conn, name=name, page_type=page_type, page_width=210.0, page_height=297.0, label_width=63.5,
        label_height=38.1, margin_top=10.0, margin_right=10.0, margin_bottom=10.0, margin_left=10.0,
        gap_x=2.5, gap_y=2.5, show_qr_code=True, show_barcode=False, show_corner_marks=True,
    )


def test_create_and_list_label_settings(conn):
    settings = _make_label_settings(conn)
    assert settings.id is not None
    assert settings.name == "A4 sheet 3x8"
    assert settings.pageType == "sheet"
    assert settings.showQrCode is True
    assert settings.showBarcode is False
    assert storage.list_label_settings(conn) == [settings]


def test_update_label_settings(conn):
    settings = _make_label_settings(conn)
    updated = storage.update_label_settings(
        conn, settings.id, name="Roll 70x40", page_type="roll", page_width=70.0, page_height=40.0,
        label_width=70.0, label_height=40.0, margin_top=0.0, margin_right=0.0, margin_bottom=0.0,
        margin_left=0.0, gap_x=0.0, gap_y=0.0, show_qr_code=True, show_barcode=True, show_corner_marks=False,
    )
    assert updated.name == "Roll 70x40"
    assert updated.pageType == "roll"
    assert updated.showBarcode is True
    assert storage.list_label_settings(conn) == [updated]


def test_update_nonexistent_label_settings_returns_none(conn):
    assert storage.update_label_settings(
        conn, 999, name="X", page_type="sheet", page_width=0, page_height=0, label_width=0, label_height=0,
        margin_top=0, margin_right=0, margin_bottom=0, margin_left=0, gap_x=0, gap_y=0,
        show_qr_code=True, show_barcode=False, show_corner_marks=True,
    ) is None


def test_delete_label_settings(conn):
    settings = _make_label_settings(conn)
    assert storage.delete_label_settings(conn, settings.id) is True
    assert storage.list_label_settings(conn) == []


def test_delete_nonexistent_label_settings_returns_false(conn):
    assert storage.delete_label_settings(conn, 999) is False


def test_create_label_settings_invalid_page_type_raises(conn):
    with pytest.raises(ValueError):
        _make_label_settings(conn, page_type="postcard")


def test_default_label_settings_id_starts_none(conn):
    assert storage.get_default_label_settings_id(conn) is None


def test_set_and_get_default_label_settings_id(conn):
    settings = _make_label_settings(conn)
    storage.set_default_label_settings_id(conn, settings.id)
    assert storage.get_default_label_settings_id(conn) == settings.id


def test_set_default_label_settings_id_nonexistent_raises(conn):
    with pytest.raises(ValueError):
        storage.set_default_label_settings_id(conn, 999)


def test_deleting_default_label_settings_clears_the_pointer(conn):
    settings = _make_label_settings(conn)
    storage.set_default_label_settings_id(conn, settings.id)
    storage.delete_label_settings(conn, settings.id)
    assert storage.get_default_label_settings_id(conn) is None
