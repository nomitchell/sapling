"use client";

import type { ProjectData } from "@/lib/api";
import {
api,
date,
errorText,
field,
label,
money,
Project,
RecordItem
} from "@/lib/api";
import {
ArrowDownToLine,
ArrowUp,
BookOpen,
ChevronDown,
ChevronRight,
CirclePause,
ClipboardList,
ExternalLink,
FlaskConical,
GitBranch,
Loader2,
Network,
Play,
Search,
Sprout,
Trash2,
X
} from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Empty,Markdown,Status } from "./ui";

function ViewHeading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="view-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {children}
    </div>
  );
}
function DetailPanel({
  record,
  onClose,
  actions,
}: {
  record: RecordItem;
  onClose: () => void;
  actions?: ReactNode;
}) {
  return (
    <aside className="record-detail">
      <div className="detail-heading">
        <span className="eyebrow">Research record</span>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close details"
        >
          <X size={17} />
        </button>
      </div>
      <h3>
        {field(record, "title", "name", "question", "text", "claim") ||
          "Details"}
      </h3>
      <Status value={record.status} />
      {actions && <div className="detail-actions">{actions}</div>}
      <dl>
        {Object.entries(record)
          .filter(
            ([key, value]) =>
              !["id", "title", "status"].includes(key) &&
              value !== null &&
              value !== "" &&
              value !== undefined,
          )
          .map(([key, value]) => (
            <div key={key}>
              <dt>{label(key)}</dt>
              <dd>
                {typeof value === "object" ? (
                  <pre>{JSON.stringify(value, null, 2)}</pre>
                ) : /code|stdout|stderr|traceback/.test(key) ? (
                  <pre>{String(value)}</pre>
                ) : (
                  <Markdown>{String(value)}</Markdown>
                )}
              </dd>
            </div>
          ))}
      </dl>
    </aside>
  );
}

