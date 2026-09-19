export type ResearchSettings = {
  provider: string;
  model: string;
  reasoning_effort: string | null;
  cadence: number;
  budget_total: number;
  permission_mode: "ask" | "balanced" | "yolo";
  execution_backend: string;
  max_concurrent_holons: number;
  max_depth: number;
  experiment_timeout: number;
  max_output_tokens: number;
  max_turn_cost_usd: number;
  input_cost_per_million: number;
  output_cost_per_million: number;
  cached_input_cost_per_million?: number | null;
};

export const defaults: ResearchSettings = {
  provider: "openai", model: "gpt-5.4-nano", reasoning_effort: "low", cadence: 0.5,
  budget_total: 10, permission_mode: "balanced", execution_backend: "docker",
  max_concurrent_holons: 4, max_depth: 5, experiment_timeout: 300,
  max_output_tokens: 4096, max_turn_cost_usd: 1,
  input_cost_per_million: 0.2, output_cost_per_million: 1.25, cached_input_cost_per_million: 0.02,
};

export type Project = {
  id: string; title: string; goal: string; status: string;
  settings: ResearchSettings; budget_total: number; budget_spent: number;
  root_holon_id?: string; created_at: string;
};
export type Message = { id: string; role: string; text: string; created_at: string };
export type RecordItem = Record<string, unknown> & { id: string; title?: string; status?: string; created_at?: string };
export type ResearchEvent = { id: string; type: string; payload: Record<string, unknown>; created_at: string };
export type Stats = Record<string, unknown>;
export type Credential = { provider: string; configured?: boolean; present?: boolean; masked_key?: string; source?: string };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...options.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : data.detail ? JSON.stringify(data.detail) : message;
    } catch { /* Preserve HTTP error if proxy response is not JSON. */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return text ? JSON.parse(text) as T : undefined as T;
}


export type ModelOption = {
  id: string; label: string; reasoning_efforts: string[];
  input_cost_per_million: number; output_cost_per_million: number;
  cached_input_cost_per_million?: number | null; price_verified_at?: string; available?: boolean | null;
};
export type ModelCatalog = { models: ModelOption[]; default_model: string };
export const fallbackModelCatalog: ModelCatalog = {
  default_model: "gpt-5.4-nano",
  models: [
    { id: "gpt-5.4-nano", label: "GPT-5.4 nano", reasoning_efforts: ["none", "low", "medium", "high", "xhigh"], input_cost_per_million: 0.2, output_cost_per_million: 1.25, cached_input_cost_per_million: 0.02 },
    { id: "gpt-5.4-mini", label: "GPT-5.4 mini", reasoning_efforts: ["none", "low", "medium", "high", "xhigh"], input_cost_per_million: 0.75, output_cost_per_million: 4.5 },
    { id: "gpt-5-mini", label: "GPT-5 mini", reasoning_efforts: ["minimal", "low", "medium", "high"], input_cost_per_million: 0.25, output_cost_per_million: 2 },
  ],
};
let modelCatalogRequest: Promise<ModelCatalog> | null = null;
export function loadModelCatalog(): Promise<ModelCatalog> {
  if (!modelCatalogRequest) modelCatalogRequest = api<ModelCatalog>("/models").catch(error => { modelCatalogRequest = null; throw error; });
  return modelCatalogRequest;
}

export function errorText(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Please try again."; }
export function money(value: unknown) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value) || 0); }
export function label(value: unknown) { return String(value ?? "").replaceAll("_", " ").replaceAll("-", " "); }
export function date(value?: string) { if (!value) return ""; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "" : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(parsed); }
export function field(record: Record<string, unknown>, ...keys: string[]) { for (const key of keys) { const value = record[key]; if (typeof value === "string" && value) return value; } return ""; }
