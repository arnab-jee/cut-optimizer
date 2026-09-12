# CutOptimizer — Desktop App Packaging Plan

> **Status: planned, not started.** Explicitly deferred until after the current UI/UX fix-and-
> update pass is done — revisit this file when that work is finished. This plan itself was
> written once the trigger condition for picking the idea back up (real-machine validation of
> the optimizer's output, via the rotation/datum physical dry-run investigation this session)
> was substantially satisfied.

---

## 1. Goal

Package CutOptimizer as a standalone Windows application that factory-floor machines (Windows
10/11) can run **with no network dependency at all** — no shared backend, no cloud, no LAN/VPN
reachability requirement. Each install is fully self-contained: its own local optimizer, its own
local SQLite database (stock boards, presets, settings), runs entirely offline.

This explicitly supersedes the earlier cloud-hosting / Tailscale-remote-access discussion for the
optimization tool itself — that approach was rejected in favor of standalone installs (this
session, in response to "I would like optimization tool to not be network dependent"). Nothing
about the optimizer's own logic needs the network anyway — CSV parsing, packing, and PDF/XML
export are all local computation already; the network dependency that existed was purely about
*reaching* a shared instance, which is exactly what's being removed.

## 2. Constraints established this session

- **Target runtime OS:** Windows 10/11 (the factory machines; also matches Fin China/NaccNesting,
  the tool this is meant to replace, which the factory already runs the same way).
- **Dev machine:** Kubuntu 24.04. A Windows `.exe` **cannot be reliably built from Linux** —
  PyInstaller (or any Python-freezing tool) needs to run on the target OS to produce a working
  Windows binary. This is the one hard technical constraint the whole plan routes around.
- **No Windows dev machine available** for building, but a GitHub remote already exists
  (`arnab-jee/nesting-pro`) — so GitHub Actions' `windows-latest` runners are the way to produce
  a real, working Windows build without needing to own or borrow a Windows machine at all.
- **v1 scope: "simple launcher," not a native app window.** A script/shortcut starts a local
  server on `127.0.0.1` and opens it in the system's default browser — not an Electron/Tauri
  wrapper with its own window chrome. This was the explicitly agreed starting point from an
  earlier (pre-session) discussion of this same idea, on the reasoning that it's the lowest-
  effort path to a real, usable, standalone tool; a true native wrapper is a possible **later**
  upgrade if the visible-browser-chrome UX ever becomes a real complaint, not a v1 requirement.

## 3. Architecture change: two processes → one

Today, local dev runs two separate processes stitched together by Vite's dev-only `/api` proxy
(`frontend/vite.config.ts`): the Vite dev server and `uvicorn`. For a packaged standalone app,
that split adds nothing but risk (two things that can fail to start, two ports, a proxy config
that only exists in dev mode). The plan is to consolidate to **one process**:

- `npm run build` produces static `frontend/dist/` files (HTML/JS/CSS) — this step is platform-
  agnostic and can be done on Linux with zero changes.
- `backend/api.py`'s FastAPI app mounts `frontend/dist/` as static files (e.g. via
  `fastapi.staticfiles.StaticFiles`), so the single `uvicorn` process serves both the API *and*
  the UI on one port. No proxy, no CORS concerns (same origin), no second process to manage.
- The dev workflow (`npm run dev` + `./start-backend.sh` with the Vite proxy) stays exactly as
  it is today for active development — this static-mount path only activates for the packaged
  build, gated so it doesn't interfere with the existing hot-reload dev loop.

## 4. Packaging the backend

- **PyInstaller** to freeze `backend/api.py` (run via a small entry-point script that starts
  `uvicorn` programmatically rather than via the CLI) into a standalone Windows executable that
  bundles Python itself — the factory machines should need **nothing** pre-installed, matching
  how Fin China's own software just runs.
