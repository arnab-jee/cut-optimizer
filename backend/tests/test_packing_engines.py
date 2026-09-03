from __future__ import annotations

import pytest

import optimizer.guillotine as guillotine_module
import optimizer.nanxing as nanxing_module
from optimizer import nanxing_packing, saw_packing
from optimizer.guillotine import optimize as saw_optimize
from optimizer.model import Margin, Part, StockBoard
from optimizer.nanxing import optimize as nanxing_optimize

from .helpers import default_stock_for, is_guillotine_cuttable, overlapping_pairs

MODULES = [saw_packing, nanxing_packing]


def _part(cutLength: float, cutWidth: float, grain: str = "none", id: str = "P") -> Part:
    return Part(
        id=id, posId=id, name=id, cutLength=cutLength, cutWidth=cutWidth,
        finishedLength=cutLength, finishedWidth=cutWidth, thickness=18.0, qty=1,
        material="MAT", grain=grain, edges={"l1": "", "l2": "", "w1": "", "w2": ""},
    )


def test_packers_are_independent_modules():
    # Updates/update_003.md: "maintain separate packers" — guillotine.py and nanxing.py must
    # not import the same underlying module, so a future change to one can't silently affect
    # the other.
    assert saw_packing is not nanxing_packing
    assert saw_packing.__file__ != nanxing_packing.__file__
    assert guillotine_module.place_parts_on_board.__module__ == "optimizer.saw_packing"
    assert nanxing_module.place_parts_on_board.__module__ == "optimizer.nanxing_packing"


@pytest.mark.parametrize("mod", MODULES)
def test_merge_free_rects_combines_adjacent_rectangles(mod):
    # Two rects sharing a full vertical edge (same x/w, stacked in y) merge into one.
    a = mod.Rectangle(0, 0, 100, 50)
    b = mod.Rectangle(0, 50, 100, 30)
    merged = mod.merge_free_rects([a, b])
    assert len(merged) == 1
    assert merged[0] == mod.Rectangle(0, 0, 100, 80)


@pytest.mark.parametrize("mod", MODULES)
def test_merge_free_rects_leaves_non_adjacent_rects_alone(mod):
    a = mod.Rectangle(0, 0, 100, 50)
    b = mod.Rectangle(200, 200, 10, 10)
    merged = mod.merge_free_rects([a, b])
    assert len(merged) == 2


@pytest.mark.parametrize("mod", MODULES)
def test_edge_strategy_always_cuts_vertically(mod):
    # "edge" must ignore the shorter-leftover-axis rule and always keep the right-hand child
    # at the free rectangle's full height. Pick pw/ph so remain_w (10) < remain_h (50): under
    # "balanced" this is exactly the case that picks a horizontal cut (clipping the right
    # child's height to ph) — "edge" must still force a vertical cut here regardless.
    free = mod.Rectangle(0, 0, 200, 100)
    children_edge = mod.guillotine_split(free, pw=190, ph=50, waste_strategy="edge")
    right = next(c for c in children_edge if c.x == 190)
    assert right.h == 100  # full height retained, not clipped to ph=50

    children_balanced = mod.guillotine_split(free, pw=190, ph=50, waste_strategy="balanced")
    right_balanced = next(c for c in children_balanced if c.x == 190)
    assert right_balanced.h == 50  # balanced clips to the part's own height here


@pytest.mark.parametrize("mod", MODULES)
def test_waste_strategy_defaults_to_balanced(mod):
    free = mod.Rectangle(0, 0, 200, 100)
    default = mod.guillotine_split(free, pw=50, ph=90)
    balanced = mod.guillotine_split(free, pw=50, ph=90, waste_strategy="balanced")
    assert default == balanced


def test_edge_strategy_stays_guillotine_decomposable_and_drops_nothing(nesting_parts, default_margin):
    stock = default_stock_for(nesting_parts)
    result = nanxing_optimize(nesting_parts, stock, default_margin, spacing=6.0, waste_strategy="edge")
    assert result.unplaced == []
    for sheet in result.sheets:
        assert overlapping_pairs(sheet.placed) == 0
        assert is_guillotine_cuttable(sheet.placed)


