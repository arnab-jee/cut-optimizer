# nesting-pro — Post-M11 Development Roadmap

> Proposed staged plan for the improvements discussed after M11 (FCC XML axis-inversion fix).
> Phases 1–3 are done (2026-08-14, 2026-08-17 ×2); Phases 4–5 not yet started. For what's
> actually been built backend-side, see `CLAUDE.md`'s milestone table (M1–M11). Phases here are
> sequenced by dependency and effort, not strictly by importance — see the note at the bottom on
> reordering around business priority.

---

## Phase 1 — Frontend-only quick wins ✅ done

Originally scoped as frontend-only; the cut-line overlay item below ended up needing one small,
isolated backend change (see that item) once "show the cut sequence" became "draw it visibly on
the board," but stayed low-risk and additive — no other item in this phase touches the backend.

- ✅ **Cut-sequence view.** `OptResult.cuts` (the ordered straight-cut list per sheet, built in
  `optimizer/guillotine.py` since M2) already comes back from `/optimize` but is never rendered
  anywhere in the UI or PDF. Highest value-to-effort item on the whole roadmap — it's "build UI
  for data that already exists," not new computation. **Built as `CutList.tsx`**, a per-sheet
  disclosure inside `SheetPreview.tsx` — listed in API order (vertical cuts by ascending offset,
  then horizontal), not re-derived or reordered, since the data has no explicit sequence field
  beyond that.
