import { useEffect, useState } from "react";
import "./App.css";
import { ApiError, downloadLabels, downloadPdf, downloadXml, getSettings, listLabelSettings, optimize, parseCsv, setWasteStrategyDefault } from "./api";
import { CsvUpload, type CsvLoaded } from "./components/CsvUpload";
import { ColumnMapping } from "./components/ColumnMapping";
import { LabelSettingsLibrary } from "./components/LabelSettingsLibrary";
import { LabelSettingsPanel } from "./components/LabelSettingsPanel";
import { MachineSelector } from "./components/MachineSelector";
import { MaterialBreakdown } from "./components/MaterialBreakdown";
import { ParamsPanel } from "./components/ParamsPanel";
import { PresetLibrary } from "./components/PresetLibrary";
import { SheetPreview } from "./components/SheetPreview";
import { StockBoardLibrary } from "./components/StockBoardLibrary";
import { Stepper } from "./components/Stepper";
import { Summary } from "./components/Summary";
import { UtilizationChart } from "./components/UtilizationChart";
import { WasteStrategyComparison } from "./components/WasteStrategyComparison";
import { XmlImport } from "./components/XmlImport";
import type { ImportXmlResult, LabelSettings, Margin, OptRequest, OptResult, Part, PlacementCorner, Preset, StockBoardWithCost, TargetMachine, WasteStrategy } from "./types";

// Built-in fallback until the user saves/picks a label-settings preset -- mirrors backend
// storage.py's DEFAULT_LABEL_SETTINGS exactly (A4 sheet, 3x8 grid of 63.5x38.1mm labels).
const DEFAULT_LABEL_SETTINGS: LabelSettings = {
  pageType: "sheet",
  pageWidth: 210,
  pageHeight: 297,
  labelWidth: 63.5,
  labelHeight: 38.1,
  marginTop: 10,
  marginRight: 10,
  marginBottom: 10,
  marginLeft: 10,
  gapX: 2.5,
  gapY: 2.5,
  showQrCode: true,
  showBarcode: false,
  showCornerMarks: true,
};

export type Step = "upload" | "map" | "configure" | "results";

function deriveDefaultStock(parts: Part[]): StockBoardWithCost[] {
  const seen = new Map<string, StockBoardWithCost>();
  for (const p of parts) {
    const key = `${p.material}__${p.thickness}`;
    if (!seen.has(key)) {
      seen.set(key, { material: p.material, length: 2440, width: 1220, thickness: p.thickness, grain: "none", cost: 0, costUnit: "board", density: 0, quantity: 0 });
    }
  }
  return Array.from(seen.values());
}

