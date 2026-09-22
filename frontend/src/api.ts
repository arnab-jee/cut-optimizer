import type { ImportXmlResult, LabelSettings, OptRequest, OptResult, Part, PersistedLabelSettings, Preset, StockBoardWithCost, WasteStrategy } from "./types";

export class ApiError extends Error {
  errors: string[];

  constructor(errors: string[]) {
    super(errors.join("; "));
    this.name = "ApiError";
    this.errors = errors;
  }
}

async function handleErrors(res: Response): Promise<Response> {
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    const errors = Array.isArray(detail) ? detail.map(String) : [detail ?? res.statusText];
    throw new ApiError(errors);
  }
  return res;
}

async function postJson(path: string, body: unknown): Promise<Response> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleErrors(res);
}

async function getJson(path: string): Promise<Response> {
  return handleErrors(await fetch(`/api${path}`));
}

async function putJson(path: string, body: unknown): Promise<Response> {
  const res = await fetch(`/api${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleErrors(res);
}

async function deleteJson(path: string): Promise<Response> {
  return handleErrors(await fetch(`/api${path}`, { method: "DELETE" }));
}

export type PersistedStockBoard = StockBoardWithCost & { id: number };

export async function listStockBoards(): Promise<PersistedStockBoard[]> {
  return (await getJson("/stock-boards")).json();
}

export async function createStockBoard(board: StockBoardWithCost): Promise<PersistedStockBoard> {
  return (await postJson("/stock-boards", board)).json();
}

export async function updateStockBoard(id: number, board: StockBoardWithCost): Promise<PersistedStockBoard> {
  return (await putJson(`/stock-boards/${id}`, board)).json();
}

export async function deleteStockBoard(id: number): Promise<void> {
  await deleteJson(`/stock-boards/${id}`);
}

export async function getSettings(): Promise<{ wasteStrategyDefault: WasteStrategy; defaultLabelSettingsId: number | null }> {
  return (await getJson("/settings")).json();
}

export async function setWasteStrategyDefault(value: WasteStrategy): Promise<void> {
  await putJson("/settings", { wasteStrategyDefault: value });
}

export async function setDefaultLabelSettingsId(id: number | null): Promise<void> {
  await putJson("/settings", { defaultLabelSettingsId: id });
}

export type PersistedPreset = Preset & { id: number };

export async function listPresets(): Promise<PersistedPreset[]> {
  return (await getJson("/presets")).json();
}

export async function createPreset(preset: Preset): Promise<PersistedPreset> {
  return (await postJson("/presets", preset)).json();
}

export async function deletePreset(id: number): Promise<void> {
  await deleteJson(`/presets/${id}`);
}

export async function listLabelSettings(): Promise<PersistedLabelSettings[]> {
  return (await getJson("/label-settings")).json();
}

export async function createLabelSettings(settings: LabelSettings & { name: string }): Promise<PersistedLabelSettings> {
  return (await postJson("/label-settings", settings)).json();
}

export async function updateLabelSettings(id: number, settings: LabelSettings & { name: string }): Promise<PersistedLabelSettings> {
  return (await putJson(`/label-settings/${id}`, settings)).json();
}

export async function deleteLabelSettings(id: number): Promise<void> {
  await deleteJson(`/label-settings/${id}`);
}

export async function importNanxingXml(xmlText: string): Promise<ImportXmlResult> {
  return (await postJson("/import/xml", { xml_text: xmlText })).json();
}

export async function parseCsv(csvText: string): Promise<{ parts: Part[] }> {
  return (await postJson("/parse", { csv_text: csvText })).json();
}

export async function optimize(request: OptRequest): Promise<OptResult> {
  return (await postJson("/optimize", request)).json();
}

async function fetchExportBlob(path: string, request: OptRequest): Promise<Blob> {
  return (await postJson(path, request)).blob();
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function timestamp(): string {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

export async function downloadPdf(
  request: OptRequest,
  overrides: { clientNameOverride: string; orderNoOverride: string },
  projectName: string,
): Promise<void> {
  const res = await postJson("/export/pdf", { ...request, ...overrides });
  const blob = await res.blob();
  triggerDownload(blob, `${projectName}-${timestamp()}.pdf`);
}

export async function downloadXml(request: OptRequest, projectName: string): Promise<void> {
  const blob = await fetchExportBlob("/export/xml", request);
  triggerDownload(blob, `${projectName}-${timestamp()}.xml`);
}

export async function downloadLabels(
  request: OptRequest,
  labelSettings: LabelSettings,
  overrides: { clientNameOverride: string; orderNoOverride: string },
  projectName: string,
): Promise<void> {
  const res = await postJson("/export/labels", { ...request, labelSettings, ...overrides });
  const blob = await res.blob();
  triggerDownload(blob, `${projectName}-labels-${timestamp()}.pdf`);
}
