# To Dos

## Feature Add/Remove

- [ ] Add to `STOCK BOARD LIBRARY`, parameters:
  - `Density` or `Board Density` with selecteable units `kg/m^3`(default), `g/cm^3` and `lb/ft^3`. 
  - `Quantity` with unit `NOS` to the stock board library.

## Bug Fixes

- [x] Grain-free parts' "unrotated" pose was backwards vs. the real Fin China machine's own
  convention. Fixed in `nanxing_packing.py`'s `_footprint()`: `natural_swap = part.grain ==
  "length"` → `natural_swap = part.grain != "width"`, so grain-free parts now share
  grain="length"'s (correct) natural-pose baseline instead of grain="width"'s. Verified against
  the exact real parts in `results/26Y118_data/`: part `26Y118T1F1A1_1246` now matches Fin
  China's `RotateAngle`/`MachiningPoint` byte-for-byte (was geometry-only-coincidentally correct
  before); part `26Y118T1F1A1_1198` still doesn't match — traced and confirmed this is a
  legitimate fallback, not a bug: on the sheet it lands on, the only free rectangle it fits
  (1205×666.5mm) is ~60mm too short for the corrected/preferred orientation, so the existing
  preference logic (pass 22 — a preference, not a hard rule) correctly falls back to the other
  orientation rather than leaving it unplaced. Full backend suite (186 tests) green, no
  regressions; re-measured efficiency on 3 real jobs (`26Y118_data`, `nesting_machine_data.csv`,
  `26Y117T1F1B1(BEDROOM 3-4)`) — identical sheet counts and utilization before/after in all
  three. Test suite updated for the new convention (`test_packing_engines.py`). See `CLAUDE.md`
  pass 24 for the full trail. **Follow-up resolved in pass 25 — not with a hard rule** (measured
  and found it makes things worse: 26Y118 20→22 sheets), but with a time-budgeted search layer
  (`nanxing.py`'s `search_time_budget_s`, default 20s at the API layer) that tries many
  randomized placements and keeps whichever full result scores best. Real, safe, measured
  improvement (never worse than before): 26Y118 mismatches 13→5, BEDROOM 3-4 68→67 sheets +
  72→50 mismatches. **Pass 26 built the group-block placement primitive** this gap called
  for (a direct per-sheet cap on how many of a duplicate group may use the mismatched pose)
  — but real measurement found it doesn't clearly beat pass 25's cruder mechanism on its own,
  so both are now kept side by side (search picks whichever wins per job). Final result: close
  to pass 25's best numbers (26Y118 13→6, BEDROOM 3-4 68→67 sheets + 72→50), genuinely no
  worse than before on any job, but **still short of full Fin China parity** — that now looks
  like it needs jointly planning a sheet's whole part mix (a real cutting-stock-style solver),
  not just a smarter duplicate-group rule; tracked in `CLAUDE.md` "Remaining work" item 14,
  not started, unclear if worth the size of that undertaking yet. Full suite: 195 tests, all
  green, +8 new tests total for the search layer (passes 25+26 combined).
- [x] Physical machine label showed a blank "F.S." (Final Size) field. Fixed by adding
  `Workpiece.Info1`/`Info2` to the exported XML (source CSV's `Lenght`/`Width` columns) — that's
  what the machine actually reads for F.S., confirmed against real golden data. `CutLength`/
  `CutWidth` (actual cutting) untouched. See `CLAUDE.md` pass 21.
- [x] Physical label placeholder text sometimes printed next to the wrong (visually shorter)
  edge. **The actual root cause (pass 27), superseding everything above**: `xml.py` wrote raw,
  un-rotated `CutLength`/`CutWidth` regardless of the packer's rotation decision — but Fin
  China's own convention (confirmed against all 1039 real golden workpieces, zero exceptions,
  present since M5/M6, not new) redefines these attributes *per placement* to always match
  whichever axis each dimension actually landed on. Fixed by deriving `CutLength`/`CutWidth`/
  `shifted`/`FccOutline`/`BenchmarkInfo` from `_footprint(part, placed.rotated)` — the same
  function the packer itself uses — instead of the raw part values; `import_xml.py` got a
  matching fix so round-trip fidelity holds for the right reason. Verified: **100%
  self-consistent across all 3 benchmark jobs (849 workpieces)** — any orientation the packer
  picks is now correctly labeled. Full suite: 197 tests, all green (+2 new). See `CLAUDE.md`
  pass 27 for the full trail, including why the round-trip tests never caught this (same
  blind-spot pattern as M11).
- [x] Resolved in pass 28 — **but the open question above turned out to have a different
  answer than expected**: instead of simply removing the now-pointless search machinery, a
  real annotated-photo report from the project owner surfaced a genuinely different, more
  serious bug — the label *placeholder* itself (not the dimension text) landing inside a
  neighboring part's territory. Investigated thoroughly: ruled out every exported XML field
  (byte-identical to Fin China's real file for matching-orientation parts), and ruled out
  "any rotation, any software" (4 of 6 affected parts are also rotated in Fin China's own real
  file, without the bug there) — the exact machine-side mechanism remains genuinely
  unexplained. Given that, simplified `nanxing_packing.py`'s preference from a searched
  probability back to a **hard constraint** (natural pose required whenever it fits some empty
  board) as the practical mitigation — minimizing rotation reduces how often this can trigger,
  regardless of the exact cause. Removed the whole search apparatus (`nanxing.py`'s
  multi-trial wrapper, `group_caps`/`defer_probability`) — `/optimize`/`/export/pdf`/
  `/export/xml` are fast again (no 20s wait). **Verified it actually works**: rotation on
  grain-free parts dropped to 0 on 2 of 3 real benchmark jobs; the third's 16 remaining
  rotations confirmed geometrically unavoidable. Real, accepted cost, measured: 26Y118 20→22
  sheets, BEDROOM 3-4 68→70. Dimension-labeling fix (pass 27) re-verified 100% intact. Full
  suite: 189 tests, all green. **Found, not fixed**: a real `EdgeGroup` face-order bug for
  the "rotated AND shifted" combination (only 2 data points confirmed so far, needs more
  before implementing). **Still open**: the stray-placeholder bug's actual mechanism — the
  hard constraint is a mitigation, not a root-cause fix, and will still matter for the 16
  genuinely-unavoidable-rotation parts. See `CLAUDE.md` "Remaining work" items 15-17.


## UI/UX Improvements

- [x] Rename the parameter `Margin` to `Trim` for `Nanxing`.
- [x] Rename the parameter `Board Corner` to `Datum Point`.