function App() {
  const [step, setStep] = useState<Step>("upload");
  const [csvLoaded, setCsvLoaded] = useState<CsvLoaded | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [parsing, setParsing] = useState(false);

  const [target, setTarget] = useState<TargetMachine>("saw");
  const [stock, setStock] = useState<StockBoardWithCost[]>([]);
  const [margin, setMargin] = useState<Margin>({ top: 0, right: 10, bottom: 10, left: 5 });
  const [kerf, setKerf] = useState(4);
  const [toolDiameter, setToolDiameter] = useState(6);
  const [partSpacing, setPartSpacing] = useState(6.1);
  const [allowRotation, setAllowRotation] = useState(true);
  const [wasteStrategy, setWasteStrategyState] = useState<WasteStrategy>("balanced");
  const [showCutLines, setShowCutLines] = useState(false);
  const [placementCorner, setPlacementCorner] = useState<PlacementCorner>("top-right");
  const [labelSettings, setLabelSettings] = useState<LabelSettings>(DEFAULT_LABEL_SETTINGS);
  // Panel Saw CSVs have no Client/Project column at all, and a part's own barcode/posId can
  // occasionally be missing too -- these are job-level manual fallbacks, entered fresh per job
  // (not persisted as part of a reusable LabelSettings preset), used only when the CSV itself
  // can't supply the data.
  const [clientNameOverride, setClientNameOverride] = useState("");
  const [orderNoOverride, setOrderNoOverride] = useState("");

  // Load the persisted default once on mount, then keep it "sticky": every change the user
  // makes gets saved back as the new default for next time (Updates/update_004.md).
  useEffect(() => {
    getSettings()
      .then((s) => {
        setWasteStrategyState(s.wasteStrategyDefault);
        if (s.defaultLabelSettingsId != null) {
          // Settings only carries the id -- the full row is needed, so resolve it against the
          // library list rather than adding a single-item GET endpoint just for this lookup.
          listLabelSettings()
            .then((items) => {
              const match = items.find((i) => i.id === s.defaultLabelSettingsId);
              if (match) setLabelSettings(match);
            })
            .catch(() => {
              /* fall back to the built-in default already set */
            });
        }
      })
      .catch(() => {
        /* fall back to the "balanced"/built-in defaults already set — persistence is a nice-to-have here */
      });
  }, []);

  function setWasteStrategy(value: WasteStrategy) {
    setWasteStrategyState(value);
    setWasteStrategyDefault(value).catch(() => {
      /* the job can still run with the chosen value even if saving the default failed */
    });
  }

  // "strips" is Panel Saw only (ParamsPanel.tsx hides the option for Nanxing) — if the target
  // switches away from saw while it's selected (manual switch, XML import forcing nanxing,
  // etc.), fall back to "balanced" for this session rather than sending an option Nanxing has
  // no dispatch for. Local state only, not persisted — the user's saved default sticks around
  // for next time they're back on the panel saw.
  useEffect(() => {
    if (target !== "saw" && wasteStrategy === "strips") setWasteStrategyState("balanced");
  }, [target, wasteStrategy]);

  const [optResult, setOptResult] = useState<OptResult | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeErrors, setOptimizeErrors] = useState<string[]>([]);
  const [downloadErrors, setDownloadErrors] = useState<string[]>([]);

  // Updates/update_006.md: a result loaded from an existing Nanxing XML rather than produced by
  // this app's own /optimize. There's no `parts` list backing it (the endpoint deliberately
  // doesn't return one — see api.py's /import/xml), so re-optimizing, adjusting parameters, or
  // downloading (which all call /optimize or /export/* with `parts`) would silently run against
  // an empty parts list and produce wrong output instead of the layout that was actually loaded.
  // Gates those actions off entirely rather than let them misbehave.
  const [isImported, setIsImported] = useState(false);

  const projectName = parts[0]?.customer?.replace(/\s+/g, "-") ?? "nesting-job";

  function currentRequest(): OptRequest {
    // cost/costUnit/density/quantity are display-only — the backend's StockBoard dataclass has
    // none of these fields and would reject an unexpected kwarg, so they're stripped here rather
    // than carried through to /optimize or /export/*.
    const backendStock = stock.map(({ cost: _cost, costUnit: _costUnit, density: _density, quantity: _quantity, ...board }) => board);
    return { parts, stock: backendStock, kerf, toolDiameter, partSpacing, margin, allowRotation, target, wasteStrategy, showCutLines, placementCorner };
  }

  function applyPreset(preset: Preset) {
    setTarget(preset.target);
    setMargin(preset.margin);
    setKerf(preset.kerf);
    setToolDiameter(preset.toolDiameter);
    setPartSpacing(preset.partSpacing);
    setAllowRotation(preset.allowRotation);
    setWasteStrategy(preset.wasteStrategy);
  }

  async function handleMappingConfirmed(finalCsvText: string) {
    setParsing(true);
    setParseErrors([]);
    try {
      const { parts: parsed } = await parseCsv(finalCsvText);
      setParts(parsed);
      setStock(deriveDefaultStock(parsed));
      setStep("configure");
    } catch (e) {
      setParseErrors(e instanceof ApiError ? e.errors : [String(e)]);
    } finally {
      setParsing(false);
    }
  }

  function handleXmlImported(imported: ImportXmlResult) {
    setTarget("nanxing");
    setMargin(imported.margin);
    setToolDiameter(imported.toolDiameter);
    setPartSpacing(imported.partSpacing);
    setStock(imported.stock.map((board) => ({ ...board, cost: 0, costUnit: "board" as const, density: 0, quantity: 0 })));
    setParts([]);
    setOptResult({ sheets: imported.sheets, unplaced: imported.unplaced, cuts: imported.cuts });
    setIsImported(true);
    setStep("results");
  }

  async function handleRunOptimize() {
    setOptimizing(true);
    setOptimizeErrors([]);
    try {
      const result = await optimize(currentRequest());
      setOptResult(result);
      setStep("results");
    } catch (e) {
      setOptimizeErrors(e instanceof ApiError ? e.errors : [String(e)]);
    } finally {
      setOptimizing(false);
    }
  }

  async function handleDownload(kind: "pdf" | "xml" | "labels") {
    setDownloadErrors([]);
    try {
      if (kind === "pdf") await downloadPdf(currentRequest(), { clientNameOverride, orderNoOverride }, projectName);
      else if (kind === "xml") await downloadXml(currentRequest(), projectName);
      else await downloadLabels(currentRequest(), labelSettings, { clientNameOverride, orderNoOverride }, projectName);
    } catch (e) {
      setDownloadErrors(e instanceof ApiError ? e.errors : [String(e)]);
    }
  }

  function startOver() {
    setStep("upload");
    setCsvLoaded(null);
    setParts([]);
    setParseErrors([]);
    setOptResult(null);
    setOptimizeErrors([]);
    setDownloadErrors([]);
    setIsImported(false);
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-title-row">
          <img src="/favicon.svg" alt="" className="app-logo" width={36} height={36} />
          <h1>CutOptimizer</h1>
        </div>
        <p className="app-tagline">Panel saw &amp; Nanxing nesting, from a parts CSV to a machine-ready file.</p>
      </header>

      <Stepper current={step} />

      {step === "upload" && (
        <div className="card">
          <CsvUpload
            onLoaded={(loaded) => {
              setCsvLoaded(loaded);
              setStep("map");
            }}
          />
          <XmlImport onLoaded={handleXmlImported} />
        </div>
      )}

      {step === "map" && csvLoaded && (
        <div className="card">
          <ColumnMapping
            csvText={csvLoaded.csvText}
            headers={csvLoaded.headers}
            previewRows={csvLoaded.previewRows}
            guessedSchema={csvLoaded.guessedSchema}
            onConfirm={handleMappingConfirmed}
            onBack={() => setStep("upload")}
          />
          {parsing && <p className="status-text">Parsing…</p>}
          <ErrorAlert errors={parseErrors} />
        </div>
      )}

      {step === "configure" && (
        <div className="card configure">
          <p className="parts-loaded-badge">{parts.length} parts loaded</p>
          <MachineSelector target={target} onChange={setTarget} />
          <ParamsPanel
            target={target}
            margin={margin}
            onMarginChange={setMargin}
            stock={stock}
            onStockChange={setStock}
            kerf={kerf}
            onKerfChange={setKerf}
            toolDiameter={toolDiameter}
            onToolDiameterChange={setToolDiameter}
            partSpacing={partSpacing}
            onPartSpacingChange={setPartSpacing}
            allowRotation={allowRotation}
            onAllowRotationChange={setAllowRotation}
            wasteStrategy={wasteStrategy}
            onWasteStrategyChange={setWasteStrategy}
            showCutLines={showCutLines}
            onShowCutLinesChange={setShowCutLines}
            placementCorner={placementCorner}
            onPlacementCornerChange={setPlacementCorner}
          />
          <StockBoardLibrary
            onUse={({ id: _id, ...board }) => setStock((prev) => [...prev, board])}
          />
          <PresetLibrary
            current={{ target, margin, kerf, toolDiameter, partSpacing, allowRotation, wasteStrategy }}
            onApply={applyPreset}
          />
          <LabelSettingsPanel
            settings={labelSettings}
            onChange={setLabelSettings}
            clientNameOverride={clientNameOverride}
            onClientNameOverrideChange={setClientNameOverride}
            orderNoOverride={orderNoOverride}
            onOrderNoOverrideChange={setOrderNoOverride}
          />
          <LabelSettingsLibrary current={labelSettings} onApply={setLabelSettings} />
          <ErrorAlert errors={optimizeErrors} />
          <div className="actions">
            <button className="btn btn--secondary" onClick={() => setStep("map")} disabled={optimizing}>
              Back
            </button>
            <button className="btn btn--primary" onClick={handleRunOptimize} disabled={optimizing}>
              {optimizing && <span className="spinner" aria-hidden="true" />}
              {optimizing ? "Optimizing…" : "Run optimize"}
            </button>
          </div>
          {optimizing && (
            <p className="status-text">
              Nesting {parts.length} parts — large jobs (hundreds of parts) can take a few seconds.
            </p>
          )}
        </div>
      )}

      {step === "results" && optResult && (
        <div className="results">
          <Summary result={optResult} stock={stock} margin={margin} allowRotation={allowRotation} />
          {isImported && (
            <p className="status-text">
              Viewing a layout imported from an existing Nanxing XML — its own placement is shown
              as-is. Downloading and adjusting parameters aren't available for imported layouts.
            </p>
          )}
          <div className="actions">
            {!isImported && (
              <>
                {/* the drawing (PDF) is a plain layout render — works for either machine's
                    placement result. The FCC XML is Nanxing-specific (it's the router's own
                    machine format), so only offer it for that target. */}
                <button className="btn btn--primary" onClick={() => handleDownload("pdf")}>
                  Download drawing (PDF)
                </button>
                {target === "nanxing" && (
                  <button className="btn btn--primary" onClick={() => handleDownload("xml")}>
                    Download XML
                  </button>
                )}
                <button className="btn btn--primary" onClick={() => handleDownload("labels")}>
                  Download labels (PDF)
                </button>
                <button className="btn btn--secondary" onClick={() => setStep("configure")}>
                  Adjust parameters
                </button>
              </>
            )}
            <button className="btn btn--quiet" onClick={startOver}>
              Start over
            </button>
          </div>
          <ErrorAlert errors={downloadErrors} />
          <UtilizationChart sheets={optResult.sheets} />
          <MaterialBreakdown sheets={optResult.sheets} stock={stock} />
          {!isImported && <WasteStrategyComparison request={currentRequest()} currentResult={optResult} />}
          <div className="sheet-grid">
            {optResult.sheets.map((sheet) => (
              <SheetPreview
                key={sheet.index}
                sheet={sheet}
                cuts={optResult.cuts.filter((c) => c.sheetIndex === sheet.index)}
                margin={margin}
                showCutLines={showCutLines}
              />
            ))}
          </div>
        </div>
      )}

      <footer className="app-footer">Copyright &copy; 2026 ARNIEM. All rights reserved.</footer>
    </div>
  );
}

function ErrorAlert({ errors }: { errors: string[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="alert alert--error">
      {errors.map((e, i) => (
        <li key={i}>{e}</li>
      ))}
    </ul>
  );
}

export default App;
