from __future__ import annotations

from dataclasses import asdict

from fastapi.testclient import TestClient

import api

from .helpers import default_stock_for

client = TestClient(api.app)


def _request_body(parts, target="saw"):
    stock = default_stock_for(parts)
    return {
        "parts": [asdict(p) for p in parts],
        "stock": [asdict(s) for s in stock],
        "kerf": 4.0,
        "toolDiameter": 6.0,
        "partSpacing": 6.1,
        "margin": {"top": 0, "right": 10, "bottom": 10, "left": 5},
        "allowRotation": True,
        "target": target,
        "wasteStrategy": "balanced",
    }


def test_optimize_defaults_to_top_right_placement(saw_parts):
    # Locks in the current default (Issues/issues_005.md's corner/datum finding): a request
    # that omits placementCorner entirely must match an explicit "top-right", not the packer's
    # native "bottom-left" fill origin.
    body = _request_body(saw_parts)
    resp = client.post("/optimize", json=body)
    assert resp.status_code == 200
    without_corner = resp.json()

    body_explicit = {**body, "placementCorner": "top-right"}
    with_default_corner = client.post("/optimize", json=body_explicit).json()
    assert without_corner["sheets"] == with_default_corner["sheets"]


def test_optimize_placement_corner_changes_output(saw_parts):
    body = _request_body(saw_parts)
    default_result = client.post("/optimize", json={**body, "placementCorner": "bottom-left"}).json()
    mirrored_result = client.post("/optimize", json={**body, "placementCorner": "top-right"}).json()
    default_positions = [(p["x"], p["y"]) for s in default_result["sheets"] for p in s["placed"]]
    mirrored_positions = [(p["x"], p["y"]) for s in mirrored_result["sheets"] for p in s["placed"]]
    assert default_positions != mirrored_positions


def test_export_xml_accepts_placement_corner(nesting_parts):
    body = _request_body(nesting_parts, target="nanxing")
    resp = client.post("/export/xml", json={**body, "placementCorner": "top-right"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"<?xml")
