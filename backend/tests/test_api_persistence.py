from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api
import storage


@pytest.fixture
def client(tmp_path):
    # A real temp-file DB (not ":memory:") — api.get_db opens a fresh connection per request,
    # and an in-memory SQLite DB doesn't survive across separate connections, so a file is
    # required for a test that makes more than one HTTP call in sequence.
    db_path = str(tmp_path / "test.db")

    def override_get_db():
        conn = storage.get_connection(db_path=db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = override_get_db
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_list_stock_boards_starts_empty(client):
    resp = client.get("/stock-boards")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_list_update_delete_stock_board(client):
    created = client.post("/stock-boards", json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18})
    assert created.status_code == 200
    board = created.json()
    assert board["material"] == "MDF"
    assert board["grain"] == "none"

    listed = client.get("/stock-boards").json()
    assert listed == [board]

    updated = client.put(f"/stock-boards/{board['id']}", json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 25, "grain": "length"})
    assert updated.status_code == 200
    assert updated.json()["thickness"] == 25
    assert updated.json()["grain"] == "length"

    deleted = client.delete(f"/stock-boards/{board['id']}")
    assert deleted.status_code == 200
    assert client.get("/stock-boards").json() == []


def test_create_stock_board_missing_field_returns_400(client):
    resp = client.post("/stock-boards", json={"material": "MDF", "length": 2440})
    assert resp.status_code == 400


def test_update_nonexistent_stock_board_returns_404(client):
    resp = client.put("/stock-boards/999", json={"material": "MDF", "length": 1, "width": 1, "thickness": 1})
    assert resp.status_code == 404


def test_delete_nonexistent_stock_board_returns_404(client):
    resp = client.delete("/stock-boards/999")
    assert resp.status_code == 404


def test_settings_default_waste_strategy_is_balanced(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert resp.json() == {"wasteStrategyDefault": "balanced", "defaultLabelSettingsId": None}


def test_settings_update_and_persist_across_requests(client):
    put_resp = client.put("/settings", json={"wasteStrategyDefault": "edge"})
    assert put_resp.status_code == 200
    assert put_resp.json() == {"wasteStrategyDefault": "edge"}

    get_resp = client.get("/settings")
    assert get_resp.json() == {"wasteStrategyDefault": "edge", "defaultLabelSettingsId": None}


def test_settings_update_invalid_value_returns_400(client):
    resp = client.put("/settings", json={"wasteStrategyDefault": "not-a-real-strategy"})
    assert resp.status_code == 400


def test_create_stock_board_defaults_cost_to_zero_and_board_unit(client):
    created = client.post("/stock-boards", json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18})
    assert created.json()["cost"] == 0.0
    assert created.json()["costUnit"] == "board"


def test_create_and_update_stock_board_cost(client):
    created = client.post(
        "/stock-boards",
        json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18, "cost": 45.5, "costUnit": "sqft"},
    )
    board = created.json()
    assert board["cost"] == 45.5
    assert board["costUnit"] == "sqft"

    updated = client.put(
        f"/stock-boards/{board['id']}",
        json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18, "grain": "none", "cost": 60.0, "costUnit": "board"},
    )
    assert updated.json()["cost"] == 60.0
    assert updated.json()["costUnit"] == "board"


def test_create_stock_board_invalid_cost_unit_returns_400(client):
    resp = client.post(
        "/stock-boards",
        json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18, "costUnit": "sqm"},
    )
    assert resp.status_code == 400


def test_create_stock_board_defaults_density_and_quantity_to_zero(client):
    created = client.post("/stock-boards", json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18})
    assert created.json()["density"] == 0.0
    assert created.json()["quantity"] == 0


def test_create_and_update_stock_board_density_and_quantity(client):
    created = client.post(
        "/stock-boards",
        json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18, "density": 720.0, "quantity": 15},
    )
    board = created.json()
    assert board["density"] == 720.0
    assert board["quantity"] == 15

    updated = client.put(
        f"/stock-boards/{board['id']}",
        json={"material": "MDF", "length": 2440, "width": 1220, "thickness": 18, "grain": "none", "density": 680.0, "quantity": 8},
    )
    assert updated.json()["density"] == 680.0
    assert updated.json()["quantity"] == 8


def test_list_presets_starts_empty(client):
    resp = client.get("/presets")
    assert resp.status_code == 200
    assert resp.json() == []


