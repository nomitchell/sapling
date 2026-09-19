"use client";

import {
  api,
  Credential,
  defaults,
  errorText,
  fallbackModelCatalog,
  loadModelCatalog,
  ModelCatalog,
  Project,
  ResearchSettings,
} from "@/lib/api";
import {
  ChevronDown,
  Palette,
  Plug,
  SlidersHorizontal,
  Sparkles,
  Workflow,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Appearance, Theme } from "./appearance";
import { Modal } from "./ui";

type Section = "general" | "models" | "research" | "connections" | "appearance";
const sections = [
  { id: "general", name: "General", icon: SlidersHorizontal },
  { id: "models", name: "Models", icon: Sparkles },
  { id: "research", name: "Research", icon: Workflow },
  { id: "connections", name: "Connections", icon: Plug },
  { id: "appearance", name: "Appearance", icon: Palette },
] as const;
const efforts: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
};

function Row({
  name,
  hint,
  children,
}: {
  name: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="setting-row">
      <span>
        {name}
        {hint && <small>{hint}</small>}
      </span>
      {children}
    </label>
  );
}

export function ModelFields({
  value,
  onChange,
}: {
  value: ResearchSettings;
  onChange: (value: ResearchSettings) => void;
}) {
  const [catalog, setCatalog] = useState<ModelCatalog>(fallbackModelCatalog);
  const [custom, setCustom] = useState(false);
  const [pricingOpen, setPricingOpen] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void loadModelCatalog()
      .then((result) => {
        if (active) setCatalog(result);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, []);
  const selected = catalog.models.find((model) => model.id === value.model);
  const set = (key: keyof ResearchSettings, next: string | number | null) =>
    onChange({ ...value, [key]: next });
  function selectModel(id: string) {
    if (id === "custom") {
      setCustom(true);
      return;
    }
    const model = catalog.models.find((item) => item.id === id);
    if (model) {
      const effort =
        value.reasoning_effort &&
        model.reasoning_efforts.includes(value.reasoning_effort)
          ? value.reasoning_effort
          : "low";
      onChange({
        ...value,
        model: id,
        reasoning_effort: effort,
        input_cost_per_million: model.input_cost_per_million,
        output_cost_per_million: model.output_cost_per_million,
        cached_input_cost_per_million:
          model.cached_input_cost_per_million ?? null,
      });
    }
    setCustom(false);
  }
  const needsPrices =
    !selected &&
    (!value.input_cost_per_million || !value.output_cost_per_million);
  return (
    <>
      <Row name="Model">
        <select
          aria-label="Model"
          value={custom || !selected ? "custom" : value.model}
          onChange={(event) => selectModel(event.target.value)}
        >
          {catalog.models.map((model) => (
            <option
              key={model.id}
              value={model.id}
              disabled={model.available === false}
            >
              {model.label}
              {model.available === false ? " (unavailable)" : ""}
            </option>
          ))}
          <option value="custom">Custom model…</option>
        </select>
      </Row>
      {(custom || !selected) && (
        <Row name="Model identifier">
          <input
            className="wide-input"
            aria-label="Model identifier"
            value={value.model}
            onChange={(event) => {
              const id = event.target.value;
              if (catalog.models.some((item) => item.id === id)) {
                selectModel(id);
              } else
                onChange({
                  ...value,
                  model: id,
                  reasoning_effort: null,
                  input_cost_per_million: 0,
                  output_cost_per_million: 0,
                  cached_input_cost_per_million: null,
                });
            }}
          />
        </Row>
      )}
      <Row name="Reasoning">
        <select
          aria-label="Reasoning"
          value={value.reasoning_effort ?? "default"}
          onChange={(event) =>
            set(
              "reasoning_effort",
              event.target.value === "default" ? null : event.target.value,
            )
          }
        >
          {!selected && <option value="default">Model default</option>}
          {(selected?.reasoning_efforts || Object.keys(efforts)).map(
            (effort) => (
              <option key={effort} value={effort}>
                {efforts[effort]}
              </option>
            ),
          )}
        </select>
      </Row>
      {error && (
        <p className="settings-note">
          Using the saved model catalog. Account availability could not be
          checked.
        </p>
      )}
      <details
        className="settings-details"
        open={pricingOpen || needsPrices}
        onToggle={(event) => setPricingOpen(event.currentTarget.open)}
      >
        <summary>
          Token pricing {needsPrices ? "— required for this model" : ""}
        </summary>
        <p className="settings-note">
          USD per million tokens. Presets include reviewed rates.
        </p>
        {(
          [
            ["input_cost_per_million", "Input"],
            ["output_cost_per_million", "Output"],
            ["cached_input_cost_per_million", "Cached input"],
          ] as const
        ).map(([key, name]) => (
          <Row name={name} key={key}>
            <input
              aria-label={name + " price"}
              type="number"
              min="0"
              step="0.001"
              value={value[key] ?? ""}
              placeholder="Full input rate"
              onChange={(event) =>
                set(
                  key,
                  event.target.value === "" &&
                    key === "cached_input_cost_per_million"
                    ? null
                    : Number(event.target.value),
                )
              }
            />
          </Row>
        ))}
      </details>
    </>
  );
}

export function SettingsPanel({
  project,
  globalSettings,
  onClose,
  onSaved,
  onDelete,
  theme,
  onTheme,
  initialSection = "general",
}: {
  project: Project | null;
  globalSettings: ResearchSettings;
  onClose: () => void;
  onSaved: () => void;
  onDelete?: () => void;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  initialSection?: Section;
}) {
  const [section, setSection] = useState<Section>(initialSection);
  const [config, setConfig] = useState<ResearchSettings>({
    ...defaults,
    ...globalSettings,
    ...project?.settings,
  });
  const [title, setTitle] = useState(project?.title || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key: keyof ResearchSettings, value: string | number) =>
    setConfig((current) => ({ ...current, [key]: value }));
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api(project ? `/projects/${project.id}` : "/settings", {
        method: "PATCH",
        body: JSON.stringify(
          project ? { title: title.trim(), settings: config } : config,
        ),
      });
      onSaved();
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={project ? "Project settings" : "Settings"}
      onClose={onClose}
      wide
    >
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {sections.map((item) => (
            <button
              key={item.id}
              className={section === item.id ? "active" : ""}
              onClick={() => setSection(item.id)}
            >
              <item.icon size={15} />
              {item.name}
            </button>
          ))}
        </nav>
        <section className="settings-pane">
          <h3>{sections.find((item) => item.id === section)?.name}</h3>
          <p>
            {section === "general"
              ? project
                ? "Preferences for this project."
                : "Defaults for new projects."
              : section === "models"
                ? "Choose how Sapling thinks with you."
                : section === "research"
                  ? "Limits for background research."
                  : section === "connections"
                    ? "Keys stay in your Windows credential vault."
                    : "Choose your working environment."}
          </p>
          {section === "general" && (
            <>
              {project && (
                <Row name="Project title">
                  <input
                    className="wide-input"
                    aria-label="Project title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </Row>
              )}
              <Row name="Permissions" hint="When to ask before taking action.">
                <select
                  aria-label="Permissions"
                  value={config.permission_mode}
                  onChange={(event) =>
                    set("permission_mode", event.target.value)
                  }
                >
                  <option value="ask">Ask by category</option>
                  <option value="balanced">Ask for risky actions</option>
                  <option value="yolo">Full autonomy</option>
                </select>
              </Row>
              <Row
                name="Collaboration"
                hint="How often to consult you on research decisions."
              >
                <select
                  aria-label="Collaboration"
                  value={
                    config.cadence < 0.34
                      ? "0.2"
                      : config.cadence > 0.66
                        ? "0.8"
                        : "0.5"
                  }
                  onChange={(event) =>
                    set("cadence", Number(event.target.value))
                  }
                >
                  <option value="0.2">Work closely together</option>
                  <option value="0.5">Balanced</option>
                  <option value="0.8">Continue independently</option>
                </select>
              </Row>
              <Row name="Research budget" hint="USD per project.">
                <input
                  type="number"
                  aria-label="Research budget"
                  min="0"
                  step="0.1"
                  value={config.budget_total}
                  onChange={(event) =>
                    set("budget_total", Number(event.target.value))
                  }
                />
              </Row>
            </>
          )}
          {section === "general" && project && onDelete && (
            <div className="setting-row">
              <span>Delete project</span>
              <button className="button secondary" onClick={onDelete}>
                Delete project…
              </button>
            </div>
          )}
          {section === "models" && (
            <ModelFields value={config} onChange={setConfig} />
          )}
          {section === "research" && (
            <>
              <Row
                name="Execution"
                hint={
                  config.execution_backend === "process"
                    ? "Local code uses your Windows account’s access."
                    : undefined
                }
              >
                <select
                  aria-label="Execution"
                  value={config.execution_backend}
                  onChange={(event) =>
                    set("execution_backend", event.target.value)
                  }
                >
                  <option value="process">Local process</option>
                  <option value="docker">Docker container</option>
                </select>
              </Row>
              <Row name="Maximum simultaneous researchers">
                <input
                  aria-label="Maximum simultaneous researchers"
                  type="number"
                  min="1"
                  max="32"
                  value={config.max_concurrent_holons}
                  onChange={(event) =>
                    set("max_concurrent_holons", Number(event.target.value))
                  }
                />
              </Row>
              <details className="settings-details">
                <summary>Advanced limits</summary>
                {(
                  [
                    ["max_depth", "Maximum depth", 1, 20],
                    [
                      "experiment_timeout",
                      "Experiment timeout (seconds)",
                      1,
                      86400,
                    ],
                    ["max_output_tokens", "Output tokens per turn", 256, 64000],
                    [
                      "max_turn_cost_usd",
                      "Maximum cost per turn ($)",
                      0.01,
                      1000,
                    ],
                  ] as const
                ).map(([key, name, min, max]) => (
                  <Row key={key} name={name}>
                    <input
                      type="number"
                      min={min}
                      max={max}
                      step={key === "max_turn_cost_usd" ? 0.01 : 1}
                      value={config[key]}
                      onChange={(event) => set(key, Number(event.target.value))}
                    />
                  </Row>
                ))}
              </details>
            </>
          )}
          {section === "connections" && <Connections />}
          {section === "appearance" && (
            <Appearance theme={theme} onChange={onTheme} />
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
        </section>
      </div>
      <footer className="settings-save">
        <small>
          {section === "appearance"
            ? "Appearance saves immediately."
            : project
              ? "Applies to this project."
              : "Existing projects keep their settings."}
        </small>
        <button className="button secondary" onClick={onClose}>
          Close
        </button>
        <button
          className="button primary"
          disabled={busy || (!!project && !title.trim())}
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </footer>
    </Modal>
  );
}

function Connections() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = () =>
    void api<Credential[]>("/credentials")
      .then(setCredentials)
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoading(false));
  useEffect(load, []);
  return (
    <>
      {["openai", "openalex", "tavily"].map((provider) => (
        <Connection
          key={provider}
          provider={provider}
          connected={credentials.some(
            (item) => item.provider === provider && item.configured,
          )}
          loading={loading}
          onSaved={load}
        />
      ))}
      {error && <p className="form-error">{error}</p>}
    </>
  );
}
function Connection({
  provider,
  connected,
  loading,
  onSaved,
}: {
  provider: string;
  connected: boolean;
  loading: boolean;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    try {
      await api("/credentials", {
        method: "POST",
        body: JSON.stringify({ provider, key }),
      });
      setKey("");
      setOpen(false);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="connection-row">
        <div>
          <strong>
            {provider === "openai"
              ? "OpenAI"
              : provider === "tavily"
                ? "Tavily"
                : "OpenAlex"}
          </strong>
          <small>
            {loading
              ? "Checking…"
              : connected
                ? "Connected"
                : provider === "openalex"
                  ? "Optional · papers work without a key"
                  : "Not connected"}
          </small>
        </div>
        <button className="button secondary" onClick={() => setOpen(!open)}>
          {connected ? "Replace key" : "Connect"}
          <ChevronDown size={12} />
        </button>
      </div>
      {open && (
        <div className="key-editor">
          <input
            type="password"
            autoComplete="new-password"
            aria-label={provider + " API key"}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="API key"
          />
          <button
            className="button primary"
            disabled={busy || key.length < 5}
            onClick={() => void save()}
          >
            Save key
          </button>
        </div>
      )}
      {error && <p className="form-error">{error}</p>}
    </>
  );
}