export function ResearchTree({
  records,
  onChange,
  onError,
  onNotice,
}: {
  records: RecordItem[];
  onChange: () => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [focused, setFocused] = useState<string | null>(null);
  const selected = records.find((item) => item.id === focused);
  const ids = new Set(records.map((item) => item.id));
  const roots = records.filter(
    (item) => !item.parent_id || !ids.has(String(item.parent_id)),
  );
  const visited = new Set<string>();
  function renderNode(node: RecordItem, depth = 0): ReactNode {
    if (visited.has(node.id) || depth > 50) return null;
    visited.add(node.id);
    const children = records.filter((item) => item.parent_id === node.id);
    return (
      <div className="tree-branch" key={node.id}>
        <button
          className={`tree-node ${focused === node.id ? "focused" : ""}`}
          onClick={() => setFocused(node.id)}
        >
          <span className="node-symbol">
            {depth === 0 ? <Sprout size={20} /> : <GitBranch size={17} />}
          </span>
          <span className="node-text">
            <span className="eyebrow">
              {depth === 0 ? "Root" : `Branch ${depth}`}{" "}
              {node.type ? ` / ${label(node.type)}` : ""}
            </span>
            <strong>
              {field(node, "title", "question", "name", "goal") ||
                "Research branch"}
            </strong>
            {field(node, "summary", "description") && (
              <small>{field(node, "summary", "description")}</small>
            )}
          </span>
          <Status value={node.status} />
          <ChevronRight size={16} />
        </button>
        {children.length > 0 && (
          <div className="tree-children">
            {children.map((child) => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    );
  }
  return (
    <section className="data-view">
      <ViewHeading
        eyebrow="Branching inquiry"
        title="Room to explore."
        description="Follow each line of inquiry, from the original question to the findings it produces."
      >
        <span className="count-label">{records.length} branches</span>
      </ViewHeading>
      {!records.length ? (
        <Empty icon={GitBranch} title="Every tree starts with a question.">
          Research branches will appear as your researchers explore the project.
        </Empty>
      ) : (
        <div className={`split-view ${selected ? "has-detail" : ""}`}>
          <div className="tree-canvas">
            {roots.map((node) => renderNode(node))}
            {records
              .filter((node) => !visited.has(node.id))
              .map((node) => renderNode(node))}
          </div>
          {selected && (
            <DetailPanel
              record={selected}
              onClose={() => setFocused(null)}
              actions={
                <NodePriority
                  key={selected.id}
                  nodeId={selected.id}
                  onChange={onChange}
                  onError={onError}
                  onNotice={onNotice}
                />
              }
            />
          )}
        </div>
      )}
    </section>
  );
}

function NodePriority({
  nodeId,
  onChange,
  onError,
  onNotice,
}: {
  nodeId: string;
  onChange: () => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function prioritize() {
    setBusy(true);
    try {
      await api("/nodes/" + nodeId + "/prioritize", {
        method: "POST",
        body: JSON.stringify({}),
      });
      onNotice("Priority sent to the root researcher.");
      onChange();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <button className="button secondary" disabled={busy} onClick={prioritize}>
      {busy ? (
        <Loader2 size={14} className="spinning" />
      ) : (
        <ArrowUp size={14} />
      )}
      {busy ? "Sending priority..." : "Prioritize this direction"}
    </button>
  );
}

export function Holarchy({
  records,
  onChange,
  onError,
}: {
  records: RecordItem[];
  onChange: () => void;
  onError: (message: string) => void;
}) {
  const [focused, setFocused] = useState<string | null>(null);
  const selected = records.find((item) => item.id === focused);
  return (
    <section className="data-view">
      <ViewHeading
        eyebrow="The research collective"
        title="Independent minds. Shared direction."
        description="Each researcher owns a piece of the question and contributes to the whole."
      >
        <span className="count-label">{records.length} researchers</span>
      </ViewHeading>
      {!records.length ? (
        <Empty icon={Network} title="Your research collective is taking shape.">
          Researchers appear here when the project begins. Inspect their roles,
          progress, and relationships.
        </Empty>
      ) : (
        <div className={`split-view ${selected ? "has-detail" : ""}`}>
          <div className="researcher-list">
            {records.map((item, index) => (
              <button
                key={item.id}
                className={`researcher-row ${focused === item.id ? "focused" : ""}`}
                onClick={() => setFocused(item.id)}
              >
                <span className="researcher-index">
                  H{(index + 1).toString().padStart(2, "0")}
                </span>
                <div>
                  <span className="eyebrow">
                    {field(item, "role", "kind") || "Researcher"}
                  </span>
                  <h3>
                    {field(item, "title", "name", "goal") || "Researcher"}
                  </h3>
                  <p>{field(item, "summary", "objective", "description")}</p>
                  <small>
                    {item.parent_id
                      ? `Reports to ${records.find((parent) => parent.id === item.parent_id)?.title || "parent researcher"}`
                      : "Root researcher"}
                  </small>
                </div>
                <Status value={item.status} />
                <ChevronRight size={16} />
              </button>
            ))}
          </div>
          {selected && (
            <DetailPanel
              record={selected}
              onClose={() => setFocused(null)}
              actions={
                <HolonControl
                  record={selected}
                  onChange={onChange}
                  onError={onError}
                />
              }
            />
          )}
        </div>
      )}
    </section>
  );
}

function HolonControl({
  record,
  onChange,
  onError,
}: {
  record: RecordItem;
  onChange: () => void;
  onError: (error: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const paused = record.status === "paused";
  async function control() {
    setBusy(true);
    try {
      await api("/holons/" + record.id + "/" + (paused ? "resume" : "pause"), {
        method: "POST",
      });
      onChange();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      className="button secondary"
      disabled={busy || record.status === "completed"}
      onClick={control}
    >
      {busy ? (
        <Loader2 size={14} className="spinning" />
      ) : paused ? (
        <Play size={14} />
      ) : (
        <CirclePause size={14} />
      )}
      {paused
        ? "Resume researcher & descendants"
        : "Pause researcher & descendants"}
    </button>
  );
}

export function Commons({
  claims,
  evidence,
  artifacts,
}: {
  claims: RecordItem[];
  evidence: RecordItem[];
  artifacts: RecordItem[];
}) {
  const [section, setSection] = useState("claims");
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState<string | null>(null);
  const records =
    section === "claims"
      ? claims
      : section === "evidence"
        ? evidence
        : artifacts;
  const filtered = records.filter((item) =>
    JSON.stringify(item).toLowerCase().includes(query.toLowerCase()),
  );
  const selected = records.find((item) => item.id === focused);
  return (
    <section className="data-view">
      <ViewHeading
        eyebrow="Shared knowledge"
        title="A growing body of understanding."
        description="Claims, sources, and files — collected in one place, grounded in the research."
      />
      <div className="data-toolbar">
        <div className="segmented">
          {[
            { id: "claims", name: "Claims", count: claims.length },
            { id: "evidence", name: "Evidence", count: evidence.length },
            { id: "files", name: "Files", count: artifacts.length },
          ].map((item) => (
            <button
              className={section === item.id ? "active" : ""}
              onClick={() => {
                setSection(item.id);
                setFocused(null);
              }}
              key={item.id}
            >
              {item.name}
              <span>{item.count}</span>
            </button>
          ))}
        </div>
        <div className="search-field">
          <Search size={15} />
          <input
            aria-label="Search knowledge"
            placeholder="Search the commons"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {!filtered.length ? (
        <Empty
          icon={BookOpen}
          title={
            query
              ? "No matching records."
              : section === "claims"
                ? "Understanding takes shape here."
                : section === "evidence"
                  ? "A place for the evidence."
                  : "Bring your own context."
          }
        >
          {query
            ? "Try another search term."
            : section === "claims"
              ? "As research progresses, claims and their supporting evidence become a shared foundation."
              : section === "evidence"
                ? "Papers, web sources, and experimental observations will appear here."
                : "Attach documents from the workspace to make them available to your researchers."}
        </Empty>
      ) : (
        <div className={`split-view ${selected ? "has-detail" : ""}`}>
          <div className="knowledge-list">
            {filtered.map((item, index) => (
              <article className="knowledge-row" key={item.id}>
                <span className="row-number">
                  {(index + 1).toString().padStart(2, "0")}
                </span>
                <div>
                  <button
                    className="text-title"
                    onClick={() => setFocused(item.id)}
                  >
                    {field(
                      item,
                      "title",
                      "text",
                      "claim",
                      "statement",
                      "filename",
                      "name",
                    ) ||
                      `Research ${section === "claims" ? "claim" : "source"}`}
                  </button>
                  {field(
                    item,
                    "summary",
                    "description",
                    "content",
                    "abstract",
                  ) && (
                    <p>
                      {field(
                        item,
                        "summary",
                        "description",
                        "content",
                        "abstract",
                      )}
                    </p>
                  )}
                  <div className="record-meta">
                    <span>
                      {label(
                        item.kind ||
                          item.type ||
                          item.source ||
                          section.slice(0, -1),
                      )}
                    </span>
                    {item.confidence !== undefined && (
                      <span>
                        {Math.round(Number(item.confidence) * 100)}% confidence
                      </span>
                    )}
                    {item.status && <Status value={item.status} />}
                  </div>
                </div>
                {section === "files" ? (
                  <a
                    className="icon-button outlined"
                    href={`/api/artifacts/${item.id}/download`}
                    title="Download file"
                    aria-label="Download file"
                  >
                    <ArrowDownToLine size={16} />
                  </a>
                ) : /^https?:\/\//.test(field(item, "url", "source_url")) ? (
                  <a
                    className="icon-button"
                    href={field(item, "url", "source_url")}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open source"
                    aria-label="Open source"
                  >
                    <ExternalLink size={16} />
                  </a>
                ) : (
                  <button
                    className="icon-button"
                    onClick={() => setFocused(item.id)}
                    aria-label="View record"
                  >
                    <ChevronRight size={16} />
                  </button>
                )}
              </article>
            ))}
          </div>
          {selected && (
            <DetailPanel record={selected} onClose={() => setFocused(null)} />
          )}
        </div>
      )}
    </section>
  );
}

export function Experiments({ records }: { records: RecordItem[] }) {
  const [focused, setFocused] = useState<string | null>(null);
  const selected = records.find((item) => item.id === focused);
  return (
    <section className="data-view">
      <ViewHeading
        eyebrow="Ideas meet evidence"
        title="The workbench."
        description="Inspect executed code, results, and observations from your researchers."
      >
        <span className="count-label">{records.length} experiments</span>
      </ViewHeading>
      {!records.length ? (
        <Empty icon={FlaskConical} title="Ready when an idea needs testing.">
          Experiments will appear here with their code, execution logs, and
          results.
        </Empty>
      ) : (
        <div className={`split-view ${selected ? "has-detail" : ""}`}>
          <div className="experiment-list">
            <div className="table-header">
              <span>Experiment</span>
              <span>Status</span>
              <span>Created</span>
              <span />
            </div>
            {records.map((item, index) => (
              <button
                key={item.id}
                className="experiment-row"
                onClick={() => setFocused(item.id)}
              >
                <span>
                  <span className="row-number">
                    E{(index + 1).toString().padStart(2, "0")}
                  </span>
                  <strong>
                    {field(item, "title", "name", "hypothesis") || "Experiment"}
                  </strong>
                  <small>{field(item, "summary", "description")}</small>
                </span>
                <Status value={item.status} />
                <time>{date(item.created_at)}</time>
                <ChevronRight size={15} />
              </button>
            ))}
          </div>
          {selected && (
            <DetailPanel record={selected} onClose={() => setFocused(null)} />
          )}
        </div>
      )}
    </section>
  );
}

export function Operations({
  project,
  data,
  onDelete,
}: {
  project: Project;
  data: ProjectData;
  onDelete: () => void;
}) {
  const [query, setQuery] = useState("");
  const events = data.events.filter((event) =>
    `${event.type} ${JSON.stringify(event.payload)}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section className="data-view">
      <ViewHeading
        eyebrow="Behind the research"
        title="An open notebook of activity."
        description="Follow the event log, inspect resource use, and export your research data."
      />
      <div className="ops-summary">
        <div>
          <span>Model</span>
          <strong>{project.settings?.model || "Not configured"}</strong>
        </div>
        <div>
          <span>Execution</span>
          <strong>
            {label(project.settings?.execution_backend || "docker")}
          </strong>
        </div>
        <div>
          <span>Permissions</span>
          <strong>
            {label(project.settings?.permission_mode || "balanced")}
          </strong>
        </div>
        <div>
          <span>Estimated spending</span>
          <strong>
            {money(project.budget_spent)}{" "}
            <small>/ {money(project.budget_total)}</small>
          </strong>
        </div>
      </div>
      <div className="data-toolbar">
        <h3 className="toolbar-title">
          Event log <span>{data.events.length}</span>
        </h3>
        <div className="search-field">
          <Search size={15} />
          <input
            aria-label="Filter events"
            placeholder="Filter activity"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {!events.length ? (
        <Empty
          icon={ClipboardList}
          title={query ? "No matching events." : "The notebook is open."}
        >
          {query
            ? "Try another filter."
            : "Actions, decisions, and runtime events will be recorded here as research progresses."}
        </Empty>
      ) : (
        <div className="event-log">
          {[...events].reverse().map((event) => (
            <details key={event.id}>
              <summary>
                <time>{date(event.created_at)}</time>
                <span className="event-dot" />
                <strong>{label(event.type)}</strong>
                <ChevronDown size={14} />
              </summary>
              <pre>{JSON.stringify(event.payload, null, 2)}</pre>
            </details>
          ))}
        </div>
      )}
      <div className="ops-footer">
        <div>
          <h3>Research data</h3>
          <p>
            Export the decisions and observations collected for future training.
          </p>
          <a
            className="button secondary"
            href={`/api/projects/${project.id}/training-data/export`}
            download
          >
            <ArrowDownToLine size={15} />
            Export training data
          </a>
        </div>
        <button className="button danger-quiet" onClick={onDelete}>
          <Trash2 size={15} />
          Archive project
        </button>
      </div>
    </section>
  );
}
