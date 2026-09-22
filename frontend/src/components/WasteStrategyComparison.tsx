import { useState } from "react";
import { ApiError, optimize } from "../api";
import type { OptRequest, OptResult, WasteStrategy } from "../types";

interface Props {
  request: OptRequest;
  currentResult: OptResult;
}

const STRATEGY_LABEL: Record<WasteStrategy, string> = {
  balanced: "Balanced (tightest local fit)",
  edge: "Push wastage to edges",
  strips: "Strips (easiest to hand-cut)",
};

function otherStrategy(current: WasteStrategy): WasteStrategy {
  // "strips" is a different placement algorithm entirely (not a balanced/edge-style split-axis
  // variant), so there's no natural "opposite" for it — compare against "balanced" as the
  // baseline a strips job would otherwise have used (same baseline "edge" compares against).
  if (current === "balanced") return "edge";
  return "balanced";
}

interface Stats {
  sheets: number;
  avgUtilizationPct: number;
  unplaced: number;
}

function statsFor(result: OptResult): Stats {
  const avgUtilizationPct =
    result.sheets.length > 0 ? result.sheets.reduce((sum, s) => sum + s.utilizationPct, 0) / result.sheets.length : 0;
  return { sheets: result.sheets.length, avgUtilizationPct, unplaced: result.unplaced.length };
}

// Orchestrates a second /optimize call client-side with only wasteStrategy flipped — no new
// endpoint needed (ROADMAP.md Phase 2), reusing the exact request that produced the on-screen
// result so the comparison is apples-to-apples (same parts, stock, margins, target machine).
export function WasteStrategyComparison({ request, currentResult }: Props) {
  const [alternateResult, setAlternateResult] = useState<OptResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const alternate = otherStrategy(request.wasteStrategy);

  async function handleCompare() {
    setLoading(true);
    setError(null);
    try {
      const result = await optimize({ ...request, wasteStrategy: alternate });
      setAlternateResult(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.errors.join("; ") : String(e));
    } finally {
      setLoading(false);
    }
  }

  const current = statsFor(currentResult);

  return (
    <div className="card chart-card">
      <h3>Waste-strategy comparison</h3>
      {!alternateResult && (
        <>
          <p className="muted">
            Currently using <strong>{STRATEGY_LABEL[request.wasteStrategy]}</strong>. See how{" "}
            <strong>{STRATEGY_LABEL[alternate]}</strong> would have done on the same job.
          </p>
          <button className="btn btn--secondary" onClick={handleCompare} disabled={loading}>
            {loading && <span className="spinner" aria-hidden="true" />}
            {loading ? "Comparing…" : `Compare with "${STRATEGY_LABEL[alternate]}"`}
          </button>
          {error && <p className="alert alert--error">{error}</p>}
        </>
      )}
      {alternateResult && (
        <div className="strategy-compare">
          <div className="strategy-compare__col">
            <h4>{STRATEGY_LABEL[request.wasteStrategy]} (current)</h4>
            <dl>
              <dt>Sheets</dt>
              <dd>{current.sheets}</dd>
              <dt>Avg. utilization</dt>
              <dd>{current.avgUtilizationPct.toFixed(1)}%</dd>
              <dt>Unplaced parts</dt>
              <dd>{current.unplaced}</dd>
            </dl>
          </div>
          <div className="strategy-compare__col">
            <h4>{STRATEGY_LABEL[alternate]}</h4>
            <dl>
              <dt>Sheets</dt>
              <dd>{statsFor(alternateResult).sheets}</dd>
              <dt>Avg. utilization</dt>
              <dd>{statsFor(alternateResult).avgUtilizationPct.toFixed(1)}%</dd>
              <dt>Unplaced parts</dt>
              <dd>{statsFor(alternateResult).unplaced}</dd>
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}
