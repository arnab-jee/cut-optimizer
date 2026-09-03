# CLAUDE.md — Wood Panel Optimization Web App (nesting-pro)

> Durable project context for Claude Code. This file is auto-loaded every session.
> Keep the **Current state** section updated as work lands so a lost/closed session
> costs nothing to resume. The full build spec lives in `instructions.md`.

---

## What this app is

Ingests panel cutting data (CSV) → runs machine-appropriate 2D cutting optimization →
exports either:

- **NanXing FCC nesting XML** for the **Nanxing NCG2812LE** CNC nesting router, or
- a **labeled layout PDF** for the **panel saw** operator.

It replaces **NirvanaTec PLUS 2D** for the panel-saw workflow.

### The one non-negotiable design principle
**Two machines = two different optimizers, not one optimizer with two exporters.**

| | Panel Saw | Nanxing NCG2812LE |
|---|---|---|
| Cut | Straight, edge-to-edge | Each part routed individually |
| Algorithm | **Guillotine** bin-packing | **Free / true-shape** nesting |
| Output | Labeled layout PDF + cut sequence | FCC nesting **XML** + per-part toolpaths |

Never feed free-nest output to the saw exporter — a non-guillotine layout **cannot** be
cut on a panel saw. Target machine is chosen up front by the user and drives everything.

### Confirmed shop parameters
- Router tool Ø: **fixed 6mm**. Board margin: **variable — user input**.
- Offcut/oddment reuse: **in scope** (returnable stock, M8).
- **Grain-directional stock IS used** — rotation must be lockable per material/part.
  Do not assume rotation is free just because sample CSVs showed grain=0.
- Golden machine-cut XML files exist and were reverse-engineered → see `instructions.md`
  **Appendix A** (turns M6 from guesswork into a derived spec).

---

## Architecture (CONFIRMED)

- **Core + exporters: Python (FastAPI).** Pure, framework-free `optimizer/` package that
  FastAPI wraps, so it's independently testable.
  Endpoints: `POST /optimize`, `POST /export/xml`, `POST /export/pdf`.
- **Frontend: React + TypeScript (Vite).** PapaParse for CSV, SVG/Canvas for previews.
- Everything crosses the boundary as the **normalized JSON contract** (see `instructions.md` §3).

Key libs: `shapely` (geometry), `rectpack`/custom packers (nesting), `lxml` (FCC XML),
`reportlab`/`svglib` (PDF).

---

## Milestone status

> Verified 2026-08-12 by actually running the code against `sample_data/` (both CSVs and the
> real golden XML files), not just reading it. A three-phase fix plan was written to
> `~/.claude/plans/delegated-moseying-robin.md`; **Phases A, B, and C are all done**, plus
> follow-on M6, M7, M3-redesign, M9, M10, and M11 passes (each planned separately, same plan
> file, overwritten per pass). Remaining work is M8, the deferred parts of M9 (login/tenancy,
> per-machine config, schema-template renaming), and getting the M11-fixed XML confirmed on the
> real machine (see "Remaining work" §1).

