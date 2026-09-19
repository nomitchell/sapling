"use client";

import {
  api,
  date,
  errorText,
  field,
  label,
  Message,
  Project,
  ProjectData,
  RecordItem,
  ResearchEvent,
  ConversationReference,
} from "@/lib/api";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Loader2,
  Paperclip,
  ShieldCheck,
  Sparkles,
  Square,
  GitBranch,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Mark, Markdown } from "./ui";

const COMPOSER_MAX_HEIGHT = 144;
const COMPOSER_MIN_HEIGHT = 45;

function resizeComposer(textarea: HTMLTextAreaElement) {
  textarea.style.height = "0px";
  textarea.style.overflowY = "hidden";
  const contentHeight = textarea.scrollHeight;
  textarea.style.height = `${Math.min(COMPOSER_MAX_HEIGHT, Math.max(COMPOSER_MIN_HEIGHT, contentHeight))}px`;
  textarea.style.overflowY = contentHeight > COMPOSER_MAX_HEIGHT ? "auto" : "hidden";
}

function tokenCount(value: number) {
  if (value < 1_000) return String(value);
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)}k`;
}

export function Conversation({
  project,
  data,
  connected,
  onRefresh,
  onSettings,
  onError,
  reference,
  onClearReference,
  visible = true,
}: {
  project: Project;
  data: ProjectData;
  connected: boolean;
  onRefresh: () => void;
  onSettings: () => void;
  onError: (error: string) => void;
  reference?: ConversationReference | null;
  onClearReference?: () => void;
  visible?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  const file = useRef<HTMLInputElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const root = data.holarchy.find((item) => item.id === project.root_holon_id);
  const jobs = (
    Array.isArray(data.stats.jobs) ? data.stats.jobs : []
  ) as RecordItem[];
  const scope = project.active_conversation_id ? `conversation:${project.active_conversation_id}` : null;
  const rootJobs = jobs.filter(job => {
    const payload = (job.payload || {}) as Record<string, unknown>;
    return scope && payload.work_scope === scope && ["running", "queued"].includes(String(job.state));
  });
  const busy =
    sending ||
    (!root?.chat_stopped && rootJobs.length > 0);
  const pending = data.attention.filter(
    (item) =>
      item.status === "pending" && item.holon_id === project.root_holon_id,
  );
  const problem = pending.find(
    (item) => !["research_decision", "permission"].includes(String(item.type)),
  );
  const question = pending.find((item) => item.type === "research_decision");
  const lastInput = [...data.events]
    .reverse()
    .find(
      (event) =>
        event.type === "HUMAN_INPUT" || event.type === "CONVERSATION_RETRIED",
    );
  const turnEvents = data.events.filter(
    (event) =>
      (!lastInput || Number(event.id) > Number(lastInput.id)) &&
      event.payload.holon_id === project.root_holon_id,
  );
  const activity = [...turnEvents]
    .reverse()
    .find((event) =>
      [
        "MODEL_STARTED",
        "MODEL_STREAM",
        "MODEL_RETRYING",
        "TOOL_STARTED",
        "STALE_TURN_DISCARDED",
        "MODEL_TURN",
      ].includes(event.type),
    );
  const tools = turnEvents.filter((event) => event.type === "TOOL_STARTED");
  const completedStreams = new Set(
    turnEvents
      .filter((event) => event.type === "MODEL_TURN")
      .map((event) => String(event.payload.stream_id || "")),
  );
  const liveStream = [...turnEvents]
    .reverse()
    .find(
      (event) =>
        event.type === "MODEL_STREAM" &&
        !completedStreams.has(String(event.payload.stream_id || "")),
    );
  const usage = turnEvents.reduce(
    (total, event) => {
      if (event.type !== "MODEL_TURN") return total;
      const value = (event.payload.usage || {}) as Record<string, unknown>;
      total.input += Number(value.input_tokens) || 0;
      total.output += Number(value.output_tokens) || 0;
      return total;
    },
    {
      input: Number(liveStream?.payload.input_tokens) || 0,
      output: Number(liveStream?.payload.output_tokens) || 0,
      estimated: Boolean(liveStream?.payload.estimated),
    },
  );
  const timeline = conversationTimeline(data, project.root_holon_id);
  const started =
    turnEvents.find((event) => event.type === "JOB_STARTED")?.created_at ||
    lastInput?.created_at;
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    const tick = () =>
      setElapsed(
        Math.max(
          0,
          Math.floor(
            (Date.now() - new Date(started || Date.now()).getTime()) / 1000,
          ),
        ),
      );
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [busy, started]);
  useEffect(() => {
    if (atBottom && scroll.current)
      scroll.current.scrollTo({
        top: scroll.current.scrollHeight,
        behavior: "smooth",
      });
  }, [data.messages.length, tools.length, atBottom]);
  useEffect(() => { if (visible) input.current?.focus(); }, [visible, reference?.nodeId]);
  useEffect(() => {
    const textarea = input.current;
    if (!textarea) return;
    resizeComposer(textarea);
  }, [draft, visible]);
  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || sending) return;
    const message = draft.trim();
    setSending(true);
    setAtBottom(true);
    try {
      await api(`/projects/${project.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ text: message, node_ids: reference ? [reference.nodeId] : [], attention_ids: reference?.attentionIds || [] }),
      });
      setDraft("");
      onRefresh();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setSending(false);
      input.current?.focus();
    }
  }
  async function stop() {
    setStopping(true);
    try {
      await api(`/projects/${project.id}/conversation/stop`, {
        method: "POST",
      });
      onRefresh();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setStopping(false);
    }
  }
  async function retry() {
    try {
      await api(`/projects/${project.id}/conversation/retry`, {
        method: "POST",
      });
      onRefresh();
    } catch (err) {
      onError(errorText(err));
    }
  }
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const item of Array.from(files)) {
        const body = new FormData();
        body.append("file", item);
        await api(`/projects/${project.id}/artifacts`, {
          method: "POST",
          body,
        });
      }
      onRefresh();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setUploading(false);
      if (file.current) file.current.value = "";
    }
  }
  const stage =
    activity?.type === "MODEL_RETRYING"
      ? "Correcting response format"
      : activity?.type === "TOOL_STARTED"
        ? toolLabel(activity)
        : activity?.type === "MODEL_STREAM"
          ? activity.payload.phase === "responding"
            ? "Forming the next step"
            : activity.payload.phase === "finalizing"
              ? "Applying the result"
              : "Thinking"
        : activity?.type === "STALE_TURN_DISCARDED"
          ? "Taking your latest message into account"
          : rootJobs.some((job) => job.state === "running")
            ? "Thinking"
            : "Queued";
  const hint =
    activity?.type === "TOOL_STARTED"
      ? String(
          activity.payload.query ||
            activity.payload.url ||
            activity.payload.summary ||
            "",
        )
      : busy && elapsed > 20
        ? "Still working. You can steer the conversation or stop this response."
        : "";
  const progressMessages = data.messages.filter(
    (message) =>
      message.channel === "progress" &&
      (!lastInput || message.created_at >= lastInput.created_at),
  );
  const runningSummary = progressMessages.at(-1)?.text ||
    (activity?.type === "TOOL_STARTED" && hint
      ? `${toolLabel(activity)}: ${hint}`
      : stage === "Thinking"
        ? "Working through the question and deciding what evidence or action is useful next."
        : `${stage}.`);
  return (
    <section className="conversation" aria-label="Conversation with Sapling">
      <div
        className="conversation-scroll"
        ref={scroll}
        onScroll={() => {
          const el = scroll.current;
          if (el)
            setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 100);
        }}
      >
        <div className="conversation-inner">
          {!data.messages.length && (
            <div className="conversation-empty">
              <Mark size={33} />
              <h1>What’s on your mind?</h1>
              <p>
                A question, a hunch, a paper you can’t stop thinking about. We
                can work it out together.
              </p>
            </div>
          )}
          {timeline.map((entry) =>
            entry.kind === "activity" ? (
              <ActivitySummary items={entry.items} key={entry.id} />
            ) : (
              <article
                key={entry.id}
                className={`chat-message ${entry.message.role}`}
              >
                <header>
                  {entry.message.role === "assistant" && <Sparkles size={15} />}
                  <strong>
                    {entry.message.role === "assistant"
                      ? "Sapling"
                      : entry.message.role === "user"
                        ? "You"
                        : label(entry.message.role)}
                  </strong>
                  <time>{date(entry.message.created_at)}</time>
                </header>
                {!!entry.message.node_ids?.length && <div className="message-node-refs">{entry.message.node_ids.map(id => <span key={id} title={id}><GitBranch size={11} />{id.slice(0, 8)}</span>)}</div>}
                <Markdown>{entry.message.text}</Markdown>
              </article>
            ),
          )}
          {data.attention
            .filter(
              (item) => item.status === "pending" && item.type === "permission",
            )
            .map((item) => (
              <Approval
                key={item.id}
                item={item}
                onRefresh={onRefresh}
                onError={onError}
              />
            ))}
          {question && data.messages.at(-1)?.role !== "assistant" && (
            <div className="conversation-prompt">
              <Markdown>{String(question.summary || "")}</Markdown>
              <span>Reply here in the conversation.</span>
            </div>
          )}
        </div>
      </div>
      <div className="composer-dock">
        {!atBottom && (
          <button
            className="jump-to-latest"
            onClick={() => {
              setAtBottom(true);
              scroll.current?.scrollTo({
                top: scroll.current.scrollHeight,
                behavior: "smooth",
              });
            }}
          >
            <ArrowDown size={13} /> Latest messages
          </button>
        )}
        {busy ? (
          <details className="live-thinking" role="status" aria-live="polite">
            <summary>
              <Loader2 size={14} className="spinning" />
              <strong>
                {sending ? "Sending your message" : stage}
                {busy && !sending && <span className="activity-dots">…</span>}
              </strong>
              <span
                className="token-activity"
                title="Tokens sent to and received from the model in this response"
                aria-label={`${usage.input} input tokens and ${usage.output} output tokens`}
              >
                <span>↑ {usage.estimated ? "~" : ""}{tokenCount(usage.input)}</span>
                <span>↓ {usage.estimated ? "~" : ""}{tokenCount(usage.output)}</span>
              </span>
              <time>{elapsed}s</time>
              <ChevronDown size={13} className="thinking-chevron" />
            </summary>
            <div className="thinking-summary">
              <p>{compactNote(runningSummary)}</p>
              {activity?.type === "TOOL_STARTED" && hint && (
                <div className="latest-action">
                  <span>{toolLabel(activity)}</span>
                  <small>{hint}</small>
                </div>
              )}
              <small>
                {tools.length ? `${tools.length} research action${tools.length === 1 ? "" : "s"}` : "Preparing the next step"}
                {usage.output > 0 ? ` · ${tokenCount(usage.output)} tokens generated` : ""}
              </small>
            </div>
          </details>
        ) : problem ? (
          <div className="chat-activity error" role="alert">
            <AlertCircle size={15} />
            <div>
              <strong>
                {problem.type === "configuration"
                  ? "Check your model settings"
                  : problem.type === "output_limit"
                    ? "Response reached its output limit"
                    : "This response stopped unexpectedly"}
              </strong>
              <small>{String(problem.summary || "")}</small>
            </div>
            {problem.type === "output_limit" && (
              <button onClick={onSettings}>Settings</button>
            )}
            <button
              onClick={
                problem.type === "configuration"
                  ? onSettings
                  : () => void retry()
              }
            >
              {problem.type === "configuration" ? "Settings" : "Retry"}
            </button>
          </div>
        ) : root?.chat_stopped ? (
          <div className="chat-activity">
            <Square size={11} />
            <div>Response stopped. Send a message to continue.</div>
          </div>
        ) : project.research_state === "paused" ? (
          <div className="chat-activity">
            <div>Autoresearch paused. We can keep thinking together.</div>
          </div>
        ) : (
          <div className="chat-activity">
            {!connected && <span>Reconnecting live updates…</span>}
          </div>
        )}
        <form className="chat-composer" onSubmit={send}>
          {reference && <div className="composer-reference"><GitBranch size={13} /><span title={reference.nodeId}>{reference.title}<small>{reference.nodeId.slice(0, 8)}</small></span><button type="button" className="icon-button" aria-label="Remove node reference" onClick={onClearReference}><X size={13} /></button></div>}
          <textarea
            ref={input}
            aria-label="Message Sapling"
            placeholder={
              busy
                ? "Add a thought or steer the conversation…"
                : "Think through a question together…"
            }
            rows={2}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              resizeComposer(event.target);
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                void send();
              }
              if (event.key === "Escape" && busy) {
                event.preventDefault();
                void stop();
              }
            }}
          />
          <div className="composer-tools">
            <input
              ref={file}
              type="file"
              hidden
              multiple
              accept=".pdf,.txt,.md,.csv,.json,.html"
              onChange={(event) => void upload(event.target.files)}
            />
            <button
              className="icon-button"
              type="button"
              aria-label="Attach context"
              disabled={uploading}
              onClick={() => file.current?.click()}
            >
              {uploading ? (
                <Loader2 size={15} className="spinning" />
              ) : (
                <Paperclip size={16} />
              )}
            </button>
            <button
              className="model-trigger"
              type="button"
              onClick={onSettings}
              aria-label="Change model and reasoning"
            >
              {project.settings.model}
              <ChevronDown size={11} />
            </button>
            <span>
              {project.settings.reasoning_effort
                ? label(project.settings.reasoning_effort)
                : "Default"}
            </span>
            {busy && !draft.trim() ? (
              <button
                className="send-button stop"
                type="button"
                aria-label="Stop response"
                disabled={stopping}
                onClick={() => void stop()}
              >
                <Square size={13} fill="currentColor" />
              </button>
            ) : (
              <button
                className="send-button"
                aria-label={busy ? "Steer conversation" : "Send message"}
                type="submit"
                disabled={!draft.trim() || sending}
              >
                {sending ? (
                  <Loader2 size={15} className="spinning" />
                ) : (
                  <ArrowUp size={17} />
                )}
              </button>
            )}
          </div>
        </form>
        <div className="composer-note">
          <span>
            {busy
              ? "New messages steer the next step. Esc to stop."
              : "Shift + Enter for a new line"}
          </span>
          <span>
            Local workspace ·{" "}
            {project.settings.permission_mode === "balanced"
              ? "Ask for risky actions"
              : project.settings.permission_mode === "ask"
                ? "Ask by category"
                : "Full autonomy"}
          </span>
        </div>
      </div>
    </section>
  );
}