def test_edge_strategy_stays_guillotine_decomposable_for_saw(saw_parts, default_margin):
    stock = default_stock_for(saw_parts)
    result = saw_optimize(saw_parts, stock, default_margin, kerf=4.0, allow_rotation=True, waste_strategy="edge")
    assert result.unplaced == []
    for sheet in result.sheets:
        assert overlapping_pairs(sheet.placed) == 0
        assert is_guillotine_cuttable(sheet.placed)


def test_edge_strategy_consolidation_on_a_real_job(nesting_parts, default_margin):
    # The real-world motivation (Updates/update_003.md's screenshot): "balanced" fragments
    # leftover space into many small offcuts scattered across a sheet; "edge" should collapse
    # more of that leftover area into one dominant offcut per sheet rather than many
    # similarly-sized scattered ones. Raw offcut *count* turned out not to reliably separate
    # the two strategies on this real job (many sheets are simple/near-full either way and
    # tie exactly); the largest-offcut share of total offcut area does.
    #
    # This used to assert edge > balanced outright (measured ~0.731 vs ~0.654 when M4 shipped
    # this). CLAUDE.md pass 28 changed that: the grain-free orientation preference became a
    # hard constraint (minimizing rotation to mitigate a real machine bug unrelated to waste
    # strategy — see nanxing_packing.py's own comment), which removes another placement degree
    # of freedom "edge" also depends on to consolidate well; measured on this exact job, that
    # interaction now makes edge *less* consolidated than balanced (0.612 vs 0.668) — a real,
    # accepted side effect of pass 28's tradeoff, not a regression in "edge" itself. The
    # mechanism `guillotine_split` uses to consolidate is still directly, deterministically
    # verified independent of any orientation policy by test_edge_strategy_always_cuts_
    # vertically above; this test now just locks in the current real measurement so a future
    # change that shifts it again gets noticed and re-evaluated deliberately, not silently.
    stock = default_stock_for(nesting_parts)
    balanced = nanxing_optimize(nesting_parts, stock, default_margin, spacing=6.0, waste_strategy="balanced")
    edge = nanxing_optimize(nesting_parts, stock, default_margin, spacing=6.0, waste_strategy="edge")

    def largest_offcut_fraction(result):
        total_area = sum(o.w * o.h for s in result.sheets for o in s.offcuts)
        if total_area <= 0:
            return 0.0
        largest_per_sheet = sum(max((o.w * o.h for o in s.offcuts), default=0.0) for s in result.sheets)
        return largest_per_sheet / total_area

    assert largest_offcut_fraction(balanced) == pytest.approx(0.668137, abs=1e-4)
    assert largest_offcut_fraction(edge) == pytest.approx(0.612350, abs=1e-4)


# --- Issues/issues_001.md: grain="length" parts were placed with cutLength forced onto the
# board's *width*-derived axis regardless of grain, silently rejecting any such part whose
# cutLength exceeded the board's width even when it fit easily along the board's length axis.
# Confirmed backwards against real golden Nanxing machine data (WorkpieceId
# 26Y117T1F1B1_1001, Grain="L", CutLength=1323.4, placed with an X-span of ~1329mm on a
# 2440mm-length board, RotateAngle absent) before fixing _footprint() in both packer modules.

@pytest.mark.parametrize("mod", MODULES)
def test_footprint_length_grain_natural_pose_runs_cutlength_on_local_y(mod):
    # local x is the board.width-derived axis, local y is board.length-derived (see
    # place_parts_on_board: width=board.width-margins, height=board.length-margins).
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="length")
    pw, ph = mod._footprint(part, rotated=False)
    assert (pw, ph) == (556.4, 1323.4)  # cutWidth on local x, cutLength on local y


@pytest.mark.parametrize("mod", MODULES)
def test_footprint_width_grain_natural_pose_unchanged(mod):
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="width")
    pw, ph = mod._footprint(part, rotated=False)
    assert (pw, ph) == (1323.4, 556.4)  # cutLength on local x, cutWidth on local y — unchanged


