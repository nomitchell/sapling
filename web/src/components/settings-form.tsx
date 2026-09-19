"use client";

import { useEffect, useState } from "react";
import { fallbackModelCatalog, loadModelCatalog, ModelCatalog, ResearchSettings } from "@/lib/api";

const reasoningLabels: Record<string, string> = { none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Maximum" };

export function SettingsForm({ value, onChange, compact = false }: { value: ResearchSettings; onChange: (value: ResearchSettings) => void; compact?: boolean }) {
  const set = (key: keyof ResearchSettings, next: string | number | null) => onChange({ ...value, [key]: next });
  const [catalog, setCatalog] = useState<ModelCatalog>(fallbackModelCatalog);
  const [custom, setCustom] = useState(false);
  const [catalogError, setCatalogError] = useState(false);
  const selected = catalog.models.find(model => model.id === value.model);
  const usingCustom = custom || (!!value.model && !selected);
  const efforts = selected?.reasoning_efforts || Object.keys(reasoningLabels);
  const effectiveEffort = selected ? (value.reasoning_effort && efforts.includes(value.reasoning_effort) ? value.reasoning_effort : efforts.includes("low") ? "low" : efforts[0] || "none") : value.reasoning_effort ?? "__model_default__";
  useEffect(() => {
    let cancelled = false;
    void loadModelCatalog().then(result => { if (!cancelled) { setCatalog(result); setCatalogError(false); } }).catch(() => { if (!cancelled) setCatalogError(true); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (selected && value.reasoning_effort !== effectiveEffort) onChange({ ...value, reasoning_effort: effectiveEffort });
  }, [selected, value, effectiveEffort, onChange]);
  function chooseModel(id: string) {
    if (id === "__custom__") { setCustom(true); return; }
    setCustom(false);
    const next = catalog.models.find(model => model.id === id);
    if (!next) { set("model", id); return; }
    const effort = value.reasoning_effort && next.reasoning_efforts.includes(value.reasoning_effort) ? value.reasoning_effort : next.reasoning_efforts.includes("low") ? "low" : next.reasoning_efforts[0] || "none";
    onChange({ ...value, model: next.id, reasoning_effort: effort, input_cost_per_million: next.input_cost_per_million, output_cost_per_million: next.output_cost_per_million, cached_input_cost_per_million: next.cached_input_cost_per_million ?? null });
  }

  return <div className="settings-fields">
    <div className="form-section-title"><span>01</span> Intelligence</div>
    <label>Model
      <select aria-label="Model" value={usingCustom ? "__custom__" : value.model} onChange={event => chooseModel(event.target.value)}>
        {!value.model && <option value="" disabled>Choose a model</option>}
        {catalog.models.map(model => <option key={model.id} value={model.id} disabled={model.available === false}>{model.label}{model.id === catalog.default_model ? " - economical default" : ""}{model.available === false ? " - unavailable" : ""}</option>)}
        <option value="__custom__">Custom model identifier...</option>
      </select>
      <small>{selected ? "Rates fill automatically when you choose a listed model. You can adjust them under advanced pricing." : "Use an exact model identifier available to your OpenAI account."}</small>
    </label>
    {usingCustom && <label>Custom model identifier<input aria-label="Custom model identifier" value={value.model} onChange={event => onChange({ ...value, model: event.target.value, reasoning_effort: null, cached_input_cost_per_million: null })} placeholder="Enter an OpenAI model ID" autoComplete="off" /><small>Set compatible reasoning and token prices for your custom model below.</small></label>}
    {catalogError && <p className="field-note model-catalog-note">The live model catalog is unavailable. Saved model choices are shown.</p>}
    <div className="form-grid">
      <label>Reasoning effort<select aria-label="Reasoning effort" value={effectiveEffort} onChange={event => set("reasoning_effort", event.target.value === "__model_default__" ? null : event.target.value)}>{!selected && <option value="__model_default__">Model default</option>}{efforts.map(effort => <option key={effort} value={effort}>{reasoningLabels[effort] || effort}</option>)}</select><small>{selected ? "Higher effort may use more time and tokens." : "Choose Model default when your model does not support reasoning controls."}</small></label>
      <label>Budget (USD)<input type="number" min="0" step="0.1" value={value.budget_total} onChange={e => set("budget_total", Number(e.target.value))} /></label>
    </div>
    <label className="range-label"><span>Human collaboration cadence <strong>{Math.round(value.cadence * 100)}%</strong></span><input type="range" min="0" max="1" step="0.05" value={value.cadence} onChange={e => set("cadence", Number(e.target.value))} /><span className="range-ends"><small>More human input</small><small>More autonomy</small></span></label>
    <div className="form-section-title"><span>02</span> Permissions</div>
    <div className="permission-options">
      {([{ mode: "ask", title: "Ask first", text: "Approve categories of actions before they run." }, { mode: "balanced", title: "Balanced", text: "Routine research proceeds; risky actions need approval." }, { mode: "yolo", title: "Full autonomy", text: "Allow actions without approval, within your budget." }] as const).map(option => <label className={`permission-option ${value.permission_mode === option.mode ? "selected" : ""}`} key={option.mode}><input type="radio" name={compact ? "project-permissions" : "default-permissions"} value={option.mode} checked={value.permission_mode === option.mode} onChange={() => set("permission_mode", option.mode)} /><span><strong>{option.title}</strong><small>{option.text}</small></span></label>)}
    </div>
    {!compact && <>
      <div className="form-section-title"><span>03</span> Local execution</div>
      <label>Execution environment<select value={value.execution_backend} onChange={e => set("execution_backend", e.target.value)}><option value="docker">Local Docker container</option><option value="process">Local process</option></select><small>Local process execution gives research code your user account’s file and network access. Choose a container for isolation.</small></label>
      <div className="form-grid"><label>Parallel researchers<input type="number" min="1" max="32" step="1" value={value.max_concurrent_holons} onChange={e => set("max_concurrent_holons", Number(e.target.value))} /></label><label>Maximum depth<input type="number" min="1" max="20" step="1" value={value.max_depth} onChange={e => set("max_depth", Number(e.target.value))} /></label></div>
      <label>Experiment timeout (seconds)<input type="number" min="1" max="86400" value={value.experiment_timeout} onChange={e => set("experiment_timeout", Number(e.target.value))} /></label>
      <div className="form-grid"><label>Output tokens per turn<input type="number" min="256" max="64000" step="1" value={value.max_output_tokens} onChange={e => set("max_output_tokens", Number(e.target.value))} /></label><label>Maximum cost per turn ($)<input type="number" min="0.01" max="1000" step="0.01" value={value.max_turn_cost_usd} onChange={e => set("max_turn_cost_usd", Number(e.target.value))} /></label></div>
    </>}
    <div className="form-section-title"><span>{compact ? "03" : "04"}</span> Cost estimates</div>
    <div className="pricing-summary"><span>Per million tokens</span><strong>{"$" + value.input_cost_per_million.toFixed(2)} input <span>/</span> {"$" + value.output_cost_per_million.toFixed(2)} output</strong></div>
    <details className="advanced-pricing">
      <summary>Advanced pricing</summary>
      <p className="field-note">Rates estimate model usage in USD. Other service charges are excluded. Verify custom rates against your provider plan.</p>
      <div className="form-grid"><label>Input / million tokens ($)<input type="number" min="0" step="0.001" value={value.input_cost_per_million} onChange={event => set("input_cost_per_million", Number(event.target.value))} /></label><label>Output / million tokens ($)<input type="number" min="0" step="0.001" value={value.output_cost_per_million} onChange={event => set("output_cost_per_million", Number(event.target.value))} /></label></div>
      <label>Cached input / million tokens ($)<input type="number" min="0" step="0.001" value={value.cached_input_cost_per_million ?? ""} placeholder="Use the full input rate" onChange={event => set("cached_input_cost_per_million", event.target.value === "" ? null : Number(event.target.value))} /><small>Leave blank to use the full input rate. Enter a discounted rate only when your provider applies one.</small></label>
    </details>
  </div>;
}