| # | Milestone | Status | Evidence |
|---|-----------|--------|----------|
| M1 | Parser + normalized model | 🟢 Fixed + tested | Parses both real CSVs cleanly (55 + 50 parts, 0 errors). Quoted-space edge codes (`""" """` → `'" "'`) now normalize to `""` (`normalize_edge_value` in `parser.py`). Job grouping now splits by `(material, thickness, grain)` as independent nesting jobs (both optimizers). `GRAIN_MAP` in `parser.py` now maps raw CSV codes `1`/`2` to `"length"`/`"width"` (`Business Logic/grain_logic.md`'s spec: 0=free, 1=part's length parallel to grain, 2=length perpendicular to grain i.e. width parallel to grain) — previously `1`/`2` fell through the `.get()` default to `"none"`, silently treating grain-locked parts as rotatable in both the packer (`can_rotate()`) and the exported FCC `Grain` attribute. Neither of the two original sample CSVs (`nesting_machine_data.csv`, `panel_saw_machine_data.csv`) contains `1`/`2` (only `0`), so this had never surfaced in the test suite from those files — but a third sample CSV added later, `26Y117T1F1B1(BEDROOM 3-4)...csv`, does contain real `grain=1` data, and was in fact what exposed M10's placement-axis bug (see that row). Covered by `backend/tests/test_parser.py` (12 tests, +2 this pass). |
| M2 | Guillotine optimizer (saw) | 🟢 Fixed + tested | Rewrote placement around a true binary guillotine split (`guillotine_split`) instead of the old 4-way maxrects-style split — free rectangles can no longer overlap by construction. Multi-sheet loop opens new sheets of a board type until every part is placed or genuinely too large for an empty board. Covered by `backend/tests/test_guillotine.py` (7 invariant tests × 2 real sample files, incl. an automated guillotine-decomposability check per spec §7.4). **Verified the suite has teeth**: temporarily restored the original buggy version and reran — 8/14 tests failed (overlaps, dropped parts, non-decomposable layouts), confirming these tests would have caught the original bug. Cut list now flows through to `/optimize`'s response. **`Updates/update_003.md` (2026-08-13): un-shared the placement engine.** It briefly lived in one shared `optimizer/packing.py` (extracted so M4 could reuse it, see that row's old text) — update_003 asked to "maintain separate packers" for the two machines, reinstating this file's own "two machines = two different optimizers" principle at the code level, not just the exporter level. `guillotine.py` now imports its own copy from `optimizer/saw_packing.py`; `nanxing.py` imports an independent copy from `optimizer/nanxing_packing.py` (see M4 row) — deliberately duplicated, not shared, so a future change to one can't silently affect the other. Both copies also gained a free-rectangle merge step (`merge_free_rects`, folds adjacent free rectangles back into one after every placement) and a `waste_strategy` param (`"balanced"` default reproduces prior behavior exactly; `"edge"` — see M4 row for what it does and its measured effect). |
| M3 | PDF layout export (saw) | 🟢 Redesigned + tested | `optimizer/export/pdf.py` went through two full redesigns this project. **Rev 1** (`Updates/update_002.md` → moved to `Business Logic/grain_logic.md`, unrelated to grain — see below) matched `sample_data/NirvanaTec Plus2D Optimization Drawing PDFs/`: landscape board (transposed render axes), colored panels, 2-per-page. **Rev 2, current** (a *second, different* `Updates/update_002.md` — the filename was reused for an unrelated spec; see the "reused filenames" note above) replaces Rev 1 entirely, following `sample_data/Max Cut Optimization Drawings/max_cut.pdf` instead: a left sidebar (material + sheet size, a per-sheet "Cutting List" grouped by `(name, nominal L, nominal W)` with running Symbol numbers via `_cutting_list()`, an "Occurrences ×N" box, a "Grain Direction" arrow box) beside a main area (a "Job Layout" header, a Client/Job/Sheet/Job stats grid, the board diagram). Board draws **portrait** this time (`board.length` vertical) — the *opposite* of Rev 1's landscape, and matching the packer's native axes directly, so no render-axis transform is needed (removed `_render_sheet_area`/`_render_offcut_area` along with it). Per the update's explicit instructions: (1) **occurrence deduplication** — `_deduplicate_layouts()` collapses physically-identical sheets (same board + same placed-part positions) into one printed page with an `×N` badge instead of N near-duplicate pages (real-data result: the 21-sheet saw sample collapses to **9** printed pages, one page alone absorbing 9 duplicate sheets); "Job Sheets"/"Job Panels"/etc. still count the *physical*, un-deduplicated sheet list. (2) **date format** `DD-MMM-YYYY hh:mm:ss` in the footer via `datetime.now().strftime(...)`. (3) **grain-direction arrow**: empty box for `grain="none"` (per the update's literal instruction — the reference itself shows an ambiguous always-on 4-way icon that didn't reliably indicate this, see below); a single vertical double-headed arrow for `grain="length"`, horizontal for `"width"` — **this mapping was NOT derivable from the reference alone** (same material showed different icons across different sheets in the reference, ruling out a simple per-material constant, and MaxCut's own internal packing-axis convention doesn't visibly match ours) and was confirmed directly with the project owner via `AskUserQuestion` rather than guessed, given the real risk (wrong grain direction → scrapped material). (4) **kept colored panels** (palette cycling by placement order, same approach as Rev 1) — the one explicit departure from the reference, which is plain black-and-white. (5) **reverted to 1 layout per page** (Rev 1's 2-per-page doesn't fit this denser layout, per the update). Client Name/Job Reference/Phone/Fax/Cell No/Date Required are left blank (labels only) — no such data exists in this app, and the reference's own sample pages leave them blank too, so this isn't a fabrication gap. "Sheet/Job Cut Length" reuse `OptResult.cuts` (already computed by M2's guillotine cut-list builder); "Job Wastage" is an area-weighted average of each sheet's own utilization (no per-sheet margin data is stored on `Sheet` to compute it more precisely — documented approximation, not a fabrication). Covered by `backend/tests/test_pdf.py` (10 tests, fully rewritten for Rev 2: dedup-page-count, page-text sanity, empty-result, dedup grouping + signature equality, cutting-list grouping/symbol assignment incl. the nominal-vs-rotated-footprint distinction, the confirmed grain-direction mapping, palette-cycling, and a sidebar/footer-overlap geometry regression — see next). **Found and fixed one real bug via visual inspection** (not caught by any test until added after the fact): the Occurrences/Grain Direction sidebar boxes extended down to the page's true bottom margin instead of stopping above the footer strip, so they visually overlapped the footer text — full pages were rendered and eyeballed, not just computed from geometry math, which is what caught it. Fixed via a `_sidebar_bottom_boxes()` helper now covered by a dedicated regression test; verified that test fails against the pre-fix geometry and passes after restoring the fix. Still open from spec §6a: no cut-sequence list/overlay (neither reference PDF shows one). |
| M4 | Free-nest optimizer (router) | 🟢 Fixed + tested | Same multi-sheet loop and grain-grouping fix as M2. **The naive shelf packer flagged in earlier passes is gone**: prompted by `update_001` comparing our output against the real Nanxing machine's own software on the same job (identical board/margin/spacing — a real efficiency benchmark), found the shelf packer's root cause (once a row wraps, its leftover space is never reconsidered by later, smaller parts) and replaced it with the same class of free-rectangle best-fit engine `guillotine.py` uses (now `optimizer/nanxing_packing.py`, its own independent copy — see M2 row). Real-data result at the time: the sheet holding the most parts in `nesting_machine_data.csv` went from 21 parts at 21.4% utilization to 29 parts at 73.8% — still below the real machine's 78–92%. Covered by `backend/tests/test_nanxing.py` (7 tests incl. a new dominant-sheet-utilization regression guard) + `test_guillotine.py`. Verified the new test has teeth the same way as always: reverted to the old shelf packer, confirmed it fails (21.4% < 55% threshold), restored. **`Updates/update_003.md` (2026-08-13): `waste_strategy` option closes most of that remaining gap.** Prompted by a real screenshot (`Updates/image.png`) of this app's own Nanxing PDF output showing thin wastage slivers scattered between placed drawer parts instead of consolidated at one edge — added a selectable `waste_strategy`: `"balanced"` (default, prior behavior) vs `"edge"` (forces the guillotine split to always cut along the same fixed axis instead of picking whichever leaves the shorter leftover strip, so leftover space keeps accumulating into fewer, larger regions instead of a new sliver on every placement — see `optimizer/saw_packing.py`'s `guillotine_split` docstring for the full reasoning). Verified on the real `nesting_machine_data.csv` job: the busiest sheet went from 28 parts/73.56% utilization (`"balanced"`, re-measured after the M2 merge-step addition — was 29/73.8% before it, an incidental ~0.2pp shift from a scoring tie now resolving differently, not a regression) to **33 parts/78.41%** under `"edge"` — now inside the real machine's 78–92% range instead of below it. Job-wide, the largest single offcut's share of total offcut area rose from ~65% to ~73% (a direct, measured consolidation metric, not just an aggregate utilization number). Re-rendered and visually compared both strategies' PDF page for the same sheet to confirm the *pattern* actually changed, not just the numbers — "balanced" still shows several similar-sized gaps between strips; "edge" shows one larger consolidated gap. Covered by new `backend/tests/test_packing_engines.py` (12 tests: packer-module independence, `merge_free_rects` correctness, `"edge"`'s fixed-axis behavior, guillotine-decomposability preserved under `"edge"` for both machines, and the largest-offcut-fraction consolidation regression guard) — verified that last test and the axis-forcing test both have teeth by temporarily reverting the fix and confirming failure, then restoring. Exposed as a "Waste placement" dropdown in `frontend/src/components/ParamsPanel.tsx` (`tsc -b`/lint/build all clean) for both machine targets, not just Nanxing, since both packers now support it identically. |
| M5 | FCC XML geometry | 🟢 Rebuilt + tested | `optimizer/export/xml.py` fully rewritten around the real `FccRoot`/`Patterns`/`Pattern`/`Workpieces`/`Workpiece`(+`EdgeGroup`/`Lineament`/`Lineament2`/`FccOutline`/`BenchmarkInfo`)/`OddmentsList` structure (spec §6b + Appendix A). Byte-exact on the empty-job case (Appendix A.8). `MachiningPoint` now uses the exact rule M6 discovered (see below) — **0 mismatches across all 4 non-empty golden files (1039 workpieces)**, not the ~83%/17% Appendix A.3 describes. **Pass 21 (2026-09-02, see "Last worked"): `Workpiece.Info1`/`Info2` added** — a real physical machine label showed a blank "F.S." (Final Size) field; found via barcode cross-reference against real golden data that `Info1`/`Info2` hold the source CSV's own `Lenght`/`Width` columns exactly (55/55 real parts, no rotation dependency), not something derivable from other XML fields as an earlier pass had assumed. `CutLength`/`CutWidth` (actual cutting geometry) deliberately untouched. Two related discrepancies found but left alone (`Length`/`Width`'s own formula; the importer still reads the old, wrong source) — see "Remaining work" #5. |
| M6 | FCC XML toolpaths | 🟢 Implemented + tested | `CutInfos.ToolPointList`/`ToolPoint` implemented per Appendix A.5's lead-in-ramp formula, plus two rules the appendix doesn't document, both found by testing against real files: **(1)** when an edge is shorter than `SlopeLen` (70mm), both of that edge's candidate points clamp to the edge midpoint instead of the raw corner-offset formula; **(2)** the `Lineament`/`Lineament2`/`FccOutline` polygon winding — and the `ToolPointList` idx→corner assignment — starts one corner later exactly when `CutWidth > CutLength` **and** the part isn't grain-locked (grain-locked parts never shift, confirmed against 207 grain-directional workpieces, all unrotated). This same signal turned out to fully explain `MachiningPoint`'s "~83%/17%" split from Appendix A.3 (now exact, see M5). `ToolPointList` itself lands at 88.1–96.4% exact match across the 4 golden files — the remaining mismatches look like isolated real-world manual adjustments (e.g. a single corner off by 6.8mm on one otherwise-perfect 55/56-attribute workpiece) rather than a missed rule; Appendix A.5 itself expects this needs machine dry-run refinement, so it's tracked as a documented tolerance (`backend/tests/test_xml_roundtrip.py`'s `MIN_TOOL_POINT_LIST_MATCH_RATE`), not chased to 100%. `ToolPoint` (which of the 4 lead-in points the cut starts at) has no discovered rule — defaults to `"0"` per Appendix A.5's own stated fallback. Point-winding and `MachiningPoint` comparisons in the round-trip test were tightened from tolerant to strict now that the real rules are known, and confirmed to have teeth (broke the shift logic, reran, 4/4 golden-file tests failed on the exact-match `MachiningPoint` assertion; restored, all green). |
| M7 | Frontend integration | 🟢 Built + browser-verified | New `frontend/` (Vite + React + TypeScript), sibling to `backend/`. Wizard flow: CSV drag-drop → column-mapping (client-side schema guess in `csvSchemas.ts` for immediate feedback; actual parsing delegated to the already-tested `/api/parse`, not reimplemented in TS) → machine selector + params panel (margins, stock boards derived from parts, kerf/saw or tool Ø+spacing/nanxing) → per-sheet SVG preview (`SheetPreview.tsx`) + summary (`Summary.tsx`, with a client-computed unplaced-reason since the backend doesn't attach one) → PDF/XML download via the existing `/api/export/*` endpoints. Dev wiring is a Vite `server.proxy` (`/api` → `127.0.0.1:8000`), zero backend changes. `tsc -b`/`npm run build`/`npm run lint` all clean. **Actually driven in a headless browser** (Playwright, no project `run` skill existed yet so used the generic browser-driven fallback): uploaded both real sample CSVs — correct schema auto-detection for both, part counts matched exactly (50, 55); ran optimize for both Panel Saw and Nanxing — 21 sheets / 0 unplaced / 46.8% avg utilization each, matching Phase A's known-good numbers exactly; 21 SVG sheet previews rendered with 50 total placed-part rects (matches part count); downloaded a real 21-page PDF and a real `FccRoot` XML; a deliberately malformed CSV correctly surfaced the backend's exact validation error in the UI instead of crashing. Zero console/page/network errors throughout. **Scope decision:** the per-sheet preview does *not* share a renderer with the PDF (spec §8's aspiration) — that would mean redesigning M3's still-skeletal `reportlab` renderer, a separate concern; downloads call the existing `/export/pdf`/`/export/xml` endpoints as-is. **Visual polish pass** (presentation-only, no logic changes): real design tokens + dark-mode support in `index.css`, a `Stepper.tsx` progress indicator, card-based layout, stat cards, selectable machine-option cards, and a responsive sheet-preview grid. Found and fixed one real bug while at it — the Vite scaffold's leftover `#root { text-align: center }` was inheriting into every form label/paragraph in the app. Reverified in a headless browser (screenshots at each wizard step) with the same real CSV — same known-good numbers (21 sheets, 0 unplaced), zero console errors, confirmed `text-align: left` via computed style. **`Updates/update_005.md` (2026-08-14): added a 4th Summary stat, "Panels/Parts cut"** (`sum` of `sheet.placed.length` across all sheets) alongside Sheets/Avg. utilization/Unplaced parts — `.stat-row`'s CSS grid widened from a hardcoded 3 to 4 columns to fit it evenly. Verified in a headless browser against a real sample CSV: shows `50`, matching that file's known part count exactly; zero console errors; screenshot confirmed the 4 cards lay out cleanly with no overlap. |
| M8 | Offcut/oddment reuse | ⬜ Not started | `Sheet.offcuts` are computed as leftover free rectangles per run but never persisted or fed back as input stock for a later job. |
| M9 | Data persistence, phase 1 (stock boards + settings) | 🟢 Implemented + tested | `Updates/update_004.md` originally asked for a much bigger scope in one pass — login, multi-tenant "company system," stock boards, per-machine optimization availability, waste-placement defaults, and renaming/editing the CSV schema templates — but explicitly asked to "discuss and ask relevant questions before start updating the code" first. Scoped down via two `AskUserQuestion` rounds before writing anything: **SQLite** (not Postgres/MySQL — single-tenant local deployment, no need for a DB server), **single-tenant** (no `tenant`/company isolation), **simple internal login deferred entirely** (this pass has no auth at all), **persistence-first sequencing** (only Stock Boards + a Waste Placement default land now; login/tenancy, per-machine availability config, and schema-template renaming are explicitly future passes, not started). New `backend/storage.py`: stdlib `sqlite3` (no ORM — two small tables don't justify one), a `stock_boards` table and a generic `key/value` `settings` table (chosen over a rigid single-column settings table so future single-value defaults don't need a schema migration each time). New endpoints: `GET/POST /stock-boards`, `PUT/DELETE /stock-boards/{id}`, `GET/PUT /settings`, using a per-request `sqlite3.Connection` via FastAPI `Depends` (safe under SQLite's threading model; the DB file itself is gitignored, not committed). Frontend: new `StockBoardLibrary.tsx` (list/add/delete saved boards, "Use" appends one to the current job's stock list) rendered in the configure step, and `wasteStrategy` now loads its initial value from `GET /settings` on mount and round-trips every change back via `PUT /settings` (a "sticky default," not a separate save button). Covered by `backend/tests/test_storage.py` (11 tests, direct CRUD unit tests against an in-memory DB) and `backend/tests/test_api_persistence.py` (8 tests, real FastAPI `TestClient` HTTP-level tests against a temp-file DB — `:memory:` doesn't work here since `get_db` opens a fresh connection per request and in-memory SQLite doesn't survive across separate connections) — this also closes a sliver of the long-standing "no test touches `api.py` directly" gap, scoped just to the new endpoints. Verified the validation-error path has teeth: temporarily disabled the waste-strategy value check in `storage.py`, reran, both the unit test and the HTTP test failed as expected, restored. **Also verified end-to-end in a real headless browser** (Playwright via a scratch script, no project `run` skill existed yet): fresh SQLite DB → confirmed `GET /settings` defaults to `"balanced"` → uploaded a real sample CSV → added a stock board through the library form → confirmed it appeared in the library table → clicked "Use" and confirmed it appeared in the job's own Stock boards table → changed the waste-placement dropdown and confirmed the change round-tripped to the server (`GET /settings` reflected it, not just React state) → deleted the library entry and confirmed it disappeared. Zero console/page errors throughout. `tsc -b`/lint/build all clean. Deferred to a later pass: login/auth, tenant/company modeling, per-machine "available optimizations" config, and the CSV-schema-template rename (Nanxing Nesting → "Template 1", Panel Saw → "Template 2" — mapping confirmed with the project owner, not yet implemented). |
| M10 | Grain-locked placement axis fix | 🟢 Fixed + tested | **Real production bug, reported via `Issues/issues_001.md`** and confirmed against real golden Nanxing machine data before touching code (not just re-reading our own logic): the packer forced every `grain="length"` part's `cutLength` onto the board's *width*-derived axis (1220mm nominal) and never tried the board's *length*-derived axis (2440mm nominal), because `can_rotate()==False` for any grain-locked part skipped the alternate-orientation branch entirely — a leftover of a rotation gate that doesn't distinguish *which* fixed orientation a `"length"`-vs-`"width"`-grain part actually needs. Any `grain="length"` part whose `cutLength` exceeded ~1205mm usable width was silently rejected as unplaceable even when it fit the 2430mm usable length axis easily — exactly the case in the reported issue (`CutLength=1323.4`/`2172.4`, both `>1205mm`, both `≤2430mm`). Verified against `sample_data/XML Data for Nanxing Nesting Machine/`: found 16 real `Grain="L"` golden workpieces including the *exact same part* (`WorkpieceId 26Y117T1F1B1_1001`, `CutLength=1323.4`) placed by the real machine with an X-span of ~1329mm on a 2440mm-length board and `RotateAngle` absent — i.e. the real machine treats "cutLength along the board's length axis" as the grain="length" part's *natural*, non-rotated pose, the opposite of what our packer assumed. No `Grain="W"` examples exist in any golden file to verify against directly, but `grain="width"`'s pairing was already correct (matches `grain_logic.md`'s spec symmetry) and is untouched by this fix. Fixed via a new `_footprint(part, rotated)` helper in both `saw_packing.py` and `nanxing_packing.py` (identical, kept in sync per M2's "separate packers" duplication) that swaps the pw/ph assignment based on `part.grain == "length"` *before* applying the `rotated` flag — critically, `PlacedPart.rotated` still means "an actual 90° turn away from the grain-mandated natural pose," not "axes swapped," so the already-verified FCC XML `RotateAngle` export (M5/M6) needed no changes and stays correct. `optimizer/export/xml.py` was independently confirmed unaffected: it reads `CutLength`/`CutWidth` from the original `Part`, never from the placed footprint. `pdf.py`'s cutting-list/dimension-label derivation *did* rely on `(w, h, rotated)` to recover nominal dimensions and would have started mislabeling `grain="length"` parts once the packer fix landed, so it got a matching grain-aware `_nominal_dims()` helper. Real-data result on the reported job (`26Y117T1F1B1(BEDROOM 3-4)` CSV, 656 parts): unplaced dropped from 16→0 on **both** Panel Saw and Nanxing (the issue reporter confirmed it reproduces identically on both — now explained: both packers share the same bug/fix). Re-verified full geometry invariants on this exact job post-fix: all 656 parts accounted for, 0 overlaps, 0 non-guillotine-decomposable sheets, 0 out-of-margin placements. Covered by `backend/tests/test_packing_engines.py` (+7 tests: `_footprint()` grain-aware unit tests for both packer modules, an integration test placing the exact real failing part, and a control test confirming a genuinely-too-large part still correctly ends up unplaced) and `backend/tests/test_pdf.py` (+2 tests for `_nominal_dims()`). None of the existing sample-CSV-driven tests exercise grain-locked parts at all (`nesting_machine_data.csv`/`panel_saw_machine_data.csv` are both 100% `grain="none"` — confirmed by direct check), so this fix changes zero previously-tested behavior; full suite went 95→106 passing with no other deltas. Verified the new tests have teeth: temporarily reverted `_footprint()` to the old grain-blind version, reran, 4 tests failed as expected, restored. **Follow-on frontend fix, same pass:** `Summary.tsx`'s client-side `unplacedReason()` (M7) had its own independent version of the same axis-mislabeling bug, plus it ignored margins and the `allowRotation` toggle entirely. Rewrote it to mirror `_footprint()`'s grain-aware natural-pose logic exactly, now margin-aware (`board.width/length` minus the actual configured margins) and gated on `allowRotation`, and takes `margin`/`allowRotation` as new props from `App.tsx`. Verified in a real headless browser (no genuinely-unplaceable part exists in the reported job anymore post-fix, so a synthetic scenario was used: shrank one stock board's width to 50mm in the UI, forcing 208 parts genuinely too large) — confirmed the table renders the new, correctly-computed `"larger than the board's usable 35.0×2430.0mm area after margins, in any orientation its grain allows"` message (35.0 = 50 − 5 − 10 margins, matching the real configured margin values, not hardcoded). `tsc -b`/lint/build all clean, zero console errors. |
| M11 | FCC XML X/Y axis inversion fix | 🟢 Fixed + tested | **The most significant bug found in this project — real machine load, reported via `Issues/issues_002.md` with 4 screenshots of the actual NaccNesting software.** Every task loaded from this app's own XML export showed parts crammed into a region no bigger than the board's *width* (~1220mm), with the board's real *length* (2440mm) left almost entirely empty — reported utilization numbers (85–89%) didn't match what was visually on screen, which was the actual tell. Root cause: `optimizer/export/xml.py` wrote `PlacedPart.x`/`.y` straight into the XML `X`/`Y` attributes with no transform. But the packer (`saw_packing.py`/`nanxing_packing.py`) places parts with `x` bounded by `board.width` (~1205mm usable) and `y` bounded by `board.length` (~2430mm usable) — while the real machine's XML convention is the *opposite*: `X` follows the board's length axis (confirmed independently during M10's golden-data investigation — a real `Grain="L"` workpiece spanned ~2178mm in X, only possible if X is the long axis — and now doubly confirmed by this real machine load). So every workpiece's *width*-axis position got reported to the machine as its *length*-axis position, capping every placement at ~1205mm regardless of the board's real 2440mm length. **Why 106 passing tests, including an extensive golden-file round-trip suite, never caught this:** `tests/fcc_golden.py` (the round-trip test's XML importer) made the exact same backwards assumption on the way in (`x=minx` from the golden file's own X, no swap) — so importing a golden file and re-exporting it cancelled the bug out both ways, and the round-trip matched byte-for-byte regardless of whether the axis labeling was actually correct. That test only ever re-serializes XML-sourced data; it never once ran data through the export path starting from the real packer's own output — which is exactly what `/export/xml` does for every real user. The bug had been there since M5/M6 first built this exporter; it just never surfaced until this real machine test. **The PDF export was unaffected** — `pdf.py` happens to already use the packer's `x`/`y` in the convention the packer actually produces, which is why the PDF drawings the project owner has been visually checking all session were correct while the XML silently wasn't; this is itself a small lesson in favor of rendering/visualizing output over trusting numeric round-trip tests alone. Fixed by transposing both sides consistently: `xml.py`'s `_workpiece_element`/`_oddments_element` now build the XML's `X`/width-extent from `placed.y`/`.h` and `Y`/length-extent from `placed.x`/`.w`; `tests/fcc_golden.py`'s importer applies the identical swap in reverse, so the golden round-trip tests keep passing (both sides of a self-consistent-but-wrong convention became both sides of a self-consistent-and-right one). New `backend/tests/test_xml_export_coordinates.py` (3 tests) exercises exactly the path the round-trip test structurally can't: two synthetic unit tests asserting a large `placed.y` value lands in XML `X` (not `Y`) for both workpieces and oddments, plus an integration test running a real sample CSV through the actual packer and exporter and asserting every workpiece's XML points fall within the declared board bounds *and* that at least one genuinely uses `X` beyond the board's width (a deliberately discriminating assertion — a test that never exercises the long axis can't tell a correct mapping from a swapped one). Verified these have teeth: reverted both transposes, reran, all 3 new tests failed — including the real-data test catching a real Y=1460.8mm on a 1220mm-wide board — restored. **Verified against the actual reported job**: regenerated XML for the exact CSV from the issue (`26Y117T1F1B1(BEDROOM 3-4)`, material `CC_MDF17_8134_BS` — the same material as screenshot 1) and confirmed max X now reaches 2349.6mm (of 2440mm available) and max Y stays within 1129.2mm (of 1220mm) — using the board's real length for the first time. Saved to `results/issue-002-fixed/` for the project owner to reload into the real NaccNesting software and confirm physically, the same way the original bug was found. Full suite: 106→109 passing. |

**Cross-cutting:** `backend/tests/` now has `conftest.py`, `helpers.py`, `fcc_golden.py` (golden
XML → exporter-input importer, now a thin wrapper around `optimizer/import_xml.py` — see M6-row
successor pass below), `test_parser.py`, `test_guillotine.py`,
`test_nanxing.py`, `test_xml_roundtrip.py` (now parametrized across all 4 non-empty golden
files, not just one), `test_pdf.py`, `test_packing_engines.py`, `test_storage.py`,
`test_api_persistence.py`, `test_xml_export_coordinates.py`, `test_import_xml.py`,
`test_api_import.py`, `test_placement.py`, `test_api_optimize.py`, `test_nanxing_search.py` —
**192 tests** (re-counted directly via `pytest --collect-only` during pass 21, +2 more in pass
22, +6 more in pass 25, see "Last worked" — the figure recorded here had drifted a few sessions
stale before pass 21), all green from a clean
`pip install -e ".[dev]"` (once the stale `sample_data` XML path from "Remaining work" #4 is
worked around).
`test_api_persistence.py` is the first test file to exercise `api.py` directly over real HTTP
(via FastAPI's `TestClient`, new `httpx` dev dep) — scoped just to the new `/stock-boards` and
`/settings` endpoints; `/parse`, `/optimize`, `/export/pdf`, `/export/xml` still aren't covered
as HTTP endpoints, only their underlying `optimizer/` functions are. `frontend/` has no
automated tests yet (type-check + build + manual browser runs only) — no test framework wired up.

**Directory reorg (post-M7):** `sample_data/` now nests its CSVs under `CSV Files from IMOS/`
and golden XMLs under `XML Data for Nanxing Nesting Machine/` (previously flat), and gained a
`NirvanaTec Plus2D Optimization Drawing PDFs/` folder (M3 Rev 1's reference, now superseded)
and a `Max Cut Optimization Drawings/` folder (M3 Rev 2's reference, current — see M3 row). A
`results/` folder was also added holding this app's own prior exports side-by-side with a real
Nanxing-software export, for comparison. `backend/tests/conftest.py` and `test_xml_roundtrip.py`
were updated to the new nested paths (`CSV_SAMPLE_DIR`, `XML_GOLDEN_DIR`) — **if a fresh session
sees `FileNotFoundError` from the test suite, check `sample_data/`'s actual layout before
assuming it's a code bug**; it has moved before and may again. Also note: **`Updates/update_002.md`
has been reused for two unrelated specs in this project** (first the grain-code fix, now moved to
`Business Logic/grain_logic.md`; then this PDF redesign) — don't assume a filename identifies
content across sessions, always re-read it. An `Issues/` folder was also added, parallel to
`Updates/`, for user-reported bugs with screenshots (see `Issues/issues_001.md`, M10) — same
pattern as `Updates/`: check it for real-world ground-truth, and re-read files rather than
trusting a stale summary, since the same file got reframed mid-conversation once already.

**This app's XML output has now touched a real machine — loaded, not yet cut — and it found a
real bug.** M11 (X/Y axes inverted, see that row) was caught this way, after M5/M6's own
golden-file round-trip tests passed 100% clean the whole time; loading the actual file into the
actual NaccNesting software found something byte-level comparison against historical files
structurally could not. A physical dry-run *cut* is still needed before fully trusting this on
real material — especially since `ToolPoint`'s rule and `MachiningPoint=7`'s actual on-machine
behavior remain unconfirmed — but "never touched a real machine" is no longer literally true,
and the one time it did, it was worth it. M8 (offcut reuse) is the remaining open backend item;
M3's cut-sequence overlay was deliberately not built (see M3 row).

---

## Current state

<!-- Update after each work block. This is what a fresh session needs most. -->

- **Last worked:** 2026-09-03 — twenty-five passes across six sessions (this session opened
  without the direct conversation history for passes 9–18 below — resumed entirely from this
  file, the auto-memory note on `DESKTOP_APP_PLAN.md`, and the actual repo state, which is
  exactly the point of keeping this file current). (1) Applied
  `Business Logic/grain_logic.md` (raw CSV `Grain` codes are `0`/`1`/`2`, not just `0`/`x`/`y`;
  `1`/`2` were previously unmapped and silently treated as ungrained/rotatable). Fixed in
  `GRAIN_MAP` (`backend/optimizer/parser.py`), see M1 row. (2) Redesigned M3's PDF export to
  match the NirvanaTec PLUS 2D reference (M3 Rev 1: colored panels, landscape orientation,
  2-per-page) after the user reorganized `sample_data/`/`results/` and pointed at that
  reference; the reorg also nested the CSV/XML fixture files the test suite reads, requiring a
  `conftest.py`/`test_xml_roundtrip.py` path fix first (see "Directory reorg" above). (3) A new
  `Updates/update_002.md` (reusing that filename for an unrelated spec — see the reused-filenames
  note above) asked for a *second*, different PDF redesign matching MaxCut software's own
  reference instead (`Max Cut Optimization Drawings/max_cut.pdf`) — sidebar with cutting
  list/occurrence badge/grain arrow, portrait board orientation, occurrence deduplication, new
  date format, back to 1-per-page. This **replaced** Rev 1 entirely (M3 row now describes both
  revisions). One point needed the project owner's direct input rather than a guess: which
  physical board axis the grain-direction arrow points along for grain-locked sheets — the
  reference itself didn't unambiguously establish this (see M3 row), and guessing wrong on a
  real job risks scrapped material, so this was resolved via `AskUserQuestion`, not inferred.
  (4) `Updates/update_003.md` asked for two things: un-share the saw/router packers (done, see
  M2 row) and a selectable wastage-placement option, prompted by a real screenshot of this app's
  own Nanxing output (`Updates/image.png`) showing scattered wastage slivers instead of
  consolidated ones — added `waste_strategy` (see M4 row), measured a real utilization
  improvement on the busiest sheet of the real sample job (73.56%→78.41%), and exposed it in the
  frontend `ParamsPanel`. (5) `Updates/update_004.md` first asked for a large, one-shot
  persistence/auth/multi-tenancy build, but explicitly asked to discuss and ask questions before
  touching code — two `AskUserQuestion` rounds scoped it down to SQLite, single-tenant, no
  login yet, and just Stock Boards + a Waste Placement default persisted this pass (see M9 row
  for the full scoping trail and what got deferred). (6) `Issues/issues_001.md` reported real
  unplaced parts on a real job (`26Y117T1F1B1(BEDROOM 3-4)` CSV) and pushed back — correctly —
  when told the parts were "too large": the reported board did physically cover the reported
  part. That pushback led to checking the real golden Nanxing XML data instead of re-explaining
  our own code, which found a genuine placement-axis bug affecting every `grain="length"` part
  whose `cutLength` exceeds the board's *width* (not length) — see M10 row. Fixed in both
  packers plus a matching fix in `pdf.py`'s dimension-label derivation; unplaced went 16→0 on
  the reported job for both machines. (7) `Issues/issues_002.md` reported something much bigger:
  the XML actually loaded into the real Nanxing NaccNesting software (not just checked against
  historical golden files) showed every job's layout confined to the board's *width*, with the
  real *length* almost entirely empty — 4 screenshots of the real machine software attached.
  Traced it to `optimizer/export/xml.py` writing `PlacedPart.x`/`.y` straight into the XML's
  `X`/`Y` with no transform, when the packer's `x` is board.width-bounded and `y` is
  board.length-bounded — the *opposite* of the machine's own X=length/Y=width convention. The
  golden-file round-trip suite never caught this because its own importer (`tests/fcc_golden.py`)
  made the identical backwards assumption on import, so round-tripping golden data cancelled the
  bug out both ways — it only manifests when real packer output (i.e. every actual user export)
  flows through, which the round-trip test structurally never exercised. Fixed by transposing
  both the exporter and the golden-file importer consistently; see M11 row for the full
  detail and the real coordinate numbers confirming it. (8) `Updates/update_005.md` asked for a
  fourth Summary stat ("Panels/Parts cut") — small, done, see M7 row. Separately, after M11
  landed, the project owner asked for post-M11 feature suggestions and a phased plan; that plan
  now lives in `ROADMAP.md` (not duplicated in this file), and its **Phase 1 is done**: a
  cut-sequence view (`CutList.tsx`, rendering `OptResult.cuts` — previously computed but never
  shown anywhere), a loading spinner + status text for the optimize request, and shared
  search/sort table controls (`useTableControls` hook, `SortableTh` component) applied to the
  unplaced-parts and Stock Board Library tables. Verified in a real headless browser against the
  656-part reported job. (9) The project owner asked for the cut-sequence view to also be drawn
  visibly, not just listed as a table — "cut-lines should also be visible in Panel Saw drawings,"
  meaning both the SVG preview and the PDF. Added as an extension of the already-done Phase 1
  (`ROADMAP.md` updated in place rather than opening a new phase). `SheetPreview.tsx` now renders
  each `CutInstruction` as a red dashed SVG `<line>`, gated on `CutList.tsx`'s (now controlled,
  `open`/`onToggle`) disclosure state instead of always-on clutter or a second toggle. The PDF
  side needed one real backend change: `optimizer/export/pdf.py`'s `render_layout_pdf` gained a
  `margin: Margin | None = None` parameter (defaults to zero margin, so existing callers/tests
  were unaffected) and a `_draw_cut_lines`/`_cut_line_bounds` pair that draws the same lines onto
  the board diagram using the existing `scale`/`origin_x`/`origin_y` transform already used for
  parts — `CutInstruction.offset` already bakes in `margin.left`/`.top` (`build_cuts_for_sheet`),
  but `.length` is only a span, so margin is what supplies the line's missing start coordinate on
  the other axis; the frontend's `cutLineBounds()` mirrors this exact logic. `api.py`'s
  `/export/pdf` now threads `margin` through. Nanxing PDFs are unaffected by construction —
  `optimizer/nanxing.py` never populates `OptResult.cuts` (confirmed by reading it directly), so
  there's simply nothing for the overlay to draw there. Covered by `backend/tests/test_pdf.py`
  (+4 tests, suite 109→113): `_cut_line_bounds()` unit tests for both orientations (verified they
  have teeth — reverted the margin-offset logic, reran, both failed as expected, restored), an
  integration test running a real saw job's cuts through `render_layout_pdf`, and a
  no-margin-argument backward-compatibility test. Verified end-to-end in a real headless browser
  against `panel_saw_machine_data.csv` (21 sheets): lines hidden by default, expanding Sheet 1's
  cut list renders exactly 17 `<line>` elements matching its 17 cut rows 1:1, collapsing hides
  them again, zero console errors. Also rasterized a real generated PDF page (`pdftoppm`) and
  visually confirmed the red dashed lines run exactly along the true guillotine cut boundaries
  between the colored panels — this project's established practice of eyeballing rendered PDF
  output rather than trusting geometry math alone (see M3's own sidebar-overlap bug, caught the
  same way). `tsc -b`/lint/build all clean. See `ROADMAP.md`'s Phase 1 section for the fuller
  writeup. (10) `Issues/issues_003.md` reported that the cut-line overlay just added in pass (9)
  actually confused panel-saw operators on some real layouts — a screenshot showed a dense sheet
  where the dashed cut grid was hard to distinguish from the part boundaries. Rather than remove
  the feature, added a **"Show cut lines"** checkbox (Saw section of `ParamsPanel.tsx`, default
  enabled at the time — **flipped to disabled by default in pass (11) below**) so it's toggleable
  per job. Backend: `render_layout_pdf` gained a
  `show_cut_lines: bool = True` parameter that swaps in an empty per-sheet cuts list for the
  board drawing when off — deliberately narrow: the numbered cutting list and the "Cut Length"
  stats in the header stay populated either way, only the drawn dashed overlay is suppressed,
  since those numbers were never the confusing part. `api.py`'s `/export/pdf` reads
  `showCutLines` from the request body. Frontend: `SheetPreview.tsx`'s SVG line rendering is now
  gated on `showCutLines && expanded` (global setting AND the per-sheet disclosure — renamed from
  `showCuts` to `expanded` for clarity now that "show cuts" is ambiguous between the two), while
  `CutList`'s own open/close state stays independent of the global setting. Covered by 2 new
  backend tests (115 total, up from 113): one checks for the literal ReportLab dash-pattern
  operator (`[3 2] 0 d`) in the PDF's decoded content stream — present when the setting is on,
  absent when off, a precise signal that lines were actually drawn rather than just "the page
  didn't crash" — and one confirms "Sheet Cut Length :" text still appears with the setting off.
  Verified the dash-pattern test has teeth: temporarily removed the gate, reran, it failed as
  expected, restored. Verified end-to-end in a real headless browser: checkbox defaults to
  checked; unchecking it and rerunning produces zero `.cut-line` SVG elements even with the cut
  list expanded; the downloaded PDF with the setting off was rasterized (`pdftoppm`) and visually
  confirmed clean (no dashed overlay) while the sidebar's Cutting List/stats remained. `tsc -b`/
  lint/build all clean. (11) Asked directly to flip the "Show cut lines" default to **off**.
  Changed in three places to keep them consistent: `frontend/App.tsx`'s
  `useState(true)` → `useState(false)`; `api.py`'s `request.get("showCutLines", True)` →
  `False` (so a caller that omits the field entirely gets the same behavior as the UI); and
  `render_layout_pdf`'s own `show_cut_lines: bool = True` → `False` default in
  `optimizer/export/pdf.py`, so the function's default matches the app's rather than silently
  disagreeing with it. One existing test (`test_render_layout_pdf_accepts_margin_and_draws_
  cut_lines_without_crashing`) had been relying on the old implicit `True` default to exercise
  the overlay-drawing path without asserting on it directly — updated to pass
  `show_cut_lines=True` explicitly so it still tests what its name says. Added a new test
  locking in the default itself: calling `render_layout_pdf` with no `show_cut_lines` argument
  must byte-for-byte match calling it with `show_cut_lines=False`. Verified it has teeth:
  temporarily reverted the default back to `True`, reran, failed as expected, restored. Full
  suite: 115→116 passing (net +1: one new default-lock-in test, no others added or removed this
  pass). Verified end-to-end in a real headless browser: the checkbox is unchecked on page load,
  running a job and expanding a sheet's cut list draws zero `.cut-line` elements, and the PDF
  still downloads successfully with the setting at its new off default. `tsc -b`/lint/build all
  clean. (12) Asked to implement `ROADMAP.md`'s Phase 2 (data visualization) — three frontend-only
  items, all reading data `/optimize` already returns, no new endpoint needed. No charting
  library exists in `frontend/package.json`, and `SheetPreview.tsx` already renders its board
  diagrams as raw SVG, so all three follow that same lightweight-custom-SVG pattern rather than
  adding a dependency. **`UtilizationChart.tsx`**: one bar per physical sheet in job order
  (deliberately not deduplicated the way the PDF's "Occurrences" view is — collapsing duplicates
  would hide exactly the "which specific sheet is underutilized" signal the chart exists for),
  threshold-colored (`--success` ≥70%, `--accent` 40–70%, `--warning` <40%), horizontally
  scrollable for large jobs. **`MaterialBreakdown.tsx`**: groups sheets by material, shows each
  material's sheet count and average waste % as a horizontal bar, sorted worst-waste-first;
  skips rendering for single-material jobs since it would just repeat the Summary's "Avg.
  utilization" stat. **`WasteStrategyComparison.tsx`**: a button offering to re-run `/optimize`
  with the *exact* current request except `wasteStrategy` flipped — an apples-to-apples
  comparison against the same parts/stock/margins/target, not a fresh job — then shows
  Sheets/Avg. utilization/Unplaced side by side for both; lazy, only fires on click. All three
  wired into `App.tsx`'s results view between the download actions and the per-sheet SVG grid.
  New `.chart-card`/`.material-row*`/`.strategy-compare*`/`.muted` CSS in `App.css`, entirely
  built from the existing CSS custom properties (`--success`/`--accent`/`--warning`/`--border`/
  `--text-muted`/`--text-h`) rather than hardcoded colors, so dark mode needed no separate work.
  Verified in a real headless browser against two real jobs: the 656-part multi-material
  reported job (`26Y117T1F1B1(BEDROOM 3-4)`, 67 sheets after M10/M11) — utilization chart's bar
  count matched the Summary's "Sheets" stat exactly, material breakdown showed all 5 real
  materials sorted by waste descending, and the strategy comparison populated both columns with
  real numbers after clicking — and `panel_saw_machine_data.csv` (21 sheets, 2 materials, lower
  utilization on later sheets) — confirmed all three utilization color tiers render distinctly.
  Also screenshotted in dark mode (Playwright `colorScheme: "dark"`) to confirm the CSS-variable
  approach actually held, not just assumed. Zero console errors either job. `tsc -b`/lint/build
  all clean. See `ROADMAP.md`'s Phase 2 section for the fuller writeup. (13) Asked to implement
  `ROADMAP.md`'s Phase 3 (presets & cost tracking) — the first phase to touch the backend, but
  by design reusing M9's exact stock-boards CRUD pattern (SQLite table + `GET`/`POST`/`PUT`/
  `DELETE`) rather than new architecture. **Named parameter presets**: a new `presets` table
  (`backend/storage.py`) storing everything `ParamsPanel.tsx` controls except stock/parts
  (target, margin, kerf, tool Ø, part spacing, allow-rotation, waste strategy) — stored flat
  (matching `stock_boards`' existing column convention) but exposed over the wire with `margin`
  nested as `{top,right,bottom,left}` like the rest of the JSON contract; two new `api.py`
  helpers (`_preset_to_dict`/`_preset_kwargs_from_payload`) are the only place that reshapes
  between the two. New `PresetLibrary.tsx` deliberately has no separate add-form fields the way
  `StockBoardLibrary.tsx` does — a preset just names and persists whatever's already set in
  `ParamsPanel.tsx`, so saving only asks for a name. **Cost-per-board tracking**: `cost_per_board`
  column added to `stock_boards` via a real `ALTER TABLE` migration (`storage._migrate()`) since
  a pre-existing local DB file won't retroactively gain a column from `CREATE TABLE IF NOT
  EXISTS` — verified directly against this session's own real dev DB file, not just a fresh one.
  Cost is deliberately display-only and never reaches the optimizer: the backend's core
  `StockBoard` dataclass (used by `/optimize`/`/export/*`) is untouched, and the frontend's new
  `StockBoardWithCost` type is a strict superset that `App.tsx`'s `currentRequest()` strips back
  down to plain `StockBoard` before any backend call, since the dataclass would reject the
  unexpected field outright. New `frontend/src/costUtils.ts` computes material cost and waste
  cost (board cost × wasted fraction) purely client-side, matched by `(material, thickness)`;
  both figures hide entirely rather than showing "$0.00" when no board has a cost entered, so
  "not configured" is never confused with "confirmed zero waste." Surfaced in `Summary.tsx` (a
  new stat row) and Phase 2's `MaterialBreakdown.tsx` (a per-material `$` figure). Covered by 18
  new backend tests (134 total, up from 116): `test_storage.py` + `test_api_persistence.py` CRUD
  and validation tests for both features — verified the preset validation tests have teeth by
  temporarily gutting `_validate_preset_fields`, rerunning, confirming both failed, restoring.
  Verified end-to-end in a real headless browser: added a stock board with a cost to the library,
  set a cost directly on the job's own stock table, saved current parameters as a named preset,
  changed the kerf, clicked "Use" on the preset, confirmed kerf reset to the saved value, ran
  optimize and confirmed both new Summary stat cards showed real dollar figures. One real, if
  minor, finding along the way: a transient `500` from `GET /presets` turned out to be a **stale
  long-running `uvicorn --reload` process** (running for hours across many hot-reloads this
  session) rather than an application bug — confirmed by querying the same DB file directly via
  `storage.py` and via a fresh in-process `TestClient` (both returned correct data), then
  restarting the dev server, after which the live endpoint also returned correctly; not a code
  fix, just a dev-environment artifact worth knowing about for future sessions with a
  long-running backend. `tsc -b`/lint/build all clean. See `ROADMAP.md`'s Phase 3 section for the
  fuller writeup. (14) Asked to change currency to **₹** and make the cost unit selectable
  (₹/board or ₹/sqft, "more units we can add later"). `stock_boards`' `cost_per_board` column
  renamed to `cost` plus a new `cost_unit` column, via a real migration handling both a DB that
  predates cost entirely and one still on the single-unit `cost_per_board` column from earlier in
  pass (13) — verified against both starting states, including confirming real pre-migration data
  survives the rename. `VALID_COST_UNITS`/frontend `CostUnit` are plain extensible
  tuples/unions on purpose, so a future unit is a one-line addition, not a schema change.
  `costUtils.ts` gained a normalization step: ₹/sqft is scaled by the sheet's own area before
  entering the same cost sums Summary/MaterialBreakdown already had; ₹/board passes through
  unchanged. **A real, unrelated bug was found and fixed along the way**: re-verifying end-to-end
  surfaced an intermittent `sqlite3.ProgrammingError: SQLite objects created in a thread can only
  be used in that same thread` on `GET /presets` — this time against a *freshly restarted*
  backend, ruling out pass (13)'s stale-reload explanation. Root cause: `api.py`'s `get_db()` is a
  sync generator dependency, and FastAPI runs sync dependencies/endpoints via anyio's threadpool,
  which doesn't guarantee the connection's creation and its use land on the same OS worker
  thread — a latent issue since M9 first introduced SQLite, not something this pass introduced,
  just exposed by unlucky thread scheduling. Fixed with `check_same_thread=False` on
  `storage.get_connection()`'s `sqlite3.connect()` call — safe since a connection is still only
  ever driven by one thread at a time in sequence, never concurrently. New regression test
  creates a real-file connection, queries it from a second `threading.Thread`, asserts no
  exception — verified it has teeth (reverted, reran, reproduced the exact real error message,
  restored). Full suite: 136→137. Verified end-to-end in a real headless browser against a freshly
  restarted backend: added a ₹/sqft-priced board and confirmed the formatted rate; confirmed the
  job stock table's per-row unit selector defaults to ₹/board; ran optimize and confirmed both
  Summary cost stat cards show ₹ with no stray `$`; zero console/network errors, including the
  specific `GET /presets` call that had failed before the fix. `tsc -b`/lint/build all clean. See
  `ROADMAP.md`'s Phase 3 section for the fuller writeup. (15) `Updates/update_006.md` asked for
  a new capability, not a roadmap phase item: "ability to load optimization from existing
  Nanxing nesting xml file." Rather than write a new parser, `backend/tests/fcc_golden.py` —
  a test-only helper written back in the M5/M6/M11 XML work to feed a real machine-cut FccRoot
  XML into `generate_fcc_xml` for round-trip testing — turned out to already do almost exactly
  this: parse a real golden XML straight into `OptResult`-shaped sheets/placed-parts, bypassing
  the optimizer entirely. Promoted that logic into a new production module,
  `optimizer/import_xml.py` (`parse_fcc_xml`), with `fcc_golden.py` now a thin wrapper around it
  instead of a duplicate copy — so a future refinement to the XML-format understanding benefits
  the test fixture and the real feature together rather than the two silently drifting apart. Two
  real gaps closed on the way: `utilizationPct` had been hardcoded to `0.0` (fine for a
  byte-comparison round-trip test that never displayed it, wrong for a real viewer) — now
  computed with the exact same formula `saw_packing.py`/`nanxing_packing.py` use (usable area =
  board dims minus margin, not raw board area) — and malformed/wrong-root-element input now
  raises a clean `InvalidFccXmlError` instead of a raw `AttributeError`/`TypeError` traceback.
  New `POST /import/xml` (`api.py`) accepts raw XML text (mirroring `/parse`'s existing
  `csv_text` embed-body convention) and returns the same sheet shape `/optimize` does, plus the
  file's own margin/tool-diameter/part-spacing and a stock list derived from the sheets'
  (material, thickness) pairs — deliberately omits `parts`/`cuts`, since this app has no way to
  re-export a *given* result (`/export/*` always re-runs the optimizer from `parts`+`stock`+
  params, it doesn't re-serialize an already-placed layout) and returning `parts` would invite a
  broken "Adjust parameters → Run optimize" path that silently produced a different layout than
  the one actually loaded. Frontend: new `XmlImport.tsx` (a plain file-picker button, not a
  `CsvUpload.tsx`-style dropzone — this is a secondary, less-common path) sits below the CSV
  upload card; picking a file jumps straight to the results view, skipping map/configure
  entirely, with a new `isImported` flag in `App.tsx` gating off Download PDF/XML, "Adjust
  parameters," and the waste-strategy comparison (all three would otherwise silently run against
  an empty `parts` list) behind a plain explanatory note — `SheetPreview`/`Summary`/
  `UtilizationChart`/`MaterialBreakdown` all still work unchanged, since they only ever needed
  `OptResult`+`stock`+`margin`, never `parts`. Covered by 24 new backend tests (161 total, up
  from 137): `test_import_xml.py` (real-golden-file parsing across all 4 non-empty golden files,
  the one legitimately-empty golden file per M5's own Appendix A.8 degenerate case, and the new
  error paths) and `test_api_import.py` (HTTP-level via `TestClient`) — verified both new error
  paths have teeth by temporarily removing each check, rerunning, confirming the raw
  traceback/wrong-shape response returned instead of the clean 400, restoring. Verified
  end-to-end in a real headless browser against a freshly restarted backend, importing a real
  golden XML (`26Y111T1F1 (1 FLOOR BEDROOM)`): jumped straight to results with the file's real 9
  sheets / 42.4% utilization / 55 parts, the imported-layout note appeared, Download/Adjust/
  Compare were all correctly hidden, the utilization chart still rendered, and "Start Over"
  correctly reset back to the upload step. Zero console/network errors. `tsc -b`/lint/build all
  clean. (16) `Issues/issues_005.md` reported the **first real dry-run cut of this app's own XML
  output**: a demo job (3 identical 210×297mm parts, grain=none, `nesting_machine_data_simple.csv`)
  was cut, and the physical result measured 290–291mm on the width dimension instead of the
  required 297mm (length measured correct at 210mm) — a ~6–7mm shortfall on one axis only. Also
  attached: the machine's own inbuilt-optimizer output for a comparable job, and screenshots of
  both files loaded in the machine's own software. Investigated thoroughly rather than guessing —
  hand-traced exactly what this app's packer and `xml.py` produce for this job (`_footprint()`
  picks `rotated=True` for these grain=none, `cutWidth>cutLength` parts, giving
  `MachiningPoint="7"`), then found a **real golden workpiece with the identical characteristics**
  (`26Y111T1F1B3_1089` in `26Y111T1F1 (1 FLOOR BEDROOM)-FccForNesting-FccPattern.xml`: grain=N,
  CutWidth>CutLength, RotateAngle=90, MachiningPoint=7) and verified **by hand, byte-for-byte**,
  that this app's `Lineament` bounds, `FccOutline`, and `ToolPointList` formulas reproduce that
  real machine-cut workpiece's actual values exactly — confirmed this exact geometry class is
  also continuously covered by the existing `test_xml_roundtrip.py` suite (17/17 passing, all
  green), which round-trips that same golden file including this exact workpiece. A separate
  hypothesis raised mid-investigation — that the `Margin` XML attribute might need the same X/Y
  axis transpose M11 applied to every other geometry element — was checked against real data
  (cross-referencing an asymmetric-margin golden file's actual placement bounds against its own
  declared `Margin` string) and **disproven**: the `Margin` attribute's convention is independently
  established as NOT transposed (top/bottom↔length axis, left/right↔width axis, matching what
  this app already emits), unlike every *geometric* element. Retracted rather than "fixed" a
  non-bug based on incomplete reasoning — consistent with this project's standing rule to verify
  against real data before changing code, not just theorize. **Conclusion: no bug found in this
  app's XML export for this scenario** — the exported geometry is provably correct and matches
  the real machine's own established file format exactly, for the *precise* combination of grain/
  rotation/dimension-ordering the reported job hit. A uniform tool-diameter/kerf error would
  shrink both axes equally, not one only, and the axis mapping itself is proven correct, so the
  reported defect doesn't match anything explicable from the file content. Reported back to the
  project owner with this evidence and a concrete next diagnostic step: physically cut the
  attached machine-inbuilt-optimizer reference file's own 297×210 parts and measure them too — if
  *that* also comes out ~290mm on the same axis despite being generated by entirely different
  software, that would conclusively point to a physical/machine-side cause (tool wear, axis
  calibration/backlash — specifically whichever physical axis corresponds to board width, since
  only that axis is affected — or board squareness/clamping during this specific cut) rather than
  anything in nesting-pro. No code changed this pass. (17) Followed up on pass (16)'s
  investigation: since the two screenshots showed nesting-pro's demo layout sitting in the
  board's bottom-left corner while Nanxing's own inbuilt optimizer chose the opposite (top-right)
  for a comparable job, the project owner wanted to test whether *table position* (vacuum-zone
  coverage, axis calibration, fence distance) rather than software explains the reported
  shortfall. Discussed the cleanest way to test this: reusing Nanxing's own inbuilt optimizer
  would confound the test (changes *which software* generated the file *and* where it lands, at
  the same time) — the clean version keeps nesting-pro's own packing decisions untouched and only
  changes where the resulting layout sits on the board. Neither app supported repositioning an
  already-generated layout, so this needed a real feature: added a **"Board corner"** dropdown
  (all 4 corners, per explicit request over a simpler 2-way toggle). New `optimizer/placement.py`
  (`mirror_sheet`) is a pure post-placement coordinate reflection — deliberately *not* part of
  either packing engine (`saw_packing.py`/`nanxing_packing.py` stay un-shared per `update_003.md`)
  since it never influences which parts get placed where *relative to each other*, only where the
  whole already-decided layout sits on the board; sharing it across both machine targets doesn't
  touch the "two machines = two different optimizers" principle. `"bottom-left"` (the packer's
  native fill origin) is a no-op; the other three corners reflect across the width axis
  (`PlacedPart.x/w`), the length axis (`PlacedPart.y/h`), or both — derived directly from the
  same axis convention the two "Work area" screenshots showed (X=horizontal=length, Y=vertical=
  width, matching M11). Applied in `guillotine.py`/`nanxing.py`'s `optimize()` *after* placement
  decisions are final but *before* `build_cuts_for_sheet` runs, so saw cut lines stay consistent
  with wherever the mirrored parts actually land. Threaded through `/optimize`, `/export/pdf`,
  `/export/xml` (`placementCorner`, defaulting to `"bottom-left"` so existing callers/tests are
  unaffected) and a new dropdown in `ParamsPanel.tsx`'s (renamed) "Layout" section, alongside
  waste placement — deliberately *not* added to the Preset bundle, matching the existing
  precedent that `showCutLines` (also display/diagnostic-oriented, not a "production" nesting
  parameter) isn't saved in presets either. Covered by 18 new backend tests (179 total, up from
  161): `test_placement.py` unit-tests `mirror_sheet()`'s exact coordinates for all 4 corners
  (including offcuts) and re-runs the existing saw/nanxing invariant checks — no overlaps, within
  margin bounds, still guillotine-decomposable for saw — parametrized across all 4 corners on
  real sample data, plus a real-data check that cut lines stay within board bounds after
  mirroring; `test_api_optimize.py` adds the first HTTP-level tests for `/optimize` (previously
  only exercised through the underlying `optimizer/` functions, per the M9/M11 rows' own noted
  gap) confirming the parameter threads through end-to-end. Verified the mirror-math tests have
  teeth: temporarily broke the length-axis mirror condition, reran, 3 tests failed exactly as
  expected (including reproducing the wrong coordinate values), restored. Verified end-to-end in
  a real headless browser against a freshly restarted backend: switched to Nanxing, confirmed the
  dropdown defaults to "Bottom-left", selected "Top-right", reran, confirmed the SVG preview's
  placed-part coordinates changed, downloaded the resulting XML, and directly inspected its real
  machine-frame coordinates — parts landed at X≈2217–2433 of 2440 (length) and Y≈910–1213 of 1220
  (width), i.e. genuinely in the board's top-right by the machine's own convention, not just
  "moved somewhere." One cosmetic note for future reference, not a bug: this app's own
  `SheetPreview.tsx` renders board.length *vertically* with an unflipped SVG y-axis (a
  pre-existing, already-documented choice — see M3's "Current state" note on portrait vs the
  machine's landscape convention), so a layout picked as "top-right" for the *machine's* screen
  convention visually renders nearer the *bottom*-right in this app's own in-browser preview —
  confirmed this is the SAME pre-existing orientation difference already on record, not something
  this pass introduced. `tsc -b`/lint/build all clean. (18) Follow-on discussion on pass (17)'s
  investigation: the project owner surfaced a real, likely-stronger explanation for
  `issues_005.md`'s shortfall — shop operators routinely trim ~7mm off a board edge before
  cutting, to remove rough factory edges, and 7mm matches the measured discrepancy almost
  exactly. `Margin` already models exactly this ("reserve N mm from an edge, place nothing
  there"), so no backend change was needed — just correctly identifying which of the 4 margin
  fields to bump, which motivated a small, purely-frontend UI request: `ParamsPanel.tsx`'s
  Margin section changed from a flat row of 4 identically-styled inputs to a spatial "compass"
  layout (top input above, bottom below, left/right on either side of a labeled placeholder
  "Board" box in the center) matching a reference diagram the project owner shared, so it's
  visually unambiguous which input governs which physical edge. New `.margin-layout*` CSS in
  `App.css` (a 3-column/3-row grid, each field explicitly positioned by `grid-column`/
  `grid-row`), plus a mobile fallback that stacks Top → Board → Left → Right → Bottom in the
  existing `@media (max-width: 640px)` block. Verified in a real headless browser: confirmed
  each field's bounding box is spatially correct relative to the center "Board" box (top above,
  bottom below, left left-of, right right-of), confirmed editing a field still updates state
  correctly, and screenshotted both dark mode and a narrow mobile viewport to confirm the layout
  and its fallback both render cleanly. `tsc -b`/lint/build all clean. No backend changes, no
  test count change. (19) Asked to also show, per material, how many panels were cut from it and
  how many boards of that type were used — the board count already existed in
  `MaterialBreakdown.tsx` (Phase 2, ROADMAP.md) as "N sheets," relabeled to "N boards" to match
  the request's wording; added a new per-material panel (parts) count alongside it. Purely
  frontend, no backend change. Verified in a real headless browser against
  `panel_saw_machine_data.csv` (2 materials) — correct singular/plural, and, more importantly,
  the per-material panel counts sum to 50 and board counts sum to 21, exactly matching the
  Summary card's own job-wide totals for the same run. `tsc -b`/lint/build all clean. **Found,
  not fixed:** the backend test suite currently has 32 errors + 4 failures, all from
  `sample_data/`'s golden-XML folder having been renamed (`XML Data for Nanxing Nesting
  Machine/` → `XML Data from Fin China/`) without the test fixture paths being updated —
  unrelated to this pass's change, flagged rather than fixed since it wasn't part of what was
  asked; see "Remaining work" below. See `ROADMAP.md`'s Phase 2 section for the fuller writeup.
  (20) A screenshot showed `.material-row__label` (in `MaterialBreakdown.tsx`, pass 12/19) being
  cut off with an ellipsis (`"CP_MDF17_8173_OS_8…"`) — the project owner flagged not being able
  to tell which material a row was for. Root cause: `.material-row`'s grid gave the label a
  fixed `minmax(0, 10rem)` column with `text-overflow: ellipsis`/`white-space: nowrap`, too
  narrow for this app's real material codes (`CC_HDH17_ANY_CL_BS`, `CP_MDF17_8173_OS_8134`,
  etc). Fixed by restructuring `.material-row` into two grid rows: the label now spans the full
  row width on its own line (`grid-column: 1 / -1`, `white-space: normal`, no more
  ellipsis/nowrap), with the waste bar/value/board-count/panel-count on the row below — matches
  the density of the existing mobile breakpoint's single-column fallback, which needed no
  further change. Verified in a real headless browser against the real 656-part, 5-material
  reported job (the same one used in pass 12's original verification): all 5 material labels —
  including the two longest, `CC_HDH17_ANY_CL_BS` and `CP_MDF17_8173_OS_8134` — render in full
  with computed `text-overflow: clip` (not `ellipsis`) and `scrollWidth === clientWidth`
  (nothing clipped), screenshot confirms bold full names on their own line above each bar,
  zero console errors. `tsc -b`/lint/build all clean. Purely CSS, no component/backend change.
  (21) A real physical workpiece label (two photos: `RE_75633_PINE_NUT_2MM` edge-band header,
  `Drawer_Front_With_G_profile` parts) showed `C.S.` (Cutting Size, `CutLength`/`CutWidth`)
  printing correctly but `F.S.` (Final Size, for the edge-banding team downstream of cutting —
  not used by the machine's own cutting) printing as a bare `x` with nothing on either side.
  Investigated by cross-referencing a real CSV (`nesting_machine_data.csv`) against its real
  machine-cut golden XML by shared barcode (all 55 matched) rather than guessing: confirmed the
  machine's `Workpiece.Info1`/`Info2` attributes — present on every real golden workpiece, but
  flagged since M5/M6 as "no consistent relationship to Length/Width/CutLength/CutWidth found,
  not worth fabricating" — actually hold the *source CSV's own* `Lenght`/`Width` columns
  exactly (`part.finishedLength`/`finishedWidth`), 55/55 exact, no rotation dependency; this
  app's exporter never emitted them at all, which is exactly why the label came up blank. Fixed
  by adding `Info1`/`Info2` to `_workpiece_element` (`optimizer/export/xml.py`), sourced from
  `finishedLength`/`finishedWidth` — confirmed the project owner wanted cutting untouched
  first (`AskUserQuestion` + direct discussion), so `CutLength`/`CutWidth` (which drive the
  actual toolpath and were already correct) were not touched. **Found, not fixed, along the
  way:** this app's existing `Length`/`Width` attributes (also sourced from
  `finishedLength`/`finishedWidth`) don't match real golden data either — real `Length`/`Width`
  is `CutLength + 6.0`/`CutWidth + 6.0` exactly across all 1039 real workpieces checked, a
  fixed constant unrelated to edge-band type. Left alone since nothing reported depends on it
  and the project owner asked to scope this to just the F.S. fix — flagged in "Remaining work"
  below. Also left `optimizer/import_xml.py`'s importer alone (it still reads
  `finishedLength`/`finishedWidth` from the XML's `Length`/`Width` on the way in, not the more
  correct `Info1`/`Info2`) since fixing it isn't needed for the reported issue and interacts
  with the golden-file round-trip test's existing `Length`/`Width` comparison — also flagged
  below rather than touched unprompted. New regression test
  `test_real_job_info1_info2_match_golden_finished_size` (`test_xml_export_coordinates.py`,
  +1 test) runs the real sample CSV through the actual parser → Nanxing packer → exporter path
  and asserts the exported `Info1`/`Info2` match the real golden file's own values for all 55
  shared barcodes, not just a synthetic case. Verified against real data twice: once via a
  standalone script (confirming the fix before writing the test) and once via the new pytest
  test itself, both 55/55 exact. Full suite: 184 passed, 0 failures (temporarily verified via a
  local, untracked symlink working around the still-unfixed stale `sample_data` path noted in
  "Remaining work" #4 below — removed after verifying, not committed). No frontend changes.
  (22) Real NaccNesting screenshots (same session as pass 21) showed the machine's own
  printed dimension label on-screen was sometimes visibly mismatched from the actual rendered
  rectangle — e.g. a real reported part (`26Y118T1F1A1_1246`, "Adjustable Shelf",
  `CutLength=1013.8`, `CutWidth=528.8`, `grain="none"`) showed `1013.8` printed along what was
  visually the *shorter* edge of the drawn rectangle. Root-caused through direct discussion,
  not guesswork: `nanxing_packing.py`'s best-short-side-fit placement had zero preference for
  which physical board axis (length vs width) carried a part's `CutLength` — it only minimizes
  leftover slack, so it happily places `CutLength` along the board's *width* axis whenever that
  packs tighter. Confirmed directly against real code+real CSV data for the exact reported
  part: on an empty board it chose exactly that "sideways" placement. The project owner's own
  diagnosis (their words: "the machine always sets the label along the length") supplied the
  missing piece — NaccNesting's label rendering apparently always assumes `CutLength` sits
  along the horizontal/length-axis position regardless of a part's actual `RotateAngle`
  (our own exported geometry + `RotateAngle` stay internally self-consistent either way, already
  verified byte-for-byte in earlier passes — this is a mismatch between our packer's placement
  *choice* and the machine's *label assumption*, not a geometry bug). Fixed by giving
  `place_parts_on_board` a **preference** (not a hard constraint) for whichever orientation
  keeps `CutLength` on the local-y (board.length-derived) axis, tried first across all free
  rectangles; only falls back to the other orientation when the preferred one fits nowhere — so
  nothing that used to fit can become unplaced. Grain-locked parts already satisfied this by
  construction (`can_rotate()` is `False` for them, so there's only ever one orientation to try,
  and it already keeps their grain-mandated axis correct — no change needed there). Scoped to
  `nanxing_packing.py` only, confirmed with the project owner first — `saw_packing.py`'s
  identical characteristic was deliberately left alone since the panel saw's PDF labels are
  rendered by this app's own `_nominal_dims()`, not a machine-side assumption, so it has no
  equivalent symptom. **Measured the efficiency impact directly rather than assuming**, per the
  project owner's explicit question ("will it make the optimization worse?"): ran before/after
  comparisons on 3 real jobs (the reported 11-part material group, the 55-part
  `nesting_machine_data.csv` sample, and the 656-part `26Y117T1F1B1(BEDROOM 3-4)` reported job)
  — identical sheet counts and identical combined utilization in all three, before and after;
  the preference only changed *which* sheet absorbed the slack on the 11-part case, not the
  total. Two new regression tests in `test_packing_engines.py` (186 total, up from 184):
  `test_nanxing_prefers_cutlength_on_length_axis_when_both_orientations_fit` (the exact real
  reported part, asserts the new placement) and
  `test_nanxing_preference_falls_back_when_preferred_orientation_does_not_fit` (a precisely
  constructed board/part combination — verified against the real code before writing the
  assertion, not guessed — where the preferred orientation genuinely doesn't fit and the
  fallback must engage, proving the "never unplace a previously-placeable part" guarantee
  holds). Full suite: 186 passed (temporarily verified via the same untracked symlink as pass
  21, removed after). No frontend changes, no PDF/saw changes. Both this fix and pass 21's F.S.
  fix are also logged in `To DOs.md`'s Bug Fixes section per the project owner's preference for
  that file as the actionable list (see that file directly for the terse version).
  (23) The project owner shared real screenshots and, critically, two real XML files for the
  *exact same job* — one generated by this app, one by Fin China's own optimizer, plus the
  source CSV — dropped into `results/26Y118_data/`. Investigated with real tooling
  (`grep`/`python3 -re`, not eyeballing two ~8,000-line files) rather than trying to manually
  diff them, after an earlier attempt at manual comparison correctly flagged its own unreliability
  and asked the project owner for real files instead of guessing. Extracted the exact same
  workpiece (`26Y118T1F1A1_1198`, `CutLength=720.4`/`CutWidth=318.4`, grain-free) from both real
  files with a small Python/regex script and compared `RotateAngle`/`MachiningPoint`/the
  `Lineament` polygon's actual X/Y span — this is what found pass 22's fix to be an incomplete
  mitigation, not the root cause (see item 1 in "Remaining work" above for the full 3-part
  evidence trail: geometry mismatch, `MachiningPoint` mismatch, and why the one previously-
  correct part only worked by two backwards conventions cancelling out). Also regenerated this
  exact real job through today's actual code (post pass-22-fix) and confirmed the same part
  still comes out wrong — proving pass 22's preference-ordering fix, while directionally
  correct and worth keeping, doesn't fully close the gap on its own. The project owner then
  asked directly whether this was the same thing as the "allow rotation" toggle, and reported
  toggling it off produced the same wrong result — which turned out to be the key diagnostic:
  with rotation off, the packer is forced into its one available orientation, which was already
  the backwards one, so there was never a second orientation for it to fall back to. This
  confirmed the real fix has to change what "unrotated" *means* for grain-free parts (in
  `_footprint()`), not just which orientation gets tried first. Proposed the concrete fix
  (`natural_swap = part.grain != "width"` instead of `part.grain == "length"`) and discussed it,
  but the project owner asked to log it rather than implement immediately — added as item 1 in
  "Remaining work" (max priority, set directly by the project owner) and in `To DOs.md`'s Bug
  Fixes section, not yet implemented. No code changed this pass — investigation and
  documentation only.
  (24) Asked directly to implement pass 23's max-priority fix ("Ok, implement it.") — see item 1
  in "Remaining work" above for the full writeup: `natural_swap = part.grain == "length"` →
  `natural_swap = part.grain != "width"` in `nanxing_packing.py`'s `_footprint()`. Verified
  against the same two real `results/26Y118_data/` parts pass 23 used: part `1246` now matches
  Fin China byte-for-byte (`RotateAngle`/`MachiningPoint`, not just geometry); part `1198` still
  doesn't, but this time diagnosed rather than left unexplained — a temporary debug trace
  (added, checked, fully reverted before committing anything) confirmed the sheet it lands on
  simply has no free rectangle tall enough for the corrected orientation (needs 726.5mm, best
  available is 666.5mm), so pass 22's existing preference logic correctly falls back rather than
  leaving it unplaced — exactly the "preference, not a hard constraint" behavior that was always
  documented, now confirmed with a concrete real trace instead of assumed. Raised, not resolved,
  a genuine open question this surfaced: whether that preference should become a hard constraint
  to guarantee 100% label-matching at some efficiency cost — left for the project owner to
  decide (see item 1). Updated the two pass-22 tests in `test_packing_engines.py` that assumed
  the old (backwards) convention, plus split the old `test_footprint_none_grain_unaffected_by_
  the_fix` (which had been parametrized across both packers) into a saw-specific test (unchanged
  assertions — confirmed `saw_packing.py` is genuinely unaffected, not just untouched) and a new
  nanxing-specific test with the corrected assertions. Full suite: 186 passed, 0 regressions
  (verified via the same untracked-symlink workaround as passes 21–23 for item 4 below, removed
  after). Re-measured efficiency on the same 3 real jobs pass 22 used, this time via `git stash`
  of just the one changed line to get a true before/after on identical code otherwise — sheet
  counts and combined utilization percentages came out identical before and after in all three,
  confirming this fix only relabels which `rotated` value is chosen for placements that were
  already happening, it doesn't change what fits. No frontend changes.
  (25) A follow-up real screenshot (`results/030920261114/`) showed the exact same
  wrong-looking-label symptom on a *different* part (`26Y118T1F1A1_1173`) of the same 26Y118
  job, even after pass 24's fix. Root-caused it fully rather than assuming a regression:
  reproduced it exactly (down to the same part) once the actual persisted `wasteStrategyDefault`
  setting ("edge", not "balanced") was accounted for — same debug-trace technique as pass 24 —
  and found it's the identical, already-understood mechanism: 7 real parts share this exact
  footprint (898.8x327.6), 6 fit the preferred pose, and whichever one is placed last runs out
  of correctly-shaped free space and legitimately falls back. The project owner then asked how
  Fin China avoids this, which led to a genuinely useful discovery: Fin China's own optimizer
  doesn't just avoid the fallback, it uses **one fewer sheet overall** (19 vs. our 20) on the
  identical job — checked directly by counting real `<Pattern>` elements in both files — and
  achieves that by spreading the 7 identical parts across 4 different sheets (2/2/2/1) instead
  of cramming 6 onto one sheet and leaving a 7th to fight over scraps. That ruled out "hard
  rule vs. soft preference" as the right framing entirely: a hard rule can only ever match or
  lose to today's sheet count on our own greedy engine, never beat it the way Fin China does.
  The project owner separately observed Fin China's own software takes 30-40+ seconds to
  optimize a comparable job vs. nesting-pro's 1-2s, and confirmed spending real time for a
  better result is acceptable — that reframed the whole approach from "find a smarter fixed
  rule" to "spend the available time searching for a better layout." **Prototyped and measured
  three approaches before implementing anything** (all in scratch scripts, zero repo changes
  until the last one): (1) a "hard preference for duplicate-sized parts" rule — genuinely
  unsafe as implemented, caused 8 real parts to go unplaced on `nesting_machine_data.csv`,
  because "footprint repeats 2+ times" does NOT guarantee the preferred pose is ever
  achievable (some duplicate groups can be uniformly too wide for the board in that pose); a
  corrected, geometrically-verified version fixed the unplaced risk but made 26Y118 *worse*
  than doing nothing (20->22 sheets) — moving further from Fin China's 19, not closer,
  confirming a fixed rule alone can't close this gap. (2) pure randomized multi-start (shuffled
  tie-order across up to 9,375 trials) — zero improvement on 2 of 3 jobs; shuffling which
  identical part is processed last doesn't change how many of that size structurally fit before
  space runs out. (3) randomized top-K free-rectangle candidate selection (GRASP-style) — real
  but modest improvement (26Y118: 13->11 mismatches; BEDROOM 3-4: 68->67 sheets, 72->52
  mismatches), plateauing well short of Fin China's actual result even at thousands of trials —
  confirmed the ceiling is structural, not a search-depth problem, since our greedy engine
  never reconsiders a placement once made.

  **Implemented** (approved directly, "please go ahead" then "implement these structural
  changes... phased manner"): a genuinely different, opt-in search layer on top of the
  *existing*, unmodified free-rectangle engine, in two parts — **Part A**,
  `place_parts_on_board()` (`nanxing_packing.py`) gained a "defer" move: when a part flagged as
  a `duplicate_id` (its exact footprint recurs 2+ times in the same material/grain group,
  computed once from the *original* group, not the shrinking remainder) is about to fall back
  to its non-preferred pose, it can instead be skipped entirely for this sheet and retried on
  the next (fresh) one, governed by a per-trial `defer_probability` roll. This move is safe
  *when* the caller's duplicate-flagging is right, but not provably safe in general (a
  duplicate group can still be one whose preferred pose is geometrically impossible
  everywhere, as approach (1) found the hard way) — so the function carries its own internal
  safety net: if deferring leaves an entire sheet's pass empty, it retries that exact sheet
  once with deferring forced off, guaranteeing real progress whenever anything is genuinely
  placeable, regardless of whether the caller's duplicate heuristic was itself sound. Also
  added randomized top-K candidate selection (`candidate_pool`), reusing approach (3)'s
  win. All four new parameters (`duplicate_ids`, `search_rng`, `defer_probability`,
  `candidate_pool`) default to no-ops, so the function is byte-identical to before for every
  caller that doesn't pass them. **Part B**, `nanxing.py`'s `optimize()` gained
  `search_time_budget_s`/`search_seed` (both default to `0`/off, same backward-compatibility
  guarantee): when a real budget is given, it runs the plain deterministic pass first (always
  the first candidate, so the search can never return something *worse* than not searching at
  all), then spends the remaining time on trials with randomized `defer_probability`/
  `candidate_pool` draws, scoring each full-job result by `(unplaced, sheets, mismatches,
  -utilization)` — that exact priority order, so it never trades away material efficiency for
  prettier labels — and keeps the best. `search_seed` (default `0`) makes the *whole search*
  deterministic: critical in this codebase specifically, since `/optimize`, `/export/pdf`, and
  `/export/xml` each independently re-run `nanxing_optimize()` from scratch with no shared
  cache (confirmed directly: two independent `/optimize` calls with identical input produce
  byte-identical placements) — without a fixed seed, a downloaded XML could silently differ
  from the preview an operator just checked.

  Wired into `api.py`'s three real call sites (`/optimize`, `/export/pdf`, `/export/xml`) via a
  new `DEFAULT_NANXING_SEARCH_TIME_BUDGET_S = 20.0` constant (comfortably under Fin China's own
  30-40+ seconds, confirmed acceptable directly with the project owner), overridable per-request
  via `searchTimeBudgetS`. Existing HTTP tests (`test_api_optimize.py`) explicitly pass `0` so
  the pre-existing suite stays fast and deterministic — confirmed this was necessary first
  (without it, one existing test would have silently grown a 20s runtime from the new default).

  **Real measured results** (all via the actual production `nanxing.optimize()`, not a
  scratch prototype) on the same 3 benchmark jobs used since pass 22, at a 20s budget:

  | Job | Sheets before → after | Mismatches before → after |
  |---|---|---|
  | 26Y118 (138 parts) | 20 → 20 | 13 → **5** |
  | nesting_machine_data.csv (55 parts) | 9 → 9 | 18 → 16 |
  | BEDROOM 3-4 (656 parts) | 68 → **67** | 72 → **50** |

  Honest framing: this is real, safe, measured improvement — never worse than the deterministic
  baseline on any of the 3 jobs, sometimes strictly better on sheets too — but it does **not**
  reach full parity with Fin China (0 mismatches, 19 sheets on 26Y118). `nesting_machine_data.csv`
  barely moves under any approach tried, including this one — strong evidence most of its
  mismatches are geometrically unavoidable given the current placement model (a part's own
  proportions vs. the board), not a search-depth problem a bigger budget would fix. Closing the
  remaining gap on 26Y118/BEDROOM 3-4 most likely needs the actual structural piece still
  missing: a genuine group-block placement primitive (deciding *how many* of a duplicate group
  to commit to a sheet as a coordinated decision, not just whether to defer one at a time) —
  discussed as a distinct, larger follow-on phase, not started this pass.

  New `backend/tests/test_nanxing_search.py` (+6 tests, suite 186->192): zero-budget backward-
  compatibility lock-in, `_compute_duplicate_ids_by_group` unit test (confirms grain-locked
  parts are correctly excluded), a real-data test asserting the search never scores worse than
  the baseline and strictly improves on the real 26Y118 job even at a fast 0.5s budget, a
  fixed-seed determinism test, a deliberately adversarial safety-net test (a fake rng that
  always defers, on a crafted 2-part scenario where deferring is *not* actually safe by the
  duplicate heuristic alone — confirms the internal retry-with-defer-disabled safety net saves
  it anyway), and a geometry-invariant check (no overlaps, everything within margin) against a
  *searched* result specifically, not just the deterministic path other tests already cover.
  Verified the safety-net test has teeth: temporarily disabled the retry, reran, reproduced the
  exact "0 placed, spuriously empty sheet" failure, restored. Full suite: 192 passed. Verified
  real end-to-end HTTP wiring too (not just direct function calls): a live `TestClient` run of
  `/optimize` then `/export/xml` against the real 26Y118 CSV at the actual 20s production
  default, both returning 200 with sensible data (20 sheets, 0 unplaced; a valid 442KB XML
  document) — and, separately, confirmed the determinism property holds over real HTTP calls
  too (two independent `/optimize` requests with identical bodies returned byte-identical
  placements). No frontend changes — this is a backend algorithm/API change only, no new
  request fields the UI needs to set (the default budget applies automatically).
  Before all eighteen prior passes: Phases A/B/C of
  `~/.claude/plans/delegated-moseying-robin.md` complete, plus follow-on M6, M7, and
  Nanxing-packer-efficiency passes (same plan file, rewritten fresh for each pass), prompted by
  `update_001` (a user-supplied real-world comparison against the actual Nanxing machine
  software's output for the same job) rather than by spec/milestone review — worth checking both
  `Updates/` and `Business Logic/` for similar drop-in spec/reference files in future sessions,
  since they carry real-world ground-truth this project otherwise doesn't have. Also worth
  noting, now confirmed twice in one day: **user pushback or a real machine result that doesn't
  match expectations is a strong signal to re-verify against golden data or the real output, not
  to re-explain the existing code** — that's exactly how both M10 and M11 were found, and both
  were real bugs that 100%-passing test suites had missed for the same structural reason: the
  tests validated internal self-consistency, not agreement with an external ground truth they
  never actually touched.
- **Backend entry point:** `backend/api.py` (FastAPI app object `app`), run via `backend/start-backend.sh`
  → `uvicorn api:app --reload --host 127.0.0.1 --port 8000`. `backend/.venv` has the `dev`
  extra installed (`pip install -e ".[dev]"`, now including `pypdf` for PDF-export test
  assertions and `httpx` for FastAPI `TestClient` HTTP tests) — `pytest -q` from `backend/` runs
  192 tests, all green (once the stale `sample_data` XML path from "Remaining work" #4 is
  worked around — see "Last worked" pass 21). New runtime dependency: a SQLite file at
  `backend/nesting_pro.db`
  (gitignored, auto-created on first request via `storage.get_connection()` — no manual setup
  step, but a fresh clone's first `/stock-boards` or `/settings` call creates it).
- **Frontend entry point:** `frontend/` (Vite + React + TypeScript), `npm run dev` serves on
  `http://localhost:5173` with `/api/*` proxied to the backend on `:8000` (`vite.config.ts`) —
  run both dev servers side by side, no backend changes needed for local dev. `npm run build`
  and `npm run lint` are clean; no test framework wired up yet (type-check + build + one
  Playwright-driven manual browser pass is the only verification so far).
- **What's done & passing:** M1 parser works for both CSV schemas on real sample data (0 parse
  errors on 55 + 50 rows), quoted-space edges now normalize correctly. M2 and M4 both place
  every part from a real job (0 unplaced, was 62%/70%) with zero overlaps, each running its own
  independent copy of the same class of free-rectangle best-fit engine (`optimizer/saw_packing.py`
  / `optimizer/nanxing_packing.py` — previously one shared `optimizer/packing.py`, deliberately
  un-shared per `update_003`, see M2 row). M4's packer additionally went through a real
  efficiency fix in an earlier pass (see the M4 row) — its old shelf-packing approach was
  replaced after `update_001` showed a real-machine-software comparison exposing just how bad it
  was (a 21-part sheet at 21.4% utilization) — and, this pass, gained the `waste_strategy`
  option that closed most of the remaining gap to the real machine's utilization (see M4 row).
  M5's
  `optimizer/export/xml.py` is a full rewrite against the real `FccRoot` schema. M6 added
  `ToolPointList`/`ToolPoint` and, along the way, found the *exact* rule behind `MachiningPoint`
  and the undocumented `Lineament.RotationAngle` secondary axis that Phase C had only worked
  around — both are now implemented for real, not tolerated as noise. The golden-file round-trip
  test (`backend/tests/test_xml_roundtrip.py` + `tests/fcc_golden.py`) now runs against all 4
  non-empty golden files via parametrization (was previously wired to only the primary one).
  All of this is covered by `backend/tests/` instead of one-off scripts — verified both the
  guillotine suite (Phase A) and the tightened round-trip assertions (M6) catch real regressions
  by temporarily breaking the fix, rerunning, and confirming failures, then restoring. All four
  endpoints (`/parse`, `/optimize`, `/export/pdf`, `/export/xml`) still respond 200 with
  non-empty bodies on real data end-to-end (smoke-tested manually; `/export/pdf`'s output is now
  also asserted by `test_pdf.py`, see M3 row — `/parse`, `/optimize`, `/export/xml` still aren't
  covered as HTTP endpoints, only their underlying `optimizer/` functions are).
- **What's done & passing (M7):** the full CSV → map → configure → preview → download wizard,
  built fresh this pass — see the M7 milestone row for the exact Playwright-driven verification
  (both real sample CSVs, both machine targets, real PDF/XML downloads, a deliberately bad CSV
  correctly surfacing the backend's error instead of crashing, zero console/page/network errors).
- **What's in progress / half-done:** M7's frontend `SheetPreview.tsx` SVG draws boards with
  `boardW` horizontal / `boardL` vertical (portrait) — this briefly diverged from M3 Rev 1's
  landscape PDF, but M3 Rev 2 switched the PDF back to portrait using the same native
  (`x` along `board.width`, `y` along `board.length`) axes the packer and `SheetPreview.tsx`
  already use, so **the preview and the PDF happen to agree again** — coincidentally, not
  because anyone reconciled them; worth being aware this could drift apart again on a future
  PDF-only change. They still don't share a renderer (M7's own aspiration, still deferred).
  `ToolPointList`'s
  exact-match rate (88.1–96.4% across the 4 golden files) has residual, likely-irreducible
  noise — Appendix A.5 itself expects this needs machine dry-run refinement, so it's tracked as
  a documented tolerance rather than chased further. `ToolPoint` (which of the 4 lead-in points
  a cut starts at) has no discovered rule; defaults to `"0"` per Appendix A.5's own stated
  fallback. `frontend/` has no automated test suite (Vitest/RTL or similar never set up) — only
  type-check, build, lint, and one manual browser-driven pass exist as verification.
- **Immediate next step:** M6's output has never touched a real machine — before relying on it,
  a small reproducible dry-run cut is needed (spec §7.3/Appendix A.5), which isn't something a
  coding session can do unattended. Barring that, remaining work is M8 (offcut reuse), the
  deferred parts of M9 (login/auth, tenant/company modeling, per-machine "available
  optimizations" config, CSV-schema-template renaming — see M9 row for the confirmed
  Template-1/Template-2 mapping, not yet wired in), the frontend/PDF board-orientation mismatch
  noted above, possibly a `frontend/` test suite, and giving the `waste_strategy="edge"` option
  a real cut/dry-run check of its own — it's verified geometrically (decomposable, no overlaps,
  no drops) and against a real CSV job's numbers, but "wastage visually pushed to one edge"
  hasn't been confirmed on an actual cut sheet, only in a rendered PDF. M10's grain-axis fix is
  verified against real golden machine XML data (as strong a signal as this project has without
  a physical cut) but, like everything grain-related, is still worth a real dry-run check before
  fully trusting it on production material — see M10 row. **M11's fix needs the project owner to
  reload the regenerated file** (`results/issue-002-fixed/26Y117T1F1B1(BEDROOM 3-4)-...xml`)
  into the real NaccNesting software the same way the original bug was found, to confirm the
  layout now matches the board correctly before it's trusted for an actual cut — this session
  verified the coordinates land within the declared board bounds and genuinely use the length
  axis, but "loads correctly" and "the machine's own screen shows the right layout" are two
  different confirmations, and only the project owner can do the second one.
- **Open questions / blockers:** none new beyond what's already in `instructions.md` §10 (still
  needs owner confirmation on kerf value, ZIP/JPEG vs plain PDF acceptance, etc). `ToolPoint`'s
  rule is unresolved (see above) — needs either more/different golden data or real machine
  feedback, neither of which this project currently has.

---

## Validation strategy (do this — prevents scrapped material)

1. **Golden-file round-trip:** parse a real machine-cut XML into the normalized model,
   re-serialize, **diff** against the original, drive to near-zero (float noise only).
   This proves the serializer before any new nest.
2. **Geometry invariants (assert in tests):** no overlaps; all parts within margin-inset
   board; `Σ part area + Σ oddment area + kerf/spacing ≈ board area`; polygons closed;
   utilization ∈ (0,100].
3. **Saw guillotine check:** assert every saw layout is recursively guillotine-decomposable;
   fail loudly if not.
4. **Machine dry-run:** first real export = a small reproducible job, cut & confirmed before scaling.

### FCC serialization fidelity (M5/M6)
UTF-8 · `\r\n` line endings · 2-space indent · self-closing empty elements · numbers
formatted like the reference. A valid empty job is a self-closed root `<FccRoot …/>`.

---

## Remaining work — likely priority order

1. **MAX PRIORITY (set directly by the project owner, 2026-09-02, pass 23).** Grain-free
   (`grain="none"`) parts' "unrotated" pose was backwards relative to the real Fin China
   machine's own convention — **fixed in pass 24** (2026-09-03): `nanxing_packing.py`'s
   `_footprint()` now uses `natural_swap = part.grain != "width"` (was `== "length"`), so
   grain-free parts share grain="length"'s (correct) natural-pose baseline instead of
   grain="width"'s. **Verified two ways against the exact real parts in `results/26Y118_data/`**:
   part `26Y118T1F1A1_1246` now matches Fin China's `RotateAngle`/`MachiningPoint`
   byte-for-byte (`RotateAngle` absent/`MachiningPoint="1"` both sides) — previously it only
   *looked* right by geometric coincidence (right axis span, wrong internal `rotated` meaning).
   Part `26Y118T1F1A1_1198` — the specific part the project owner asked about — **still doesn't
   match Fin China after the fix**, but this was traced to its actual cause rather than left
   unexplained: on the sheet it lands on, the free rectangle available at that point in the
   packing sequence is 1205×666.5mm, and the corrected/preferred orientation needs 726.5mm on
   the short axis — about 60mm more than is there. Confirmed via a temporary debug trace of
   `place_parts_on_board`'s per-part rectangle scoring (added, checked, then fully reverted —
   `git diff` after confirms only the `_footprint` line changed). This is pass 22's own
   documented "preference, not a hard constraint" behavior working exactly as designed, not a
   bug in the fix. **Open follow-up, not yet decided:** should the preference become a hard
   constraint (only ever try the Fin-China-matching orientation, never falling back) to
   guarantee every part's on-screen label matches, at the cost of possibly more sheets or
   genuinely unplaced parts on tight jobs? Left as a preference for now, unchanged from how
   pass 22 shipped it — needs the project owner's steer before changing that tradeoff. Updated
   the pass-22 tests that assumed the old (backwards) convention in `test_packing_engines.py`
   (`test_footprint_none_grain_unaffected_by_the_fix` split into a saw-specific test, unchanged,
   and a new nanxing-specific test asserting the corrected swap;
   `test_nanxing_prefers_cutlength_on_length_axis_when_both_orientations_fit`'s
   `placed.rotated` flipped `True`→`False`;
   `test_nanxing_preference_falls_back_when_preferred_orientation_does_not_fit`'s flipped
   `False`→`True` — the two tests' pre-fix/post-fix roles literally swapped, since the "which
   orientation is preferred" flip means the part that used to demonstrate the preferred case now
   demonstrates the fallback case and vice versa). Full suite: 186 passed, no regressions
   (confirmed via the same untracked-symlink workaround as passes 21/22 for the still-open item
   4 below, removed after). Re-measured efficiency on all 3 real jobs used in pass 22
   (`26Y118_data`'s 138-part job, `nesting_machine_data.csv`, `26Y117T1F1B1(BEDROOM 3-4)`'s 656
   parts) by diffing against a `git stash` of just this one file's change — identical sheet
   counts and combined utilization before/after in all three; this fix only changes *which*
   `rotated` value is chosen for a part whose fallback orientation was already the one being
   used, not whether anything fits. `saw_packing.py` deliberately untouched (unaffected —
   confirmed the parametrized-across-both-modules test only failed for `nanxing_packing`, not
   `saw_packing`, matching the intentional scoping from pass 22). Logged in `To DOs.md`'s Bug
   Fixes section too, per the project owner's preference for that file as the terse actionable
   list. **The "open follow-up" question above got answered empirically in pass 25, not by a
   direct decision**: a hard constraint was measured and found to make things *worse* than
   today (26Y118: 20->22 sheets), not better — so instead of a hard rule, pass 25 built a
   time-budgeted search layer (see "Last worked" pass 25 for the full writeup) that gets real,
   safe, measured improvement (26Y118: 13->5 mismatches; BEDROOM 3-4: 68->67 sheets, 72->50
   mismatches) without ever regressing sheets/unplaced count. Still short of full Fin China
   parity — tracked as its own follow-on item, see "Remaining work" below (new item, structural
   group-block placement).
2. **~~Reload the M11-fixed XML into the real NaccNesting software~~ — done, and superseded by
   an actual physical dry-run cut** (see item 2 and `Issues/issues_005.md`). The layout loaded
   and looked correct; a small demo job was then actually cut.
3. **Physical dry-run cut happened (`Issues/issues_005.md`) — reported a real 6–7mm shortfall on
   one dimension (width), the other (length) exact.** Investigated thoroughly: this app's
   exported geometry for the exact reported scenario was verified byte-for-byte against a real
   golden machine-cut workpiece with identical characteristics, and found correct — no bug in
   this app's XML export explains the discrepancy (see pass 16 in "Last worked" above for the
   full evidence trail). Follow-on discussion (pass 17) noticed nesting-pro's demo layout sat in
   a different board corner than Nanxing's own inbuilt-optimizer layout for a comparable job, and
   added a **"Board corner" dropdown** (`optimizer/placement.py`) so the *exact same* layout can
   be reproduced in any of the 4 corners — the clean way to test whether table position (vacuum-
   zone coverage, axis calibration, fence distance) explains the shortfall, without also changing
   which software generated the file. Still open, needs the project owner and the physical
   machine: cut the same demo job again with "Board corner" set to match where Nanxing's own
   optimizer placed it, and measure. If the shortfall follows the *position* (goes away in the
   new corner) that points at the table/machine; if it follows the *file* regardless of corner,
   that reopens the software investigation. `ToolPoint`'s rule is still unknown (defaults to `0`)
   and remains unconfirmed either way. Not something a coding session can do unattended.
4. **Bug: physical label placeholder position is inconsistent/"off-center" on nesting-pro
   exports — partially fixed in pass 22, real root cause found in pass 23, tracked as item 1
   above (max priority, set by the project owner).** Reported directly (2026-09-02) via real
   NaccNesting screenshots — "Fin China's own optimization tool places it properly; nesting-
   pro's placement is sometimes off-center." My first hypothesis (`ToolPoint`, always hardcoded
   to `"0"` in this app's export — checked real Fin-China data and confirmed it genuinely varies
   0/1/2/3 there) turned out to be a red herring for *this* symptom: the project owner's own
   diagnosis was sharper — "the machine always sets the label along the length," meaning
   NaccNesting's printed dimension text apparently always assumes `CutLength` sits horizontally
   (the board's length axis) regardless of a part's actual `RotateAngle`. Combined with a real
   gap in `nanxing_packing.py` (its best-fit placement had zero preference for which board axis
   carried `CutLength`, confirmed directly against the exact reported part), pass 22 shipped a
   placement *preference* (not a hard rule — falls back safely) for keeping `CutLength` on the
   board's length axis when both orientations fit, measuring zero efficiency impact on 3 real
   jobs. **That fix turned out to be a real but incomplete mitigation, not the root cause**: pass
   23's direct real-file comparison (`results/26Y118_data/`, see item 1) found this app's own
   notion of "unrotated" for grain-free parts is backwards relative to Fin China's own
   convention, which the preference-ordering fix can't fully correct since it's still just a
   preference that falls back when the "corrected" orientation doesn't fit the current free
   space — see item 1 for the actual fix. `ToolPoint`'s own rule genuinely remains unknown
   (still hardcoded to `"0"`, see item 3 above) — it was never the explanation for this bug.
5. **Fix stale golden-XML test paths (quick, actionable right now, unlike items 1–4).**
   `sample_data/`'s XML folder was renamed (`XML Data for Nanxing Nesting Machine/` → `XML Data
   from Fin China/`) since the paths were last updated in `conftest.py`/`test_xml_roundtrip.py` —
   currently causes 32 errors + 4 failures in the backend suite. Found during pass 19 (see "Last
   worked"), deliberately not fixed then since it was unrelated to that pass's request — but
   worth confirming the new folder name is the intended final one (not itself a temporary/WIP
   rename) before updating the test paths to point at it.
6. **Two related, quick-to-fix discrepancies found during pass 21's F.S./`Info1`/`Info2` fix
   (see "Last worked"), deliberately left alone since they weren't part of what was asked and
   the project owner explicitly scoped that pass to just the label fix:**
   - `optimizer/export/xml.py`'s `Workpiece.Length`/`Width` attributes are set from
     `part.finishedLength`/`finishedWidth` (the same source `Info1`/`Info2` now correctly use),
     but real golden data's actual `Length`/`Width` values are `CutLength + 6.0`/`CutWidth +
     6.0` exactly — confirmed across all 1039 real workpieces in `sample_data`, zero
     exceptions, no dependency on edge-band type. Nothing currently reads `Length`/`Width` from
     this app's own export (cutting uses `CutLength`/`CutWidth`; F.S. now correctly uses
     `Info1`/`Info2`), so this is latent, not visibly broken — but worth fixing to match the
     real format if anything ever does start reading it.
   - `optimizer/import_xml.py`'s `parse_fcc_xml` (used by both the real "import an existing
     Nanxing XML" feature and `tests/fcc_golden.py`'s round-trip fixture) still sets
     `finishedLength`/`finishedWidth` from the imported XML's `Length`/`Width` attributes, not
     `Info1`/`Info2` — meaning an imported real machine file currently gets the *wrong* value
     into the field that means "finished/F.S. size," the mirror image of the export bug pass 21
     fixed. Not fixed this pass because it interacts with `test_xml_roundtrip.py`'s existing
     `Length`/`Width` comparison (see `compare_workpiece`): fixing the importer without also
     fixing the *first* bullet's `Length`/`Width` export formula would break that test's
     round-trip fidelity check for a well-understood reason (the two sides currently agree only
     because they share the same wrong assumption — the same structural blind spot that let
     M11 go undetected for a full milestone, see the note right after the M11 row). The clean
     fix is almost certainly both bullets together, plus extending `compare_workpiece` to check
     `Info1`/`Info2` too — not attempted here since it wasn't requested and touches the
     project's core validation asset.
7. **Share a renderer between `SheetPreview.tsx` and the PDF:** still two independent
   implementations (M7's own deferred aspiration) — they currently happen to agree on board
   orientation (both portrait, both using the packer's native axes) after M3 Rev 2 switched the
   PDF back to portrait, but that's incidental, not enforced; a future PDF-only orientation
   change could silently diverge them again (see M3/Current-state notes). M3's own remaining
   gap — a cut-sequence overlay — was deliberately skipped since neither reference PDF (Rev 1
   nor Rev 2) shows one; the `cuts` data still isn't wired into `export/pdf.py`, but nothing
   currently calls for it to be (M9-era passes did add a cut-line *overlay*, see M2/M3-adjacent
   passes 9–11 in "Last worked" — this item is specifically about a shared renderer, still open).
8. **Grain-direction arrow, real-world confirmation:** the length↔vertical/width↔horizontal
   mapping in M3 Rev 2 was confirmed with the project owner (not derived from the MaxCut
   reference, which was ambiguous — see M3 row), but still hasn't been checked against an
   actual grain-locked job on real material. Lower risk now than when this was first confirmed:
   M10's independent golden-XML investigation empirically found that `grain="length"` really
   does run along the board's *length* axis, matching this mapping exactly — still worth a
   sanity check if/when a grain-locked CSV goes to real material, but no longer just a guess
   backed only by the project owner's say-so.
9. **Frontend test suite:** `frontend/` has none yet — every UI pass through pass 19 was verified
   via type-check, build, lint, and a real headless-browser (Playwright) pass, not an automated
   suite (Vitest/RTL or similar).
10. **M10 grain-axis fix, real-world confirmation:** verified against real golden Nanxing XML
   data (16 matching `Grain="L"` workpieces, including the exact reported part) and against
   full geometry invariants on the reported job — the strongest evidence this project has for a
   grain-placement rule without an actual cut. Still worth a physical dry-run before fully
   trusting it, same caveat as everything else grain-related (see M6 row's own dry-run gap).
11. **`waste_strategy="edge"`, real-world confirmation:** verified geometrically (guillotine-
   decomposable, no overlaps, nothing dropped, both machines) and against real CSV job numbers
   (measured utilization + offcut-consolidation improvement — see M4 row), plus visually via a
   rendered PDF. Not yet confirmed on an actual cut sheet that the consolidated wastage is where
   it visually appears to be and is actually more usable as offcut stock in practice.
12. **M8 offcut reuse:** larger oddments become returnable stock (see Appendix A.6).
13. **M9, deferred scope:** `update_004.md`'s login/auth, tenant/company modeling, per-machine
    "available optimizations" config, and CSV-schema-template renaming (Nanxing Nesting →
    "Template 1", Panel Saw → "Template 2" — mapping already confirmed with the project owner,
    just not implemented yet) were all explicitly scoped out of the first persistence pass (see
    M9 row) to land Stock Boards + Waste Placement defaults first. Pick up in that order unless
    priorities change. Note: passes 13–14 (see "Last worked") already delivered cost/preset
    persistence in this same spirit, so this item is specifically the remaining login/tenancy/
    per-machine-config/template-rename slice, not the whole of M9's original scope.
14. **Structural group-block placement — the remaining gap to Fin China's actual result
    (pass 25, see "Last worked").** Pass 25's time-budgeted search layer (`nanxing.py`'s
    `search_time_budget_s`) got real, measured, safe improvement (26Y118: 13→5 mismatches;
    BEDROOM 3-4: 68→67 sheets, 72→50 mismatches) but plateaus short of Fin China's own result
    (0 mismatches, 19 sheets on 26Y118) — confirmed this is a structural ceiling, not a
    search-depth problem: our engine still only ever decides "place one part into the single
    best free rectangle," even when randomized: it never reconsiders *how many* of a duplicate
    group to commit to a given sheet as a joint decision, which is what Fin China's own output
    implies (spreads identical parts 2/2/2/1 across 4 sheets rather than 6+1 on one). Building
    that needs a genuine second placement primitive — grouping identical/near-identical
    footprints up front, computing candidate block sizes per sheet, and searching *how many*
    to commit per sheet (not just whether to defer one at a time) — on top of, not replacing,
    pass 25's existing search wrapper and safety-net machinery. Comparable in scope to M4's
    original packer rewrite; not started. `nesting_machine_data.csv` (55 parts) is worth
    re-checking once this exists — pass 25 found this specific job barely moves under *any*
    approach tried so far, suggesting most of its mismatches may be geometrically unavoidable
    (a part's own proportions vs. the board) rather than a placement-strategy problem, which a
    group-block primitive wouldn't fix either.

---

## Keeping this file honest (run at the start/end of a session)

```bash
git log --oneline -20
git status
git stash list
# what source exists:
find . -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \) \
  -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/__pycache__/*' | sort
# do backend tests pass?
pytest -q 2>/dev/null || true
```

## Avoiding another lost session
- This `CLAUDE.md` is durable memory — a closed/crashed window resumes from it, not from a transcript.
- Claude Code only persists a session to disk once it writes; a window that lived only in the
  extension's memory (opened, never messaged, then closed on restart) can vanish. Send at least
  one message early, and prefer resuming via `claude --resume <id>` in the project dir.