def test_footprint_none_grain_unaffected_on_saw():
    # saw_packing.py's grain="none" natural-pose convention is deliberately untouched by the
    # nanxing-only fix below — the panel saw's PDF labels are rendered by this app's own
    # _nominal_dims(), not a machine-side assumption, so it has no equivalent symptom to fix
    # (see CLAUDE.md pass 22/23-24).
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="none")
    assert saw_packing._footprint(part, rotated=False) == (1323.4, 556.4)
    assert saw_packing._footprint(part, rotated=True) == (556.4, 1323.4)


def test_footprint_none_grain_now_matches_length_grain_convention_on_nanxing():
    # Real production bug (CLAUDE.md pass 23/24, results/26Y118_data/): confirmed directly
    # against two real machine-cut XML exports of the *same* job (one from this app, one from
    # Fin China's own optimizer) that a grain="none" part's natural (rotated=False) pose must
    # run cutLength along the board's length axis, exactly like grain="length" — not grain=
    # "width"'s convention, which the old natural_swap baseline incorrectly shared.
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="none")
    assert nanxing_packing._footprint(part, rotated=False) == (556.4, 1323.4)
    assert nanxing_packing._footprint(part, rotated=True) == (1323.4, 556.4)


def test_length_grain_part_too_wide_for_board_width_now_places_on_saw():
    # The exact real part from Issues/issues_001.md: cutLength=1323.4 exceeds a 2440x1220
    # board's usable width axis (1205mm after 5+10mm margins) but fits its usable length axis
    # (2430mm). Previously rejected outright since grain="length" disallows rotation and the
    # only orientation ever tried put cutLength on the width axis.
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="length", id="X")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=0, right=10, bottom=10, left=5)
    result = saw_optimize([part], stock, margin, kerf=4.0, allow_rotation=True)
    assert result.unplaced == []
    assert len(result.sheets) == 1
    placed = result.sheets[0].placed[0]
    assert placed.rotated is False  # natural pose, not an actual 90-degree turn
    assert (placed.w, placed.h) == (556.4, 1323.4)


def test_length_grain_part_too_wide_for_board_width_now_places_on_nanxing():
    part = _part(cutLength=1323.4, cutWidth=556.4, grain="length", id="X")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=0, right=10, bottom=10, left=5)
    result = nanxing_optimize([part], stock, margin, spacing=6.0)
    assert result.unplaced == []
    assert len(result.sheets) == 1


def test_length_grain_part_still_rejected_if_it_exceeds_both_axes():
    # A part that's genuinely too big for the board in any orientation must still end up
    # unplaced — the fix only corrects which axis is tried, not whether the geometry check
    # itself is enforced.
    part = _part(cutLength=3000.0, cutWidth=556.4, grain="length", id="X")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=0, right=10, bottom=10, left=5)
    result = saw_optimize([part], stock, margin, kerf=4.0, allow_rotation=True)
    assert len(result.unplaced) == 1


# Reported directly (2026-09-02): real NaccNesting screenshots showed the machine's own
# on-screen dimension label printed next to the wrong (visually shorter) edge for a grain="none"
# part placed with CutLength running along the board's *width* axis — traced to the machine's
# label rendering apparently always assuming CutLength is horizontal (the board's length axis),
# regardless of a part's actual placement. Since we can't change NaccNesting's own rendering,
# nanxing_packing.py's place_parts_on_board now prefers whichever orientation keeps CutLength on
# the local-y (board.length-derived) axis when both orientations fit — matching that assumption
# — falling back to the tighter-packing orientation only when the preferred one fits nowhere.
# saw_packing.py is deliberately untouched: the panel saw's own PDF labels are computed by our
# own code (_nominal_dims()), not a machine-side assumption, so this has no equivalent there.
def test_nanxing_prefers_cutlength_on_length_axis_when_both_orientations_fit():
    # The exact real reported part: WorkpieceId 26Y118T1F1A1_1246, "Adjustable Shelf",
    # CutLength=1013.8 > CutWidth=528.8, grain="none". On an empty board both orientations fit
    # easily. Under the corrected natural_swap convention (CLAUDE.md pass 23/24), the *natural*,
    # unrotated pose is now the one that keeps CutLength on the board's length axis — matching
    # Fin China's own real machine-cut export of this exact part (RotateAngle absent i.e. "0",
    # MachiningPoint="1") byte-for-byte, not just geometrically.
    part = _part(cutLength=1013.8, cutWidth=528.8, grain="none", id="26Y118T1F1A1_1246")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=10, right=10, bottom=10, left=10)
    result = nanxing_optimize([part], stock, margin, spacing=6.0)
    assert result.unplaced == []
    placed = result.sheets[0].placed[0]
    assert placed.rotated is False  # natural pose, matching Fin China's RotateAngle="0"
    assert (placed.w, placed.h) == (528.8, 1013.8)  # cutLength (h) on local y / length axis


