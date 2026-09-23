from __future__ import annotations
import csv
import re
from dataclasses import replace
from io import StringIO
from typing import Any

from .model import EdgeSet, Grain, Part

SCHEMA_COLUMNS = {
    "nesting": {
        "Project Name": "customer",
        "Pos. barcode": "posId",
        "Barcode": "id",
        "Part Name": "name",
        "Cutting Length": "cutLength",
        "Cutting Width": "cutWidth",
        "Qty": "qty",
        "Lenght": "finishedLength",
        "Length": "finishedLength",
        "Width": "finishedWidth",
        "Panel Thickness": "thickness",
        "Material Name": "material",
        "Face Material 1": "faceTop",
        "Face Material 2": "faceBottom",
        "Core material": "core",
        "Edge 1": "edges.l1",
        "Edge 2": "edges.l2",
        "Edge 3": "edges.w1",
        "Edge 4": "edges.w2",
        "Grain": "grain",
    },
    "saw": {
        "Mate": "material",
        "NAME": "name",
        "Pos.#": "posId",
        "Barcode": "id",
        "Length": "finishedLength",
        "Width": "finishedWidth",
        "Thickness": "thickness",
        "Length2": "cutLength",
        "Width2": "cutWidth",
        "Thickness2": "_unused",
        "Top": "faceTop",
        "Bottom": "faceBottom",
        "Edge 1": "edges.l1",
        "Edge 2": "edges.l2",
        "Edge 3": "edges.w1",
        "Edge 4": "edges.w2",
        "Qty": "qty",
    },
}

GRAIN_MAP = {
    # 0/1/2 are the raw CSV codes (Business Logic/grain_logic.md): 0 = no grain constraint,
    # 1 = part's length must be parallel to the grain direction ("length" grain), 2 = part's
    # length must be perpendicular to the grain direction, i.e. width parallel to grain
    # ("width" grain). 1/2 previously fell through to "none" via the .get() default below,
    # silently treating grain-locked parts as rotatable.
    "0": "none",
    "1": "length",
    "2": "width",
    "none": "none",
    "x": "length",
    "y": "width",
    "length": "length",
    "width": "width",
}

def clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if text == "\uFEFF":
        return ""
    return text

def parse_number(raw: str, default: float = 0.0) -> float:
    raw = clean_value(raw)
    raw = raw.replace(",", ".")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_int(raw: str, default: int = 0) -> int:
    raw = clean_value(raw)
    if raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def parse_grain(raw: str) -> Grain:
    raw = clean_value(raw).lower()
    raw = raw.strip('"')
    return GRAIN_MAP.get(raw, "none")


def normalize_edge_value(raw: str) -> str:
    value = clean_value(raw).strip()
    # some exports encode "no edge" as a literal quoted space, e.g. `""" """` -> '" "'
    if value.strip('"').strip() == "":
        return ""
    return value


def normalize_edges(row: dict[str, str]) -> EdgeSet:
    return {
        "l1": normalize_edge_value(row.get("Edge 1", "")),
        "l2": normalize_edge_value(row.get("Edge 2", "")),
        "w1": normalize_edge_value(row.get("Edge 3", "")),
        "w2": normalize_edge_value(row.get("Edge 4", "")),
    }


def detect_schema(headers: list[str]) -> str | None:
    keys = [h.strip().replace("\ufeff", "") for h in headers]
    nested = set(k.lower() for k in SCHEMA_COLUMNS["nesting"].keys())
    saw = set(k.lower() for k in SCHEMA_COLUMNS["saw"].keys())
    lower = set(h.lower() for h in keys)
    if nested <= lower:
        return "nesting"
    if saw <= lower:
        return "saw"
    if "cutting length" in lower and "grains" not in lower:
        return "nesting"
    if "mate" in lower and "length2" in lower:
        return "saw"
    return None


def build_part_from_row(row: dict[str, str], schema: str) -> Part:
    edges = normalize_edges(row)
    grain = parse_grain(row.get("Grain", ""))
    if schema == "saw":
        # Panel saw export: "Length"/"Width" are the finished (F.S.) size, and
        # "Length2"/"Width2" are the actual cutting size (see conversation with
        # the project owner, 2026-09-23) — the opposite pairing from the
        # nesting schema below.
        cut_length_raw = row.get("Length2", "0")
        cut_width_raw = row.get("Width2", "0")
        finished_length_raw = row.get("Length", "0")
        finished_width_raw = row.get("Width", "0")
    else:
        cut_length_raw = row.get("Cutting Length", row.get("Length", "0"))
        cut_width_raw = row.get("Cutting Width", row.get("Width", "0"))
        finished_length_raw = row.get("Lenght", row.get("Length2", row.get("Length", "0")))
        finished_width_raw = row.get("Width2", row.get("Width", row.get("Finished Width", "0")))
    return Part(
        id=clean_value(row.get("Barcode", row.get("id", ""))) or clean_value(row.get("Pos. barcode", "")),
        posId=clean_value(row.get("Pos. barcode", row.get("Pos.#", ""))) or "",
        name=clean_value(row.get("Part Name", row.get("NAME", ""))) or "",
        cutLength=parse_number(cut_length_raw),
        cutWidth=parse_number(cut_width_raw),
        finishedLength=parse_number(finished_length_raw),
        finishedWidth=parse_number(finished_width_raw),
        thickness=parse_number(row.get("Panel Thickness", row.get("Thickness", "0"))),
        qty=max(parse_int(row.get("Qty", "1")), 1),
        material=clean_value(row.get("Material Name", row.get("Mate", ""))) or "",
        grain=grain,
        edges=edges,
        faceTop=clean_value(row.get("Face Material 1", row.get("Top", ""))) or None,
        faceBottom=clean_value(row.get("Face Material 2", row.get("Bottom", ""))) or None,
        core=clean_value(row.get("Core material", "")) or None,
        customer=clean_value(row.get("Project Name", "")) or None,
    )


def expand_quantity(parts: list[Part]) -> list[Part]:
    expanded: list[Part] = []
    for part in parts:
        if part.qty <= 1:
            expanded.append(part)
            continue
        for i in range(1, part.qty + 1):
            copy_id = f"{part.id}_{i}"
            expanded.append(replace(part, id=copy_id, qty=1))
    return expanded


def parse_csv_text(text: str) -> tuple[list[Part], list[str]]:
    if not text:
        return [], ["Empty CSV text"]
    text = text.replace("\ufeff", "")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        return [], ["Missing header row"]
    schema = detect_schema(reader.fieldnames)
    if schema is None:
        return [], ["Unable to detect CSV schema, please provide a supported nesting or saw export."]
    errors: list[str] = []
    parts: list[Part] = []
    for index, raw_row in enumerate(reader, start=2):
        row = {k: clean_value(v) for k, v in raw_row.items() if k is not None}
        part = build_part_from_row(row, schema)
        if part.cutLength <= 0 or part.cutWidth <= 0 or part.thickness <= 0:
            errors.append(
                f"Row {index}: invalid dimensions length={part.cutLength}, width={part.cutWidth}, thickness={part.thickness}"
            )
            continue
        if not part.id:
            errors.append(f"Row {index}: missing Barcode or id")
            continue
        parts.append(part)
    if errors:
        return [], errors
    return expand_quantity(parts), []
