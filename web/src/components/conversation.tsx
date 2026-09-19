"use client";

import {
  api,
  date,
  errorText,
  field,
  label,
  Project,
  ProjectData,
  RecordItem,
  ResearchEvent,
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
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Mark, Markdown } from "./ui";

export function Conversation({
  project,
  data,
  connected,
  onRefresh,
  onSettings,
  onError,
}: {
  project: Project;
  data: ProjectData;
  connected: boolean;
  onRefresh: () => void;
  onSettings: () => void;
  onError: (error: string) => void;
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
  const rootJobs = jobs.filter(
    (job) =>
      job.holon_id === project.root_holon_id &&
      ["running", "queued"].includes(String(job.state)),
  );
  const busy =
    sending ||
    (!root?.chat_stopped &&
      project.status === "active" &&
      root?.status === "active" &&
      (rootJobs.length > 0 || sending));
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
        "MODEL_RETRYING",
        "TOOL_STARTED",
        "STALE_TURN_DISCARDED",
        "MODEL_TURN",
      ].includes(event.type),
    );
  const tools = turnEvents.filter((event) => event.type === "TOOL_STARTED");
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
  async function send(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || sending) return;
    const message = draft.trim();
    setSending(true);
    setAtBottom(true);
    try {
      await api(`/projects/${project.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ text: message }),
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
          {data.messages.map((message) => (
            <article
              key={message.id}
              className={`chat-message ${message.role}`}
            >
              <header>
                {message.role === "assistant" && <Sparkles size={15} />}
                <strong>
                  {message.role === "assistant"
                    ? "Sapling"
                    : message.role === "user"
                      ? "You"
                      : label(message.role)}
                </strong>
                <time>{date(message.created_at)}</time>
              </header>
              <Markdown>{message.text}</Markdown>
            </article>
          ))}
          {tools.length > 0 && (
            <details className="turn-activity">
              <summary>
                {tools.length} research{" "}
                {tools.length === 1 ? "action" : "actions"}
              </summary>
              {tools.map((event) => (
                <div key={event.id}>
                  <span>{toolLabel(event)}</span>
                  <small>
                    {String(
                      event.payload.query ||
                        event.payload.url ||
                        event.payload.summary ||
                        "",
                    )}
                  </small>
                </div>
              ))}
            </details>
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
          <div className="chat-activity" role="status" aria-live="polite">
            <Loader2 size={14} className="spinning" />
            <div>
              <strong>
                {sending ? "Sending your message" : stage}
                {busy && !sending && <span className="activity-dots">…</span>}
              </strong>
              {hint && <small title={hint}>{hint}</small>}
            </div>
            <time>{elapsed}s</time>
          </div>
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
        ) : project.status === "paused" && data.messages.length > 0 ? (
          <div className="chat-activity">
            <div>Project paused. Your messages will be saved.</div>
            <button
              onClick={() =>
                void api(`/projects/${project.id}/resume`, { method: "POST" })
                  .then(onRefresh)
                  .catch((err) => onError(errorText(err)))
              }
            >
              Resume
            </button>
          </div>
        ) : (
          <div className="chat-activity">
            {!connected && <span>Reconnecting live updates…</span>}
          </div>
        )}
        <form className="chat-composer" onSubmit={send}>
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
              event.target.style.height = "auto";
              event.target.style.height =
                Math.min(180, event.target.scrollHeight) + "px";
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
