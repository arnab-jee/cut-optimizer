import { useEffect, useState } from "react";
import {
  ApiError,
  createLabelSettings,
  deleteLabelSettings,
  getSettings,
  listLabelSettings,
  setDefaultLabelSettingsId,
} from "../api";
import type { LabelSettings, PersistedLabelSettings } from "../types";

interface Props {
  // The bundle a saved entry would capture if the user hit "Save" right now — mirrors
  // PresetLibrary.tsx's `current` prop: "save" just names and persists whatever's already set
  // in LabelSettingsPanel.tsx, rather than a separate add-form asking for every field again.
  current: LabelSettings;
  onApply: (settings: LabelSettings) => void;
}

export function LabelSettingsLibrary({ current, onApply }: Props) {
  const [items, setItems] = useState<PersistedLabelSettings[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [defaultId, setDefaultId] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([listLabelSettings(), getSettings()])
      .then(([list, settings]) => {
        setItems(list);
        setDefaultId(settings.defaultLabelSettingsId);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load saved label settings"))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createLabelSettings({ ...current, name: name.trim() });
      setItems((prev) => [...prev, created]);
      setName("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to save label settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await deleteLabelSettings(id);
      setItems((prev) => prev.filter((s) => s.id !== id));
      if (defaultId === id) setDefaultId(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to delete label settings");
    }
  }

  async function handleSetDefault(id: number) {
    setError(null);
    try {
      await setDefaultLabelSettingsId(id);
      setDefaultId(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to set default label settings");
    }
  }

  return (
    <fieldset className="params-section">
      <legend className="params-section__title">Saved Label Settings</legend>
      {error && (
        <ul className="alert alert--error">
          <li>{error}</li>
        </ul>
      )}
      {loading ? (
        <p>Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No saved label settings yet — configure the layout above, then save it as one below.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Paper</th>
                <th>Label size (mm)</th>
                <th>Default</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td>{item.pageType === "roll" ? "Roll" : "Sheet"}</td>
                  <td>
                    {item.labelWidth} × {item.labelHeight}
                  </td>
                  <td>{item.id === defaultId ? "✓ Default" : ""}</td>
                  <td>
                    <button type="button" className="btn btn--quiet" onClick={() => onApply(item)}>
                      Use
                    </button>
                    {item.id !== defaultId && (
                      <button type="button" className="btn btn--quiet" onClick={() => handleSetDefault(item.id)}>
                        Set as default
                      </button>
                    )}
                    <button type="button" className="btn btn--quiet" onClick={() => handleDelete(item.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="field-grid field-grid--2">
        <label className="field">
          <span className="field__label">Save current label settings as</span>
          <input type="text" placeholder="e.g. Standard sheet labels" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      <button type="button" className="btn btn--secondary" onClick={handleSave} disabled={saving || !name.trim()}>
        {saving ? "Saving…" : "Save to library"}
      </button>
    </fieldset>
  );
}
