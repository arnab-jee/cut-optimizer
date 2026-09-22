import type { LabelSettings } from "../types";

interface Props {
  settings: LabelSettings;
}

const MAX_PX = 240;

// Mirrors backend/optimizer/export/labels.py's _grid_dims() -- duplicated client-side purely for
// this live preview, same way SheetPreview.tsx already renders its own independent board drawing
// rather than sharing a renderer with the PDF exporter (an established, accepted divergence in
// this project — see CLAUDE.md's M7/M3 notes on that).
function gridDims(settings: LabelSettings): [number, number] {
  if (settings.pageType === "roll") return [1, 1];
  if (settings.labelWidth <= 0 || settings.labelHeight <= 0) return [1, 1];
  const usableW = settings.pageWidth - settings.marginLeft - settings.marginRight + settings.gapX;
  const usableH = settings.pageHeight - settings.marginTop - settings.marginBottom + settings.gapY;
  const cols = Math.max(1, Math.floor(usableW / (settings.labelWidth + settings.gapX)));
  const rows = Math.max(1, Math.floor(usableH / (settings.labelHeight + settings.gapY)));
  return [cols, rows];
}

export function LabelDiagram({ settings }: Props) {
  const pageW = settings.pageType === "roll" ? settings.labelWidth : settings.pageWidth;
  const pageH = settings.pageType === "roll" ? settings.labelHeight : settings.pageHeight;
  const safePageW = Math.max(pageW, 1);
  const safePageH = Math.max(pageH, 1);
  const scale = MAX_PX / Math.max(safePageW, safePageH);
  const svgW = safePageW * scale;
  const svgH = safePageH * scale;
  const pad = 22; // room for dimension lines/labels outside the page outline

  const [cols, rows] = gridDims(settings);
  const labelW = Math.min(settings.labelWidth, safePageW) * scale;
  const labelH = Math.min(settings.labelHeight, safePageH) * scale;
  const marginLeft = settings.pageType === "roll" ? 0 : settings.marginLeft * scale;
  const marginTop = settings.pageType === "roll" ? 0 : settings.marginTop * scale;
  const gapX = settings.pageType === "roll" ? 0 : settings.gapX * scale;
  const gapY = settings.pageType === "roll" ? 0 : settings.gapY * scale;

  const cells: { x: number; y: number }[] = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      cells.push({ x: marginLeft + col * (labelW + gapX), y: marginTop + row * (labelH + gapY) });
    }
  }

  return (
    <div className="label-diagram">
      <svg
        className="label-diagram__svg"
        width={svgW + pad * 2}
        height={svgH + pad * 2}
        viewBox={`0 0 ${svgW + pad * 2} ${svgH + pad * 2}`}
        role="img"
        aria-label={`${settings.pageType === "roll" ? "Roll" : "Sheet"} label layout preview, ${settings.labelWidth}x${settings.labelHeight}mm labels`}
      >
        <g transform={`translate(${pad}, ${pad})`}>
          <rect x={0} y={0} width={svgW} height={svgH} className="label-diagram__page" />
          {cells.map((cell, i) => (
            <rect key={i} x={cell.x} y={cell.y} width={labelW} height={labelH} className="label-diagram__label" />
          ))}
          {/* width dimension line, above the page */}
          <g className="label-diagram__dim">
            <line x1={0} y1={-8} x2={svgW} y2={-8} />
            <line x1={0} y1={-11} x2={0} y2={-5} />
            <line x1={svgW} y1={-11} x2={svgW} y2={-5} />
            <text x={svgW / 2} y={-12} textAnchor="middle">
              {settings.pageType === "roll" ? settings.labelWidth : settings.pageWidth} mm
            </text>
          </g>
          {/* height dimension line, left of the page */}
          <g className="label-diagram__dim">
            <line x1={-8} y1={0} x2={-8} y2={svgH} />
            <line x1={-11} y1={0} x2={-5} y2={0} />
            <line x1={-11} y1={svgH} x2={-5} y2={svgH} />
            <text x={-12} y={svgH / 2} textAnchor="middle" transform={`rotate(-90, -12, ${svgH / 2})`}>
              {settings.pageType === "roll" ? settings.labelHeight : settings.pageHeight} mm
            </text>
          </g>
          {cells.length > 0 && (
            <text x={cells[0].x + labelW / 2} y={cells[0].y + labelH / 2} textAnchor="middle" dominantBaseline="middle" className="label-diagram__cell-caption">
              {settings.labelWidth}×{settings.labelHeight}mm
            </text>
          )}
        </g>
      </svg>
      <p className="label-diagram__caption muted">
        {settings.pageType === "roll" ? "1 label per page (roll)" : `${cols} × ${rows} = ${cols * rows} labels per page`}
      </p>
    </div>
  );
}
