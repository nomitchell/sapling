export type ResearchSettings = {
  provider: string;
  model: string;
  reasoning_effort: string | null;
  cadence: number;
  budget_total: number;
  permission_mode: "ask" | "balanced" | "yolo";
  execution_backend: string;
  max_concurrent_holons: number;
  max_depth: number | null;
  experiment_timeout: number;
  max_output_tokens: number;
  max_turn_cost_usd: number;
  input_cost_per_million: number;
  output_cost_per_million: number;
  cached_input_cost_per_million?: number | null;
};

export const defaults: ResearchSettings = {
  provider: "openai", model: "gpt-5.4-nano", reasoning_effort: "low", cadence: 0.45,
  budget_total: 10, permission_mode: "balanced", execution_backend: "docker",
  max_concurrent_holons: 4, max_depth: null, experiment_timeout: 300,
  max_output_tokens: 32768, max_turn_cost_usd: 1,
  input_cost_per_million: 0.2, output_cost_per_million: 1.25, cached_input_cost_per_million: 0.02,
};

export type Project = {
  id: string; title: string; goal: string; status: string;
  research_state?: "planning" | "running" | "paused";
  active_conversation_id?: string | null;
  conversation_requests?: Record<string, { state: string }>;
  settings: ResearchSettings; budget_total: number; budget_spent: number;
  root_holon_id?: string; created_at: string;
};
export type Message = { id: string; role: string; text: string; created_at: string; channel?: "answer" | "progress"; node_ids?: string[]; attention_ids?: string[] };
export type ConversationReference = { nodeId: string; title: string; attentionIds?: string[] };
export type ProjectData = { messages: Message[]; tree: RecordItem[]; holarchy: RecordItem[]; claims: RecordItem[]; evidence: RecordItem[]; experiments: RecordItem[]; attention: RecordItem[]; events: ResearchEvent[]; stats: Stats; artifacts: RecordItem[] };
export const emptyData: ProjectData = { messages: [], tree: [], holarchy: [], claims: [], evidence: [], experiments: [], attention: [], events: [], stats: {}, artifacts: [] };
export type RecordItem = Record<string, unknown> & { id: string; title?: string; status?: string; created_at?: string };
export type ResearchEvent = { id: string; type: string; payload: Record<string, unknown>; created_at: string };
export type Stats = Record<string, unknown>;
export type Credential = { provider: string; configured?: boolean; present?: boolean; masked_key?: string; source?: string };

export async function loadEvents(projectId: string): Promise<ResearchEvent[]> {
  const events: ResearchEvent[] = [];
  let after = "0";
  while (true) {
    const batch = await api<ResearchEvent[]>(`/projects/${projectId}/events?after=${after}`);
    events.push(...batch);
    if (batch.length < 250) return compactEvents(events);
    after = batch[batch.length - 1].id;
  }
}

export function mergeResearchEvent(events: ResearchEvent[], event: ResearchEvent) {
  const streamId = String(event.payload.stream_id || "");
  if (event.type === "MODEL_STREAM") {
    const previous = [...events].reverse().find((item) =>
      item.type === "MODEL_STREAM" && String(item.payload.stream_id || "") === streamId
    );
    const next = previous?.payload.response_preview && !event.payload.response_preview
      ? { ...event, payload: { ...event.payload, response_preview: previous.payload.response_preview } }
      : event;
    return [...events.filter((item) =>
      item.type !== "MODEL_STREAM" || String(item.payload.stream_id || "") !== streamId
    ), next];
  }
  if (events.some((item) => item.id === event.id)) return events;
  return [...events, event];
}

function compactEvents(events: ResearchEvent[]) {
  const completed = new Set(
    events
      .filter((event) => event.type === "MODEL_TURN")
      .map((event) => String(event.payload.stream_id || "")),
  );
  const lastLive = new Map<string, ResearchEvent>();
  for (const event of events) {
    if (event.type !== "MODEL_STREAM") continue;
    const streamId = String(event.payload.stream_id || "");
    if (!completed.has(streamId)) lastLive.set(streamId, event);
  }
  return [
    ...events.filter((event) => event.type !== "MODEL_STREAM"),
    ...lastLive.values(),
  ].sort((a, b) => Number(a.id) - Number(b.id));
}

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
  provider?: string; key?: string; reasoning_control?: "effort" | "toggle" | "fixed" | "none";
  default_reasoning_effort?: string | null; reasoning_note?: string;
  input_cost_per_million: number; output_cost_per_million: number;
  cached_input_cost_per_million?: number | null; price_verified_at?: string; available?: boolean | null;
};
export type ModelCatalog = { models: ModelOption[]; default_model: string };
export const fallbackModelCatalog: ModelCatalog = {
  default_model: "gpt-5.4-nano",
  models: [
    { id: "gpt-6-astra", label: "GPT-6 Astra", reasoning_efforts: ["low", "medium", "high", "xhigh", "max"], input_cost_per_million: 10, cached_input_cost_per_million: 1, output_cost_per_million: 50 },
    { id: "gpt-5.6-sol", label: "GPT-5.6 Sol", reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"], input_cost_per_million: 4, cached_input_cost_per_million: 0.4, output_cost_per_million: 20 },
    { id: "gpt-5.6-terra", label: "GPT-5.6 Terra", reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"], input_cost_per_million: 2, cached_input_cost_per_million: 0.2, output_cost_per_million: 12 },
    { id: "gpt-5.6-luna", label: "GPT-5.6 Luna", reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"], input_cost_per_million: 0.2, cached_input_cost_per_million: 0.02, output_cost_per_million: 1.2 },
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
