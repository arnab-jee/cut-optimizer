import { LabelDiagram } from "./LabelDiagram";
import type { LabelPageType, LabelSettings } from "../types";

interface Props {
  settings: LabelSettings;
  onChange: (settings: LabelSettings) => void;
  // Manual, job-level fallbacks used only when a part's own data can't supply Client/Order No
  // (e.g. Panel Saw CSVs have no Client/Project column at all) -- not part of LabelSettings
  // itself since these are per-job values, not a reusable paper/format preset.
  clientNameOverride: string;
  onClientNameOverrideChange: (v: string) => void;
  orderNoOverride: string;
  onOrderNoOverrideChange: (v: string) => void;
}

interface QuickSize {
  label: string;
  pageType: LabelPageType;
  pageWidth: number;
  pageHeight: number;
  labelWidth: number;
  labelHeight: number;
}

// A convenience shortlist of real, commonly used label sizes -- not persisted entities, just a
// one-click way to prefill the width/height/page-type fields below.
const QUICK_SIZES: QuickSize[] = [
  { label: "A4 sheet — 3×8 (63.5×38.1mm)", pageType: "sheet", pageWidth: 210, pageHeight: 297, labelWidth: 63.5, labelHeight: 38.1 },
  { label: "A4 sheet — 2×7 (99.1×38.1mm)", pageType: "sheet", pageWidth: 210, pageHeight: 297, labelWidth: 99.1, labelHeight: 38.1 },
  { label: "Roll — 100×50mm", pageType: "roll", pageWidth: 100, pageHeight: 50, labelWidth: 100, labelHeight: 50 },
  { label: "Roll — 70×40mm", pageType: "roll", pageWidth: 70, pageHeight: 40, labelWidth: 70, labelHeight: 40 },
  { label: "Roll — 50×30mm", pageType: "roll", pageWidth: 50, pageHeight: 30, labelWidth: 50, labelHeight: 30 },
];

const A4 = { pageWidth: 210, pageHeight: 297 };
const LETTER = { pageWidth: 215.9, pageHeight: 279.4 };

