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
  pass 24 for the full trail. **Open follow-up, not yet decided:** should the preference become
  a hard constraint (never fall back) to guarantee every part matches Fin China's label
  convention, at the cost of possibly more sheets / unplaced parts on tight jobs? Left as a
  preference for now, same as pass 22 shipped it — flagged in `CLAUDE.md` "Remaining work" for a
  decision.
- [x] Physical machine label showed a blank "F.S." (Final Size) field. Fixed by adding
  `Workpiece.Info1`/`Info2` to the exported XML (source CSV's `Lenght`/`Width` columns) — that's
  what the machine actually reads for F.S., confirmed against real golden data. `CutLength`/
  `CutWidth` (actual cutting) untouched. See `CLAUDE.md` pass 21.
- [x] Physical label placeholder text sometimes printed next to the wrong (visually shorter)
  edge. Root cause fixed above (grain-free natural-pose direction) — most real-world cases now
  match Fin China exactly; a minority of parts can still fall back to the mismatched-looking
  orientation when their preferred pose doesn't fit the available free space (see the "Open
  follow-up" note above for the hard-rule tradeoff still on the table).


## UI/UX Improvements

- [x] Rename the parameter `Margin` to `Trim` for `Nanxing`.
- [x] Rename the parameter `Board Corner` to `Datum Point`.