PRESET_PAYLOAD = {
    "name": "Standard Panel Saw run",
    "target": "saw",
    "margin": {"top": 0, "right": 10, "bottom": 10, "left": 5},
    "kerf": 4.0,
    "toolDiameter": 6.0,
    "partSpacing": 6.1,
    "allowRotation": True,
    "wasteStrategy": "balanced",
}


def test_create_list_update_delete_preset(client):
    created = client.post("/presets", json=PRESET_PAYLOAD)
    assert created.status_code == 200
    preset = created.json()
    assert preset["name"] == "Standard Panel Saw run"
    assert preset["margin"] == {"top": 0, "right": 10, "bottom": 10, "left": 5}

    listed = client.get("/presets").json()
    assert listed == [preset]

    updated_payload = {**PRESET_PAYLOAD, "name": "Renamed", "wasteStrategy": "edge"}
    updated = client.put(f"/presets/{preset['id']}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["wasteStrategy"] == "edge"

    deleted = client.delete(f"/presets/{preset['id']}")
    assert deleted.status_code == 200
    assert client.get("/presets").json() == []


def test_create_preset_missing_field_returns_400(client):
    payload = {k: v for k, v in PRESET_PAYLOAD.items() if k != "name"}
    resp = client.post("/presets", json=payload)
    assert resp.status_code == 400


def test_create_preset_invalid_target_returns_400(client):
    resp = client.post("/presets", json={**PRESET_PAYLOAD, "target": "laser-cutter"})
    assert resp.status_code == 400


def test_update_nonexistent_preset_returns_404(client):
    resp = client.put("/presets/999", json=PRESET_PAYLOAD)
    assert resp.status_code == 404


def test_delete_nonexistent_preset_returns_404(client):
    resp = client.delete("/presets/999")
    assert resp.status_code == 404


def test_list_label_settings_starts_empty(client):
    resp = client.get("/label-settings")
    assert resp.status_code == 200
    assert resp.json() == []


LABEL_SETTINGS_PAYLOAD = {
    "name": "A4 sheet 3x8",
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


def test_create_list_update_delete_label_settings(client):
    created = client.post("/label-settings", json=LABEL_SETTINGS_PAYLOAD)
    assert created.status_code == 200
    settings = created.json()
    assert settings["name"] == "A4 sheet 3x8"
    assert settings["pageType"] == "sheet"

    listed = client.get("/label-settings").json()
    assert listed == [settings]

    updated_payload = {**LABEL_SETTINGS_PAYLOAD, "name": "Roll 70x40", "pageType": "roll", "showBarcode": True}
    updated = client.put(f"/label-settings/{settings['id']}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Roll 70x40"
    assert updated.json()["pageType"] == "roll"
    assert updated.json()["showBarcode"] is True

    deleted = client.delete(f"/label-settings/{settings['id']}")
    assert deleted.status_code == 200
    assert client.get("/label-settings").json() == []


def test_create_label_settings_missing_field_returns_400(client):
    payload = {k: v for k, v in LABEL_SETTINGS_PAYLOAD.items() if k != "name"}
    resp = client.post("/label-settings", json=payload)
    assert resp.status_code == 400


def test_create_label_settings_invalid_page_type_returns_400(client):
    resp = client.post("/label-settings", json={**LABEL_SETTINGS_PAYLOAD, "pageType": "postcard"})
    assert resp.status_code == 400


def test_update_nonexistent_label_settings_returns_404(client):
    resp = client.put("/label-settings/999", json=LABEL_SETTINGS_PAYLOAD)
    assert resp.status_code == 404


def test_delete_nonexistent_label_settings_returns_404(client):
    resp = client.delete("/label-settings/999")
    assert resp.status_code == 404


def test_settings_default_label_settings_id_round_trips(client):
    resp = client.get("/settings")
    assert resp.json()["defaultLabelSettingsId"] is None

    created = client.post("/label-settings", json=LABEL_SETTINGS_PAYLOAD).json()
    resp = client.put("/settings", json={"defaultLabelSettingsId": created["id"]})
    assert resp.status_code == 200
    assert resp.json()["defaultLabelSettingsId"] == created["id"]

    resp = client.get("/settings")
    assert resp.json()["defaultLabelSettingsId"] == created["id"]


def test_deleting_default_label_settings_clears_the_default_pointer(client):
    created = client.post("/label-settings", json=LABEL_SETTINGS_PAYLOAD).json()
    client.put("/settings", json={"defaultLabelSettingsId": created["id"]})

    client.delete(f"/label-settings/{created['id']}")

    resp = client.get("/settings")
    assert resp.json()["defaultLabelSettingsId"] is None