type ActivityItem =
  | { kind: "note"; id: string; text: string; created_at: string }
  | { kind: "tool"; id: string; event: ResearchEvent; created_at: string };
type TimelineEntry =
  | { kind: "message"; id: string; message: Message }
  | { kind: "activity"; id: string; items: ActivityItem[] };

function conversationTimeline(
  data: ProjectData,
  rootId?: string,
): TimelineEntry[] {
  const entries: (
    | ActivityItem
    | { kind: "message"; id: string; message: Message; created_at: string }
  )[] = [
    ...data.messages.map((message) =>
      message.channel === "progress"
        ? {
            kind: "note" as const,
            id: message.id,
            text: message.text,
            created_at: message.created_at,
          }
        : {
            kind: "message" as const,
            id: message.id,
            message,
            created_at: message.created_at,
          },
    ),
    ...data.events
      .filter(
        (event) =>
          event.type === "TOOL_STARTED" && event.payload.holon_id === rootId,
      )
      .map((event) => ({
        kind: "tool" as const,
        id: `tool-${event.id}`,
        event,
        created_at: event.created_at,
      })),
  ];
  // ISO timestamps retain microseconds; Date would round same-millisecond steps.
  entries.sort((a, b) => a.created_at.localeCompare(b.created_at));
  const result: TimelineEntry[] = [];
  for (const entry of entries) {
    if (entry.kind === "message") result.push(entry);
    else {
      const previous = result.at(-1);
      if (previous?.kind === "activity") previous.items.push(entry);
      else
        result.push({
          kind: "activity",
          id: `activity-${entry.id}`,
          items: [entry],
        });
    }
  }
  return result;
}

