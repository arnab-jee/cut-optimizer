from __future__ import annotations

from optimizer.parser import detect_schema, parse_csv_text, parse_grain

NESTING_HEADER = (
    "Project Name,Pos. barcode,Barcode,Part Name,Cutting Length,Cutting Width,Qty,"
    "Lenght,Width,Panel Thickness,Material Name,Face Material 1,Face Material 2,"
    "Core material,Edge 1,Edge 2,Edge 3,Edge 4,Grain\n"
)

SAW_HEADER = (
    "Mate,NAME,Pos.#,Barcode,Length,Width,Thickness,Length2,Width2,Thickness2,"
    "Top,Bottom,Edge 1,Edge 2,Edge 3,Edge 4,Qty\n"
)


def test_parses_saw_csv_with_no_errors(saw_parts):
    assert len(saw_parts) == 50


def test_parses_nesting_csv_with_no_errors(nesting_parts):
    assert len(nesting_parts) == 55


def test_detects_nesting_schema(nesting_csv_text):
    header = nesting_csv_text.splitlines()[0].split(",")
    assert detect_schema(header) == "nesting"


def test_detects_saw_schema(saw_csv_text):
    header = saw_csv_text.splitlines()[0].split(",")
    assert detect_schema(header) == "saw"


def test_quoted_space_edge_codes_normalize_to_empty(saw_parts):
    # 26Y111T1F1B3_1010's raw Edge 1-4 fields are the literal quoted-space marker `""" """`
    part = next(p for p in saw_parts if p.id == "26Y111T1F1B3_1010")
    assert part.edges == {"l1": "", "l2": "", "w1": "", "w2": ""}


def test_real_edge_codes_survive_normalization(nesting_parts):
    part = next(p for p in nesting_parts if p.id == "26Y111T1F1B3_1001")
    assert part.edges["l1"] == "RE_75633_PINE_NUT_2MM"


def test_grain_zero_maps_to_none(nesting_parts):
    assert all(p.grain == "none" for p in nesting_parts)


def test_parse_grain_mapping():
    assert parse_grain("0") == "none"
    assert parse_grain("1") == "length"
    assert parse_grain("2") == "width"
    assert parse_grain("x") == "length"
    assert parse_grain("y") == "width"
    assert parse_grain("") == "none"
    assert parse_grain("bogus") == "none"


def test_grain_1_and_2_lock_rotation_end_to_end():
    # Business Logic/grain_logic.md: raw Grain values are 0 (free), 1 (length parallel to
    # grain), 2 (length perpendicular to grain).
    # 1 and 2 must both disallow rotation (part.can_rotate() False), not silently fall back to
    # "none"/rotatable like an unrecognized code would.
    row_parallel = "Mr. Vijay,01.X,X,Part,100,200,1,100,200,17,MAT,,,,,,,,1\n"
    row_perp = "Mr. Vijay,01.Y,Y,Part,100,200,1,100,200,17,MAT,,,,,,,,2\n"
    parts, errors = parse_csv_text(NESTING_HEADER + row_parallel + row_perp)
    assert errors == []
    parallel_part = next(p for p in parts if p.id == "X")
    perp_part = next(p for p in parts if p.id == "Y")
    assert parallel_part.grain == "length"
    assert perp_part.grain == "width"
    assert parallel_part.can_rotate() is False
    assert perp_part.can_rotate() is False


def test_rejects_non_positive_dimensions():
    bad_row = "Mr. Vijay,01.X,X,Bad Part,0,100,1,0,100,17,MAT,,,,,,,,0\n"
    parts, errors = parse_csv_text(NESTING_HEADER + bad_row)
    assert parts == []
    assert errors
    assert "Row 2" in errors[0]


def test_saw_schema_takes_cutting_size_from_length2_width2():
    # Panel saw export: "Length"/"Width" hold the finished (F.S.) size, and
    # "Length2"/"Width2" hold the actual cutting size — the opposite pairing from the
    # nesting schema's "Cutting Length"/"Cutting Width" vs. "Lenght"/"Width" (see
    # conversation with the project owner, 2026-09-23). Values below are deliberately
    # distinct per column so a regression that reads the wrong pair is caught.
    row = "Mr. Vijay,Part,01.X,X,111,222,17,333,444,17,,,,,,,1\n"
    parts, errors = parse_csv_text(SAW_HEADER + row)
    assert errors == []
    part = parts[0]
    assert (part.cutLength, part.cutWidth) == (333, 444)
    assert (part.finishedLength, part.finishedWidth) == (111, 222)


def test_nesting_schema_cutting_and_finished_size_unaffected():
    row = "Mr. Vijay,01.X,X,Part,333,444,1,111,222,17,MAT,,,,,,,,0\n"
    parts, errors = parse_csv_text(NESTING_HEADER + row)
    assert errors == []
    part = parts[0]
    assert (part.cutLength, part.cutWidth) == (333, 444)
    assert (part.finishedLength, part.finishedWidth) == (111, 222)


def test_qty_expands_into_individual_placeable_units():
    row = "Mr. Vijay,01.X,X,Part,100,200,3,100,200,17,MAT,,,,,,,,0\n"
    parts, errors = parse_csv_text(NESTING_HEADER + row)
    assert errors == []
    assert len(parts) == 3
    assert {p.id for p in parts} == {"X_1", "X_2", "X_3"}