export function LabelSettingsPanel({
  settings,
  onChange,
  clientNameOverride,
  onClientNameOverrideChange,
  orderNoOverride,
  onOrderNoOverrideChange,
}: Props) {
  function update<K extends keyof LabelSettings>(field: K, value: LabelSettings[K]) {
    onChange({ ...settings, [field]: value });
  }

  function applyQuickSize(size: QuickSize) {
    onChange({
      ...settings,
      pageType: size.pageType,
      pageWidth: size.pageWidth,
      pageHeight: size.pageHeight,
      labelWidth: size.labelWidth,
      labelHeight: size.labelHeight,
    });
  }

  const pageSizePreset =
    settings.pageWidth === A4.pageWidth && settings.pageHeight === A4.pageHeight
      ? "a4"
      : settings.pageWidth === LETTER.pageWidth && settings.pageHeight === LETTER.pageHeight
        ? "letter"
        : "custom";

  return (
    <fieldset className="params-section">
      <legend className="params-section__title">Label Settings</legend>

      <div className="label-quick-sizes">
        {QUICK_SIZES.map((size) => (
          <button key={size.label} type="button" className="btn btn--quiet" onClick={() => applyQuickSize(size)}>
            {size.label}
          </button>
        ))}
      </div>

      <div className="field-grid field-grid--2">
        <label className="field">
          <span className="field__label">Client name (if not in CSV)</span>
          <input
            type="text"
            placeholder="e.g. Mr. Vijay"
            value={clientNameOverride}
            onChange={(e) => onClientNameOverrideChange(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field__label">Order No (if not in CSV)</span>
          <input
            type="text"
            placeholder="e.g. 26Y130T3F14B1"
            value={orderNoOverride}
            onChange={(e) => onOrderNoOverrideChange(e.target.value)}
          />
        </label>
      </div>
      <p className="muted label-override-hint">
        Used only when a part's own data is missing this info — e.g. Panel Saw CSVs have no Client column at all.
      </p>

      <div className="label-settings-layout">
        <div className="label-settings-layout__fields">
          <div className="field-grid field-grid--2">
            <label className="field">
              <span className="field__label">Paper type</span>
              <select value={settings.pageType} onChange={(e) => update("pageType", e.target.value as LabelPageType)}>
                <option value="sheet">Sheet (e.g. A4)</option>
                <option value="roll">Continuous roll</option>
              </select>
            </label>
            <label className="field field--checkbox">
              <input type="checkbox" checked={settings.showQrCode} onChange={(e) => update("showQrCode", e.target.checked)} />
              <span className="field__label">Show QR code</span>
            </label>
          </div>

          <div className="field-grid field-grid--2">
            <label className="field">
              <span className="field__label">Label width (mm)</span>
              <input type="number" min="1" value={settings.labelWidth} onChange={(e) => update("labelWidth", Number(e.target.value))} />
            </label>
            <label className="field">
              <span className="field__label">Label height (mm)</span>
              <input type="number" min="1" value={settings.labelHeight} onChange={(e) => update("labelHeight", Number(e.target.value))} />
            </label>
          </div>

          {settings.pageType === "sheet" && (
            <>
              <div className="field-grid field-grid--2">
                <label className="field">
                  <span className="field__label">Page size</span>
                  <select
                    value={pageSizePreset}
                    onChange={(e) => {
                      if (e.target.value === "a4") onChange({ ...settings, ...A4 });
                      else if (e.target.value === "letter") onChange({ ...settings, ...LETTER });
                    }}
                  >
                    <option value="a4">A4 (210×297mm)</option>
                    <option value="letter">Letter (215.9×279.4mm)</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
                {pageSizePreset === "custom" && (
                  <label className="field">
                    <span className="field__label">Page width × height (mm)</span>
                    <span className="label-settings-inline-pair">
                      <input type="number" min="1" value={settings.pageWidth} onChange={(e) => update("pageWidth", Number(e.target.value))} />
                      <input type="number" min="1" value={settings.pageHeight} onChange={(e) => update("pageHeight", Number(e.target.value))} />
                    </span>
                  </label>
                )}
              </div>

              <div className="field-grid field-grid--4">
                <label className="field">
                  <span className="field__label">Margin top (mm)</span>
                  <input type="number" min="0" value={settings.marginTop} onChange={(e) => update("marginTop", Number(e.target.value))} />
                </label>
                <label className="field">
                  <span className="field__label">Margin right (mm)</span>
                  <input type="number" min="0" value={settings.marginRight} onChange={(e) => update("marginRight", Number(e.target.value))} />
                </label>
                <label className="field">
                  <span className="field__label">Margin bottom (mm)</span>
                  <input type="number" min="0" value={settings.marginBottom} onChange={(e) => update("marginBottom", Number(e.target.value))} />
                </label>
                <label className="field">
                  <span className="field__label">Margin left (mm)</span>
                  <input type="number" min="0" value={settings.marginLeft} onChange={(e) => update("marginLeft", Number(e.target.value))} />
                </label>
              </div>

              <div className="field-grid field-grid--2">
                <label className="field">
                  <span className="field__label">Gap between labels, horizontal (mm)</span>
                  <input type="number" min="0" value={settings.gapX} onChange={(e) => update("gapX", Number(e.target.value))} />
                </label>
                <label className="field">
                  <span className="field__label">Gap between labels, vertical (mm)</span>
                  <input type="number" min="0" value={settings.gapY} onChange={(e) => update("gapY", Number(e.target.value))} />
                </label>
              </div>
            </>
          )}

          <div className="field-grid field-grid--2">
            <label className="field field--checkbox">
              <input type="checkbox" checked={settings.showBarcode} onChange={(e) => update("showBarcode", e.target.checked)} />
              <span className="field__label">Show barcode</span>
            </label>
            <label className="field field--checkbox">
              <input type="checkbox" checked={settings.showCornerMarks} onChange={(e) => update("showCornerMarks", e.target.checked)} />
              <span className="field__label">Show corner marks</span>
            </label>
          </div>
        </div>

        <LabelDiagram settings={settings} />
      </div>
    </fieldset>
  );
}