- **Data directory handling needs a real fix, not just reusing today's dev behavior.**
  `storage.py`'s `DB_PATH` currently defaults to a path next to `api.py` on disk
  (`CUTOPTIMIZER_DB_PATH` env var override, otherwise `os.path.dirname(__file__)`) — inside a
  PyInstaller-frozen executable, `__file__`-relative paths behave differently (resources get
  unpacked to a temp `sys._MEIPASS` directory that's wrong for something that must *persist*
  across runs, like the SQLite file). The packaged app needs to resolve its data directory to
  somewhere stable and writable regardless of where the exe is launched from or reinstalled —
  the standard Windows-appropriate location is something like `%APPDATA%\CutOptimizer\`, created
  on first run if missing. This is a real code change in `storage.py`, not just a packaging
  concern, and should be one of the first things implemented and tested once this plan is
  picked back up.
- Bundle the `frontend/dist/` static files as PyInstaller "data files" alongside the executable
  so the mounted-static-files path in §3 finds them at runtime.

## 5. The launcher

A small script (or the packaged exe's own `main()`) that, on double-click:

1. Starts the bundled backend, bound to `127.0.0.1` on a fixed port (e.g. `8000`) — no need to
   bind `0.0.0.0` at all now, since there's no remote-access requirement for this deployment
   shape (that was specifically for the cloud/Tailscale path, which this supersedes).
2. Opens the system default browser to `http://127.0.0.1:8000`.
3. Should handle the double-launch case gracefully — if the port's already in use (e.g. the app
   is already running from an earlier double-click), just open a browser tab to the existing
   instance instead of failing or spawning a second backend.

No installer wizard (Inno Setup/NSIS) planned for v1 — ship as a folder/zip you extract and run.
A proper installer is a reasonable later addition, not blocking.

## 6. Build pipeline (GitHub Actions)

New workflow (e.g. `.github/workflows/build-windows.yml`), running on `windows-latest`:

1. Checkout, set up Node + Python.
2. `npm ci && npm run build` in `frontend/`.
3. `pip install -e ".[dev]"` (or a trimmed non-dev requirements file — worth splitting out a
   `requirements.txt` without test-only deps like `pytest`/`httpx` for the frozen build, to keep
   the executable smaller) in `backend/`.
4. Run PyInstaller against the entry-point script, with the built frontend bundled as data files
   (§4).
5. Zip the resulting `dist/` output and upload it as a workflow artifact (or attach to a GitHub
   Release, if tagging releases ends up being the preferred distribution model).

Trigger: manual (`workflow_dispatch`) to start, rather than on every push — this isn't something
that needs to rebuild constantly, and Windows runner minutes aren't free forever on GitHub's
free tier. Can revisit triggering on version tags later if this becomes a more regular release
cadence.

## 7. Explicitly out of scope for this pass

- **Authentication / multi-user.** Not needed — standalone, single-user, offline installs have
  no shared-access surface to protect the way a networked deployment would.
- **Cross-machine data sync** (stock boards/presets following you between office and factory
  installs). Directly traded away by choosing "not network dependent" over the earlier cloud+
  Tailscale plan. If this becomes a real pain point later, it'd need its own design (e.g. an
  explicit export/import of the SQLite data, or a manual "copy this file" step) — not something
  to build speculatively now.
- **Native app window** (Tauri/Electron). Possible v2, not v1 — see §2.
- **Code signing.** Windows SmartScreen will very likely flag an unsigned executable as
  "unknown publisher" on first run. This is a real, expected friction point for factory-floor
  users, not a bug — a code-signing certificate is a real (and recurring) cost, worth deciding
  on deliberately later rather than assuming it's needed for a v1 internal tool. Document the
  click-through-the-warning step for whoever installs it in the meantime.
- **Antivirus false positives.** PyInstaller-frozen executables are commonly flagged by some
  antivirus products (a well-known, if unfortunate, pattern for this packaging tool). Worth
  knowing about before rollout so it doesn't look like a program problem when it happens — not
  something fixable from this side beyond code signing.
- **Auto-update mechanism.** New versions ship as a new zip/build for now; no in-app updater.

## 8. Verification plan (before considering this "done")

- Test on an actual clean Windows 10/11 machine with **no Python installed** — the whole point
  of PyInstaller bundling is that this should just work; this is the one test that actually
  proves it.
- Full workflow smoke test on that machine: launch → upload a real sample CSV → map columns →
  configure → run optimize → download PDF and XML → confirm both open correctly.
- Confirm the SQLite data directory (§4) is genuinely writable and persists correctly across
  multiple runs/relaunches, including after the machine reboots.
- Confirm a second double-click while it's already running doesn't break anything (§5).

## 9. Rough sequencing, for whenever this is picked back up

1. `storage.py`'s data-directory fix (§4) — do this first, independent of packaging, since it's
   a real correctness issue for a frozen build and easy to get wrong silently.
2. Consolidate frontend static-file serving into `api.py` (§3), verified still works fine in the
   normal dev flow.
3. Write the PyInstaller entry-point + spec file, test-run it *locally on Linux* first just to
   catch obvious packaging mistakes early (won't produce a working Windows binary, but will
   surface missing-data-file/import errors faster than waiting on a full CI round-trip).
4. Write the launcher.
5. Write and run the GitHub Actions workflow, get a real Windows artifact out of it.
6. Full verification pass per §8 on an actual Windows machine.
