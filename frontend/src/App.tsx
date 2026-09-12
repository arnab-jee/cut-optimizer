import { useEffect, useState } from "react";
import "./App.css";
import { ApiError, downloadPdf, downloadXml, getSettings, optimize, parseCsv, setWasteStrategyDefault } from "./api";
import { CsvUpload, type CsvLoaded } from "./components/CsvUpload";
import { ColumnMapping } from "./components/ColumnMapping";
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
import type { ImportXmlResult, Margin, OptRequest, OptResult, Part, PlacementCorner, Preset, StockBoardWithCost, TargetMachine, WasteStrategy } from "./types";

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

  // Load the persisted default once on mount, then keep it "sticky": every change the user
  // makes gets saved back as the new default for next time (Updates/update_004.md).
  useEffect(() => {
    getSettings()
      .then((s) => setWasteStrategyState(s.wasteStrategyDefault))
      .catch(() => {
        /* fall back to the "balanced" default already set — persistence is a nice-to-have here */
      });
  }, []);

  function setWasteStrategy(value: WasteStrategy) {
    setWasteStrategyState(value);
    setWasteStrategyDefault(value).catch(() => {
      /* the job can still run with the chosen value even if saving the default failed */
    });
  }

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

  async function handleDownload(kind: "pdf" | "xml") {
    setDownloadErrors([]);
    try {
      if (kind === "pdf") await downloadPdf(currentRequest(), projectName);
      else await downloadXml(currentRequest(), projectName);
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