- ✅ **Cut-line overlay** (added after Phase 1 was first marked done, per explicit request —
  cut-lines needed to be *visible*, not just listed as numbers in a table). **This is the one
  item in Phase 1 that isn't frontend-only**: the same overlay was also requested for the Panel
  Saw PDF drawing, so `optimizer/export/pdf.py` gained a `margin` parameter on
  `render_layout_pdf` (previously absent — `CutInstruction.offset` already bakes in
  `margin.left`/`.top`, but `.length` is only a span, so the line's start along the other axis
  has nowhere else to come from) and a `_draw_cut_lines` helper that draws each cut as a red
  dashed line using the exact same `scale`/`origin_x`/`origin_y` transform already used for
  parts. `margin` defaults to zero so pre-existing callers/tests didn't need updating.
  Frontend-side, `SheetPreview.tsx` renders the same lines as SVG `<line>` elements computed by
  a `cutLineBounds()` helper that mirrors the backend's `_cut_line_bounds()` exactly (same gap,
  same fix). `CutList.tsx` became a **controlled** component (`open`/`onToggle` props instead of
  a plain uncontrolled `<details>`) so the SVG overlay's visibility ties to the same disclosure
  toggle — expanding the cut list also draws the lines on the board preview, collapsing it hides
  them again, avoiding a second separate toggle. Nanxing PDFs/previews are naturally unaffected:
  `optimizer/nanxing.py` never populates `OptResult.cuts` (confirmed by reading it directly), so
  the overlay code paths simply have nothing to draw there.
  Covered by `backend/tests/test_pdf.py` (+4 tests: `_cut_line_bounds()` unit tests for both
  orientations, an integration test running a real saw job's cuts through `render_layout_pdf`
  end to end, and a no-margin-argument backward-compatibility test) — verified the two unit
  tests have teeth by temporarily reverting `_cut_line_bounds()` to ignore margin, reran, both
  failed as expected, restored. Full suite: 109→113 passing.
  Verified in a real headless browser (`panel_saw_machine_data.csv`, 21 sheets): cut lines are
  hidden by default, expanding Sheet 1's cut list (17 cuts) renders exactly 17 `<line>` elements
  matching the table row count 1:1, collapsing hides them again, zero console errors. Also
  visually confirmed on the actual rendered PDF (rasterized via `pdftoppm` and inspected) — the
  red dashed lines run exactly along the real guillotine cut boundaries between the colored
  panels, not offset or crossing through them.
  **Follow-on, same phase (`Issues/issues_003.md`):** the drawn overlay itself confused panel-saw
  operators on some real layouts (dense sheets with many small cuts made the dashed grid hard to
  read against the part boundaries), so it needed to be a toggle rather than always-on. Added
  **"Show cut lines"** as a checkbox in the Saw section of `ParamsPanel.tsx` (default flipped to
  **off** shortly after, see below), wired through `OptRequest.showCutLines`. Suppresses only the
  drawn overlay, not the underlying data: the numbered cut list and the PDF's "Cut Length" stats
  stay available either way — only the SVG `<line>`/PDF dashed-line rendering is gated. Backend:
  `render_layout_pdf` gained a `show_cut_lines` parameter that swaps in an empty cuts list for
  the board drawing only when off; `api.py`'s `/export/pdf` reads `showCutLines` from the request
  body. Frontend: `SheetPreview.tsx`'s line rendering is now gated on `showCutLines && expanded`
  (both the global setting and the per-sheet disclosure), while the `CutList` table's own
  open/close state stays independent of the global setting.
  Covered by 2 new backend tests (115 total): one asserts the PDF content stream's dash-pattern
  operator (`[3 2] 0 d`, `_draw_cut_lines`'s literal ReportLab output) is present when the
  setting is on and absent when off — a precise signal that lines were actually drawn, not just
  that the page didn't crash — and one confirms the "Sheet Cut Length" stat text still appears
  even with the setting off. Verified the dash-pattern test has teeth: temporarily removed the
  gate, reran, it failed as expected, restored. Verified end-to-end in a real headless browser:
  checkbox defaults to checked, unchecking it and rerunning produces a job with zero `.cut-line`
  elements even with the sheet's cut list expanded, and the downloaded PDF with the setting off
  was rasterized and visually confirmed to have a clean board diagram (no dashed overlay) while
  the sidebar's Cutting List and Cut Length stats are still present. `tsc -b`/lint/build clean.
  **Default flipped to off, same phase:** asked directly to make "Show cut lines" default to
  false. Changed in three places for consistency: `App.tsx`'s initial `useState`, `api.py`'s
  fallback when the request omits the field, and `render_layout_pdf`'s own parameter default in
  `optimizer/export/pdf.py` — all three previously defaulted `True`, now all default `False`, so
  no layer silently disagrees with the others. One existing test relied on the old implicit
  default to exercise the overlay-drawing path; updated to pass `show_cut_lines=True` explicitly
  so it still tests what it's named for. Added a new test asserting the no-argument call is
  byte-for-byte identical to an explicit `show_cut_lines=False` call — verified with teeth
  (reverted the default, reran, failed as expected, restored). Suite: 115→116. Verified in a real
  headless browser: checkbox now loads unchecked, a freshly run job's cut list expands with zero
  `.cut-line` elements drawn, and PDF download still works normally at the new default.
- ✅ **Loading/progress state** for the `/optimize` call on large jobs (the reported 656-part job
  is a good real test case). **Built**: a CSS spinner on the "Run optimize" button plus a
  "Nesting N parts…" status line while the request is in flight; the "Back" button is now also
  disabled during the request (wasn't before).
- ✅ **Table sort/filter/search** on the unplaced-parts table and the Stock Board Library table —
  both are plain unsorted tables today, fine at small scale, not at real job sizes. **Built** as
  a shared `useTableControls` hook (search text + click-to-sort, both tables reuse it — two real
  consumers, not a hypothetical one) and a shared `SortableTh` component for the clickable
  sortable header cells.

Verified in a real headless browser against the real 656-part reported job: sort-by-material
correctly reorders a Stock Board Library with two added boards, search correctly filters to
matching rows, the spinner appears during the optimize request, and a sheet's cut list expands
to show real numbered rows (6–11 depending on the sheet). Zero console errors. `tsc -b`/lint/
build all clean. The cut-line overlay (added after the rest of this phase) was separately
verified against `panel_saw_machine_data.csv` — see that item for the specifics.

## Phase 2 — Data visualization ✅ done

Still frontend-only — every number these need is already in the `/optimize` response — but more
design work than Phase 1, hence its own phase. No new charting dependency was added — the
project already renders `SheetPreview.tsx` with raw SVG, so all three items here follow that same
lightweight-custom-SVG pattern instead of pulling in a chart library for what turned out to be
simple bar charts.

- ✅ **Per-sheet utilization chart**, so one bad sheet doesn't hide among a wall of individual SVG
  previews. **Built as `UtilizationChart.tsx`**: one bar per *physical* sheet, in job order (not
  deduplicated the way the PDF's "Occurrences" view is — collapsing duplicates would hide exactly
  the "which specific sheet is bad" signal this chart exists for). Bars are threshold-colored
  (`--success` ≥70%, `--accent` 40–70%, `--warning` <40%) and horizontally scrollable for jobs
  with many sheets (the reported job has 67 after the M10/M11 fixes).
- ✅ **Material/waste breakdown chart** — sheets and waste % per material, for jobs spanning
  multiple materials (the reported job has 5). **Built as `MaterialBreakdown.tsx`**: groups
  sheets by material, computes each material's sheet count and average waste % (`100 -` average
  utilization), and renders one horizontal bar-in-row per material, worst-waste-first — the
  material most worth a shop owner's attention leads. Skips rendering entirely for single-material
  jobs (would just repeat the Summary's "Avg. utilization" stat card).
- ✅ **Waste-strategy side-by-side comparison** — orchestrates two `/optimize` calls client-side
  (`"balanced"` vs `"edge"`) and shows results together; no new endpoint needed. **Built as
  `WasteStrategyComparison.tsx`**: a button offering to compare against whichever strategy isn't
  currently active, re-running `/optimize` with the *exact* current request except
  `wasteStrategy` flipped (same parts/stock/margins/target — an apples-to-apples comparison, not
  a fresh job), then showing Sheets/Avg. utilization/Unplaced parts side by side for both. Lazy —
  the second `/optimize` call only fires on click, not on every results view.

Verified in a real headless browser against two real jobs: the 656-part multi-material reported
job (`26Y117T1F1B1(BEDROOM 3-4)`, 67 sheets after M10/M11) — utilization chart bar count matches
the Summary's "Sheets" stat exactly (67), material breakdown shows all 5 real materials sorted by
waste descending, and the strategy comparison populates both columns with real numbers after
clicking; and `panel_saw_machine_data.csv` (21 sheets, 2 materials) — confirmed the three
utilization color tiers all render distinctly. Also visually confirmed in dark mode (Playwright
`colorScheme: "dark"`) — every color in the new components comes from the existing CSS custom
properties, so no separate dark-mode work was needed, just verification that it actually held.
Zero console errors either job. `tsc -b`/lint/build all clean.

**Follow-on, same phase:** asked to also show, per material, how many panels were cut from it
and how many boards of that material type were used. The board count already existed
(`sheetCount`, previously labeled "N sheets") — relabeled to "N boards" to match the request's
own wording, since this app already calls the job-config UI a "Stock Board Library," not a
"sheet library." Added a new `panelCount` field to `MaterialBreakdown.tsx`'s per-material `Row`
(`group.reduce((sum, s) => sum + s.placed.length, 0)`) rendered as its own `N panels` span next
to the board count — two clearly separate, explicitly labeled numbers rather than one combined
string, matching how the request named them as two distinct things. `.material-row`'s CSS grid
widened from 4 to 5 columns (`auto`-sized, so it doesn't force the row wider on typical material
name/percentage lengths); the existing mobile breakpoint already collapses the row to a single
column regardless of column count, so it needed no separate change. Verified in a real headless
browser against `panel_saw_machine_data.csv` (2 materials): both new figures render with correct
singular/plural ("1 board" vs "20 boards"), and — the real correctness check, not just "it
renders something" — the per-material panel counts sum to 50 and board counts sum to 21,
exactly matching the Summary card's own "Panels/Parts cut" and "Sheets" totals for the same job.
Zero console errors. `tsc -b`/lint/build all clean. **Found, not fixed, this pass:** the backend
test suite has 32 errors + 4 failures unrelated to this change — `sample_data/`'s golden-XML
folder was renamed (`XML Data for Nanxing Nesting Machine/` → `XML Data from Fin China/`)
without the test fixture paths being updated, the same class of breakage `CLAUDE.md` already
warns about ("it has moved before and may again"). Flagged to the project owner rather than
fixed, since it's unrelated to this request and the rename might need confirming before test
paths are touched.

## Phase 3 — Presets & cost tracking ✅ done

First phase touching the backend — reuses the exact CRUD pattern M9 already built for stock
boards (SQLite table + `GET`/`POST`/`PUT`/`DELETE` endpoints), not new architecture.

- ✅ **Named parameter presets** — margin/kerf/waste-strategy bundles (e.g. "Standard Panel Saw
  run"), saved and reused the same way stock boards are today. **Built**: a new `presets` SQLite
  table (`backend/storage.py`) storing everything `ParamsPanel.tsx` controls except stock/parts
  (target, margin, kerf, tool Ø, part spacing, allow-rotation, waste strategy) — stored flat
  (`margin_top`/`margin_right`/etc. columns, matching `stock_boards`' existing flat-column
  convention) but exposed over the wire with `margin` nested as `{top,right,bottom,left}` like
  everywhere else in the JSON contract; `api.py`'s two new helpers (`_preset_to_dict`/
  `_preset_kwargs_from_payload`) are the only place that reshapes between the two. Full CRUD
  (`GET`/`POST`/`PUT`/`DELETE /presets`), matching the stock-boards precedent of exposing `PUT`
  even though, also like stock boards, no frontend "edit in place" UI was built — only
  list/create/delete/use. New `PresetLibrary.tsx` **doesn't have its own separate add-form
  fields** the way `StockBoardLibrary.tsx` does — a preset just names and persists whatever's
  currently set in `ParamsPanel.tsx`, so "save" only asks for a name, not a re-entry of every
  field. "Use" applies a preset's values back onto the six pieces of `App.tsx` state it bundles.
- ✅ **Cost-per-board field** on stock boards, plus a $-cost-of-waste figure shown alongside the
  utilization %. Weighted heaviest, per the roadmap's own framing — it turns an abstract
  percentage into a number a shop owner feels directly. **Built**: `cost_per_board` column added
  to `stock_boards` (a real `ALTER TABLE` migration, `storage._migrate()`, since a pre-existing
  local DB file — like the one already running this session — won't retroactively gain a column
  from `CREATE TABLE IF NOT EXISTS`; verified directly against the real dev DB file, not just a
  fresh one). Cost is deliberately **display-only** and never reaches the optimizer: the
  backend's core `StockBoard` dataclass (used by `/optimize`/`/export/*`) is untouched, and the
  frontend's `StockBoardWithCost` type (used for the job's stock list and the library) is a
  strict superset of the wire-contract `StockBoard` — `App.tsx`'s `currentRequest()` strips
  `costPerBoard` back off before building any `/optimize`/`/export/*` call, since the backend
  dataclass would reject the unexpected field outright. New `frontend/src/costUtils.ts` computes
  material cost and waste cost (a sheet's board cost × its wasted fraction) purely client-side
  from the job's own stock list, matched by `(material, thickness)` since the same material can
  have different costs at different thicknesses. Both figures are hidden entirely (not shown as
  "$0.00") when no stock board has a nonzero cost, so "cost not entered" is never confused with
  "confirmed zero waste." Surfaced in two places: `Summary.tsx` gained a second stat row
  ("Material cost" / "Est. waste cost", job-wide) and `MaterialBreakdown.tsx` (Phase 2) gained a
  per-material `$` figure alongside its existing waste % bar.

Covered by 18 new backend tests (134 total, up from 116): `test_storage.py` direct CRUD unit
tests for both presets and stock-board cost (incl. invalid-target/invalid-waste-strategy
validation on presets, mirroring the existing waste-strategy-default validation pattern) and
`test_api_persistence.py` HTTP-level tests via `TestClient` for the same. Verified the preset
validation tests have teeth: temporarily gutted `_validate_preset_fields`, reran, both failed as
expected, restored. Verified end-to-end in a real headless browser: added a stock board with a
cost to the library and confirmed it displays; set a cost directly on the job's own stock table;
saved the current parameters as a named preset, changed the kerf, clicked "Use" on the preset,
and confirmed kerf reset to the saved value; ran optimize and confirmed both new Summary stat
cards appear with real dollar figures. One real, if minor, finding along the way: a `500` from
`GET /presets` turned out to be a **stale long-running `uvicorn --reload` process** (running for
hours across many hot-reloads this session) rather than an application bug — confirmed by
querying the same DB file directly via `storage.py` and via a fresh in-process `TestClient` (both
returned correct data), then restarting the dev server, after which the live endpoint also
returned correctly; not a code fix, just a dev-environment artifact worth knowing about.
`tsc -b`/lint/build all clean.

**Follow-on, same phase:** asked to change currency to **₹** and make the cost unit selectable
(₹/board or ₹/sqft now, "more units we can add later"). `stock_boards`' `cost_per_board` column
was renamed to `cost` and a new `cost_unit` column added — a real migration
(`storage._migrate()`), handling both a DB that predates cost entirely and one still on the
original single-unit `cost_per_board` column from earlier in this same phase (verified against
both starting states, including inserting real pre-migration-shaped data and confirming it
survives the rename, not just the schema change). `VALID_COST_UNITS`/frontend `CostUnit` are
plain extensible tuples/unions specifically so a future unit is a one-line addition, not a schema
change. `frontend/src/costUtils.ts` gained a `costPerBoardFor()` normalization step — ₹/sqft is
scaled by the sheet's own area (`(boardL × boardW) / (304.8×304.8)` mm²-to-sqft) before entering
the same material-cost/waste-cost sums Summary/MaterialBreakdown already had; ₹/board passes
through unchanged. A shared `formatRate()` (`₹80.00/board`, `₹5.50/sqft`) keeps the library table
and job stock table's display consistent. **A real, unrelated bug was found and fixed along the
way**: re-verifying end-to-end surfaced an intermittent `sqlite3.ProgrammingError: SQLite objects
created in a thread can only be used in that same thread` on `GET /presets` — this time against a
*freshly restarted* backend, ruling out the earlier stale-reload explanation. Root cause:
`api.py`'s `get_db()` is a sync generator dependency, and FastAPI runs sync dependencies/
endpoints via anyio's threadpool, which doesn't guarantee the connection's creation and its use
land on the same OS worker thread — a pre-existing latent issue since M9 first introduced SQLite
(not something this pass introduced), exposed by bad thread-scheduling luck rather than by the
currency change itself. Fixed with `check_same_thread=False` on the `sqlite3.connect()` call in
`storage.get_connection()` — safe here since a connection is still only ever driven by one thread
at a time in sequence, never concurrently, being created fresh per request and closed at the end
of that same request. New regression test creates a real-file connection, queries it from a
second `threading.Thread`, and asserts no exception — verified it has teeth (reverted the fix,
reran, reproduced the exact real error message, restored). Full suite: 136→137 (18 Phase-3 tests
adjusted in place for the rename rather than counted as new, +1 genuinely new for the thread-
safety regression). Verified end-to-end in a real headless browser against a freshly restarted
backend: added a ₹/sqft-priced board to the library and confirmed the rate displays correctly
formatted; confirmed the job stock table's per-row unit selector defaults to ₹/board; ran optimize
and confirmed both Summary cost stat cards show ₹ (and no stray `$` anywhere); zero console/
network errors this time, including the specific `GET /presets` call that had failed before the
fix. `tsc -b`/lint/build all clean.

## Phase 4 — Job history

Follows Phase 3 rather than preceding it, since a saved job record is more useful once it can
also capture which preset and cost basis were used. Needs a real new table — every optimize run
is currently discarded the moment the results screen is left — plus a browsing/comparison UI.

## Phase 5 — M8 offcut reuse

The largest remaining item, and the one that makes the Phase 2/3 waste numbers actionable
rather than just informative: persist real offcuts as usable stock, feed them back into the
optimizer as available input for future jobs, and give operators a "waste bank" to browse.
Naturally sits on top of Phase 4's data model rather than before it.

## Cross-cutting — not a phase

- **Frontend test suite.** Start alongside Phase 1, not bolted on at the end, so Phases 2–5 get
  regression coverage as they land instead of everything being manually browser-verified the
  way every frontend change has been so far (`frontend/` currently has no automated tests —
  type-check, build, lint, and manual Playwright passes are the only verification).

---

## On reordering

This sequencing optimizes for "cheapest to build next," not necessarily "matters most to the
business." If cost tracking (Phase 3) or offcut reuse (Phase 5) is actually the priority, either
can be pulled forward — Phase 3's preset half and Phase 5 don't depend on Phases 1–2 completing
first, only on the backend patterns M9 already established.

Also unrelated to this roadmap but still open per `CLAUDE.md`'s own "Remaining work" list: a
physical machine dry-run to confirm M6/M10/M11's output on real material, and the deferred parts
of M9 (login/auth, tenant/company modeling, per-machine "available optimizations" config,
CSV-schema-template renaming). Those aren't UI/UX or data-viz work, so they're tracked in
`CLAUDE.md` rather than duplicated here.