function compactNote(text: string) {
  const plain = text.replace(/\s+/g, " ").trim();
  const firstSentence = plain.match(/^.*?[.!?](?=\s|$)/)?.[0] || plain;
  return firstSentence.length > 180
    ? `${firstSentence.slice(0, 177).trimEnd()}…`
    : firstSentence;
}

function toolLabel(event: ResearchEvent) {
  return (
    (
      {
        search_literature: "Searching papers",
        search_web: "Searching the web",
        open_source: "Reading a source",
        run_experiment: "Running an experiment",
        read_artifact: "Reading an artifact",
        retrieve_evidence: "Reviewing evidence",
      } as Record<string, string>
    )[String(event.payload.kind)] || "Working"
  );
}

function ActivitySummary({ items }: { items: ActivityItem[] }) {
  const notes = items.filter(
    (item): item is Extract<ActivityItem, { kind: "note" }> => item.kind === "note",
  );
  const actions = items.filter(
    (item): item is Extract<ActivityItem, { kind: "tool" }> => item.kind === "tool",
  );
  const latestNote = notes.at(-1);
  const latestAction = actions.at(-1);
  const headline = latestNote
    ? compactNote(latestNote.text)
    : latestAction
      ? toolLabel(latestAction.event)
      : "Thought through the next step";
  const actionDetail = latestAction
    ? String(
        latestAction.event.payload.query ||
          latestAction.event.payload.url ||
          latestAction.event.payload.summary ||
          "",
      )
    : "";

  return (
    <details className="turn-activity">
      <summary>
        <span>Thought process</span>
        <strong>{headline}</strong>
        <ChevronDown size={12} />
      </summary>
      <div className="thinking-summary">
        {latestNote && <p>{compactNote(latestNote.text)}</p>}
        {latestAction && (
          <div className="latest-action">
            <span>{toolLabel(latestAction.event)}</span>
            {actionDetail && <small>{actionDetail}</small>}
          </div>
        )}
        <small>
          {notes.length ? `${notes.length} reasoning update${notes.length === 1 ? "" : "s"}` : ""}
          {notes.length && actions.length ? " · " : ""}
          {actions.length ? `${actions.length} research action${actions.length === 1 ? "" : "s"}` : ""}
        </small>
      </div>
    </details>
  );
}

function Approval({
  item,
  onRefresh,
  onError,
}: {
  item: RecordItem;
  onRefresh: () => void;
  onError: (error: string) => void;
}) {
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  async function respond(approve: boolean) {
    setBusy(true);
    try {
      await api(`/attention/${item.id}/respond`, {
        method: "POST",
        body: JSON.stringify({ approve, remember }),
      });
      onRefresh();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="approval-card">
      <div className="inline-status">
        <ShieldCheck size={15} />
        Permission requested
      </div>
      <p>{field(item, "summary", "description")}</p>
      <details>
        <summary>Review action</summary>
        <pre>{JSON.stringify(item.work_order, null, 2)}</pre>
      </details>
      <div className="approval-actions">
        <label>
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
          />
          Remember for this project
        </label>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => void respond(false)}
        >
          Deny
        </button>
        <button
          className="button primary"
          disabled={busy}
          onClick={() => void respond(true)}
        >
          Allow
        </button>
      </div>
    </div>
  );
}
