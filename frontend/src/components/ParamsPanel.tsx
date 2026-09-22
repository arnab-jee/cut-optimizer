import type { CostUnit, Margin, PlacementCorner, StockBoardWithCost, TargetMachine, WasteStrategy } from "../types";

interface Props {
  target: TargetMachine;
  margin: Margin;
  onMarginChange: (margin: Margin) => void;
  stock: StockBoardWithCost[];
  onStockChange: (stock: StockBoardWithCost[]) => void;
  kerf: number;
  onKerfChange: (kerf: number) => void;
  toolDiameter: number;
  onToolDiameterChange: (v: number) => void;
  partSpacing: number;
  onPartSpacingChange: (v: number) => void;
  allowRotation: boolean;
  onAllowRotationChange: (v: boolean) => void;
  wasteStrategy: WasteStrategy;
  onWasteStrategyChange: (v: WasteStrategy) => void;
  showCutLines: boolean;
  onShowCutLinesChange: (v: boolean) => void;
  placementCorner: PlacementCorner;
  onPlacementCornerChange: (v: PlacementCorner) => void;
}

export function ParamsPanel({
  target,
  margin,
  onMarginChange,
  stock,
  onStockChange,
  kerf,
  onKerfChange,
  toolDiameter,
  onToolDiameterChange,
  partSpacing,
  onPartSpacingChange,
  allowRotation,
  onAllowRotationChange,
  wasteStrategy,
  onWasteStrategyChange,
  showCutLines,
  onShowCutLinesChange,
  placementCorner,
  onPlacementCornerChange,
}: Props) {
  function updateStockField<K extends keyof StockBoardWithCost>(index: number, field: K, value: StockBoardWithCost[K]) {
    const next = stock.slice();
    next[index] = { ...next[index], [field]: value };
    onStockChange(next);
  }

  return (
    <div className="params-panel">
      <h2>Parameters</h2>

      <fieldset className="params-section">
        {/* Same underlying Margin value/behavior for both machines (optimizer/saw_packing.py
            and nanxing_packing.py use it identically) — only the label differs. Nanxing
            operators know this concept as "Trim" (confirmed against Fin China's own "Board
            triming" field, whose value matches our Margin XML attribute byte-for-byte). Panel
            Saw keeps "Margin" per the project owner's own workflow terminology. */}
        <legend className="params-section__title">{target === "nanxing" ? "Trim (mm)" : "Margin (mm)"}</legend>
        <div className="margin-layout">
          <label className="margin-layout__field margin-layout__field--top">
            <span className="field__label">Top</span>
            <input type="number" value={margin.top} onChange={(e) => onMarginChange({ ...margin, top: Number(e.target.value) })} />
          </label>
          <label className="margin-layout__field margin-layout__field--left">
            <span className="field__label">Left</span>
            <input type="number" value={margin.left} onChange={(e) => onMarginChange({ ...margin, left: Number(e.target.value) })} />
          </label>
          <div className="margin-layout__board" aria-hidden="true">
            Board
          </div>
          <label className="margin-layout__field margin-layout__field--right">
            <span className="field__label">Right</span>
            <input type="number" value={margin.right} onChange={(e) => onMarginChange({ ...margin, right: Number(e.target.value) })} />
          </label>
          <label className="margin-layout__field margin-layout__field--bottom">
            <span className="field__label">Bottom</span>
            <input type="number" value={margin.bottom} onChange={(e) => onMarginChange({ ...margin, bottom: Number(e.target.value) })} />
          </label>
        </div>
      </fieldset>

      {target === "saw" ? (
        <fieldset className="params-section">
          <legend className="params-section__title">Saw</legend>
          <div className="field-grid field-grid--2">
            <label className="field">
              <span className="field__label">Kerf (mm)</span>
              <input type="number" value={kerf} onChange={(e) => onKerfChange(Number(e.target.value))} />
            </label>
            <label className="field field--checkbox">
              <input type="checkbox" checked={allowRotation} onChange={(e) => onAllowRotationChange(e.target.checked)} />
              <span className="field__label">Allow rotation (grain-free parts only)</span>
            </label>
            <label className="field field--checkbox">
              <input type="checkbox" checked={showCutLines} onChange={(e) => onShowCutLinesChange(e.target.checked)} />
              <span className="field__label">Show cut lines</span>
            </label>
          </div>
        </fieldset>
      ) : (
        <fieldset className="params-section">
          <legend className="params-section__title">Nanxing</legend>
          <div className="field-grid field-grid--2">
            <label className="field">
              <span className="field__label">Tool Ø (mm)</span>
              <input type="number" value={toolDiameter} onChange={(e) => onToolDiameterChange(Number(e.target.value))} />
            </label>
            <label className="field">
              <span className="field__label">Part spacing (mm)</span>
              <input type="number" value={partSpacing} onChange={(e) => onPartSpacingChange(Number(e.target.value))} />
            </label>
            <label className="field field--checkbox">
              <input type="checkbox" checked={allowRotation} onChange={(e) => onAllowRotationChange(e.target.checked)} />
              <span className="field__label">Allow rotation (grain-free parts only)</span>
            </label>
          </div>
        </fieldset>
      )}

      <fieldset className="params-section">
        <legend className="params-section__title">Layout</legend>
        <div className="field-grid field-grid--2">
          <label className="field">
            <span className="field__label">Waste placement</span>
            <select value={wasteStrategy} onChange={(e) => onWasteStrategyChange(e.target.value as WasteStrategy)}>
              <option value="balanced">Balanced (tightest local fit)</option>
              <option value="edge">Push wastage to edges</option>
              {/* Panel Saw only — groups parts into full-length, single-width strips so an
                  operator only ever needs full-length strip cuts plus plain crosscuts, never a
                  cut that starts/stops mid-board. Trades some material efficiency for that
                  simplicity (see optimizer/saw_packing.py's own docstring for the measured
                  trade-off) — not offered for Nanxing, which has no manual-cutting operator. */}
              {target === "saw" && <option value="strips">Strips (easiest to hand-cut)</option>}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Datum point</span>
            <select value={placementCorner} onChange={(e) => onPlacementCornerChange(e.target.value as PlacementCorner)}>
              <option value="bottom-left">Bottom-left</option>
              <option value="bottom-right">Bottom-right</option>
              <option value="top-left">Top-left</option>
              <option value="top-right">Top-right</option>
            </select>
          </label>
        </div>
      </fieldset>

      <fieldset className="params-section">
        <legend className="params-section__title">Stock boards</legend>
        <table>
          <thead>
            <tr>
              <th>Material</th>
              <th>Thickness</th>
              <th>Length</th>
              <th>Width</th>
              <th>Cost (₹)</th>
              <th>Cost unit</th>
            </tr>
          </thead>
          <tbody>
            {stock.map((board, i) => (
              // Keyed by index, not material+thickness: "Use" in the Stock Board Library can
              // legitimately add a second row for the same (material, thickness) pair — e.g. to
              // override cost/density with a different library entry — which previously
              // collided on a material+thickness key (React "two children with the same key").
              // Rows are only ever edited/appended here, never reordered, so index is stable.
              <tr key={i}>
                <td>{board.material}</td>
                <td>{board.thickness}</td>
                <td>
                  <input type="number" value={board.length} onChange={(e) => updateStockField(i, "length", Number(e.target.value))} />
                </td>
                <td>
                  <input type="number" value={board.width} onChange={(e) => updateStockField(i, "width", Number(e.target.value))} />
                </td>
                <td>
                  <input type="number" min="0" step="0.01" value={board.cost} onChange={(e) => updateStockField(i, "cost", Number(e.target.value))} />
                </td>
                <td>
                  <select value={board.costUnit} onChange={(e) => updateStockField(i, "costUnit", e.target.value as CostUnit)}>
                    <option value="board">₹ per board</option>
                    <option value="sqft">₹ per sqft</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </fieldset>
    </div>
  );
}