def test_nanxing_preference_falls_back_when_preferred_orientation_does_not_fit():
    # Precisely constructed discriminating case (board 2440x1220, margin 10 all round, so
    # usable width=1200/usable length=2420): under the corrected natural_swap convention, the
    # preferred (natural, rotated=False) pose needs local x = cutWidth = 1250 + gap, which does
    # NOT fit the 1200mm usable width — but the flipped (rotated=True) orientation (cutLength=900
    # on local x, cutWidth=1250 on local y) fits fine (900+gap <= 1200, 1250+gap <= 2420). The
    # part must still place — via the fallback — not end up unplaced just because its preferred
    # orientation doesn't fit.
    part = _part(cutLength=900.0, cutWidth=1250.0, grain="none", id="X")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=10, right=10, bottom=10, left=10)
    result = nanxing_optimize([part], stock, margin, spacing=6.0)
    assert result.unplaced == []
    placed = result.sheets[0].placed[0]
    assert placed.rotated is True  # fell back away from the (unfitting) natural pose
    assert (placed.w, placed.h) == (900.0, 1250.0)  # cutWidth (h) on the length axis


def test_nanxing_hard_preference_defers_to_a_fresh_sheet_rather_than_falling_back_on_this_one():
    # CLAUDE.md pass 28: the preference became a hard constraint whenever the natural pose
    # fits *some* empty board -- so unlike the softer pass-22/25/26 behavior, a part whose
    # natural pose doesn't fit the *current* sheet's leftover space no longer falls back to
    # squeeze in sideways there; it's deferred to a fresh sheet instead, where its natural pose
    # is guaranteed to fit (that guarantee is exactly why deferring is always safe).
    #
    # Board 2440x1220, no margin/gap for simple arithmetic. Part A (2000x1000, natural pose
    # unrotated) eats most of the width, leaving a 220x2000 strip and a 1220x440 strip. Part B
    # (900x300)'s natural pose needs 300 on the width axis -- doesn't fit either leftover
    # strip -- but its *fallback* pose (900 on width axis, 300 on length axis) fits the 1220x440
    # strip easily. Under the old soft preference, B would take that fallback opportunity and
    # both parts would fit on one sheet. Under the new hard constraint, B skips it and gets its
    # own fresh second sheet instead, still in its natural (unrotated) pose.
    part_a = _part(cutLength=2000.0, cutWidth=1000.0, grain="none", id="A")
    part_b = _part(cutLength=900.0, cutWidth=300.0, grain="none", id="B")
    stock = [StockBoard(material="MAT", length=2440, width=1220, thickness=18.0, grain="none")]
    margin = Margin(top=0, right=0, bottom=0, left=0)
    result = nanxing_optimize([part_a, part_b], stock, margin, spacing=0.0)
    assert result.unplaced == []
    assert len(result.sheets) == 2  # B did NOT squeeze onto sheet 1's leftover space
    sheet1_ids = {p.partId for p in result.sheets[0].placed}
    sheet2 = result.sheets[1]
    assert sheet1_ids == {"A"}
    assert len(sheet2.placed) == 1 and sheet2.placed[0].partId == "B"
    assert sheet2.placed[0].rotated is False  # placed in its natural pose on the fresh sheet
    assert (sheet2.placed[0].w, sheet2.placed[0].h) == (300.0, 900.0)
