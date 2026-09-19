"use client";

import { api, date, errorText, field, label, money, type ConversationReference, type Project, type ProjectData, type RecordItem } from "@/lib/api";
import { Check, ChevronDown, ChevronRight, Copy, Expand, ExternalLink, GitBranch, MessageSquare, Minus, Plus, Sprout, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Markdown } from "./ui";

type PositionedNode = { record: RecordItem; x: number; y: number; parent?: string; children: number };
const WIDTH = 252, HEIGHT = 126, GAP_X = 86, GAP_Y = 36;

function layout(records: RecordItem[], collapsed: Set<string>) {
  const ids = new Set(records.map(node => node.id));
  const children = new Map<string, RecordItem[]>();
  records.forEach(node => {
    if (node.parent_id && ids.has(String(node.parent_id))) {
      const parent = String(node.parent_id);
      children.set(parent, [...(children.get(parent) || []), node]);
    }
  });
  const nodes: PositionedNode[] = [], seen = new Set<string>();
  let row = 0;
  function visit(record: RecordItem, depth: number, parent?: string): number {
    if (seen.has(record.id)) return row * (HEIGHT + GAP_Y);
    seen.add(record.id);
    const descendants = (children.get(record.id) || []).filter(child => !seen.has(child.id));
    const childYs = collapsed.has(record.id) ? [] : descendants.map(child => visit(child, depth + 1, record.id));
    const y = childYs.length ? (childYs[0] + childYs[childYs.length - 1]) / 2 : row++ * (HEIGHT + GAP_Y);
    nodes.push({ record, x: depth * (WIDTH + GAP_X), y, parent, children: descendants.length });
    return y;
  }
  records.filter(node => !node.parent_id || !ids.has(String(node.parent_id))).forEach(node => visit(node, 0));
  records.filter(node => !seen.has(node.id)).forEach(node => {
    // Descendants hidden by a collapsed ancestor stay hidden; disconnected roots remain inspectable.
    let ancestor = records.find(candidate => candidate.id === node.parent_id);
    const chain = new Set<string>();
    while (ancestor && !chain.has(ancestor.id)) {
      if (collapsed.has(ancestor.id)) return;
      chain.add(ancestor.id);
      ancestor = records.find(candidate => candidate.id === ancestor?.parent_id);
    }
    if (!seen.has(node.id)) visit(node, 0);
  });
  return { nodes, children, width: Math.max(WIDTH, ...nodes.map(node => node.x + WIDTH)), height: Math.max(HEIGHT, row * (HEIGHT + GAP_Y) - GAP_Y) };
}

export function ResearchCanvas({ project, data, onDiscuss, onRefresh, onError, onKnowledge }: {
  project: Project; data: ProjectData; onDiscuss: (reference?: ConversationReference) => void;
  onRefresh: () => void; onError: (message: string) => void; onKnowledge: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [view, setView] = useState({ x: 64, y: 120, zoom: 1 });
  const [copied, setCopied] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const dragged = useRef<{ pointer: number; x: number; y: number; originX: number; originY: number } | null>(null);
  const acknowledged = useRef(new Set<string>());
  const result = useMemo(() => layout(data.tree, collapsed), [data.tree, collapsed]);
  const selected = data.tree.find(node => node.id === selectedId);
  const attentionByNode = new Map<string, RecordItem[]>();
  data.attention.filter(item => item.status === "pending").forEach(item => {
    const nodeId = String(item.node_id || data.holarchy.find(holon => holon.id === item.holon_id)?.assigned_node_id || "");
    attentionByNode.set(nodeId, [...(attentionByNode.get(nodeId) || []), item]);
  });
  const selectedAttention = selected ? attentionByNode.get(selected.id) || [] : [];
  const readIds = selectedAttention.filter(item => !item.read_at).map(item => item.id).join(",");
  useEffect(() => {
    const ids = readIds.split(",").filter(id => id && !acknowledged.current.has(id));
    if (!ids.length) return;
    ids.forEach(id => acknowledged.current.add(id));
    void Promise.all(ids.map(id => api(`/attention/${id}/read`, { method: "POST" }))).then(onRefresh).catch(error => {
      ids.forEach(id => acknowledged.current.delete(id));
      onError(errorText(error));
    });
  }, [readIds, onRefresh, onError]);
  const jobs = (Array.isArray(data.stats.jobs) ? data.stats.jobs as RecordItem[] : []);
  const runningHolons = new Set(jobs.filter(job => job.state === "running").map(job => String(job.holon_id || "")));
  const queuedHolons = new Set(jobs.filter(job => job.state === "queued").map(job => String(job.holon_id || "")));
  const runningNodes = new Set(data.holarchy.filter(holon => runningHolons.has(holon.id)).map(holon => String(holon.assigned_node_id || "")));
  const queuedNodes = new Set(data.holarchy.filter(holon => queuedHolons.has(holon.id)).map(holon => String(holon.assigned_node_id || "")));
  function attentionCounts(id: string, recursive: boolean, seen = new Set<string>()): { unread: number; blocked: number; running: number } {
    if (seen.has(id)) return { unread: 0, blocked: 0, running: 0 };
    seen.add(id);
    const items = attentionByNode.get(id) || [];
    const count = {
      unread: items.filter(item => !item.pauses_subtree && item.type !== "permission" && !item.read_at).length,
      blocked: items.filter(item => item.pauses_subtree || item.type === "permission").length,
      running: runningNodes.has(id) ? 1 : 0,
    };
    if (recursive) (result.children.get(id) || []).forEach(child => {
      const next = attentionCounts(child.id, true, seen);
      count.unread += next.unread; count.blocked += next.blocked; count.running += next.running;
    });
    return count;
  }
  function fit() {
    const rect = viewport.current?.getBoundingClientRect();
    if (!rect) return;
    const zoom = Math.min(1, Math.max(.18, Math.min((rect.width - 100) / result.width, (rect.height - 180) / result.height)));
    setView({ x: (rect.width - result.width * zoom) / 2, y: Math.max(95, (rect.height - result.height * zoom) / 2), zoom });
  }
  function zoomBy(factor: number) {
    setView(current => {
      const rect = viewport.current?.getBoundingClientRect();
      const zoom = Math.max(.18, Math.min(1.8, current.zoom * factor));
      const cx = (rect?.width || 600) / 2, cy = (rect?.height || 500) / 2;
      return { x: cx - (cx - current.x) * zoom / current.zoom, y: cy - (cy - current.y) * zoom / current.zoom, zoom };
    });
  }
  function startPan(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button,a,aside")) return;
    dragged.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, originX: view.x, originY: view.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  const activeJobs = runningHolons.size;
  return <div className="research-stage">
    <div className="canvas-heading">
      <div><span className="eyebrow">Research tree</span><h1>{project.goal ? "Following the question." : "Room for a good question."}</h1></div>
      <button className="canvas-knowledge" onClick={onKnowledge}>Shared knowledge <span>{data.claims.length + data.evidence.length + data.artifacts.length}</span><ChevronRight size={13} /></button>
    </div>
    <div className="research-viewport" ref={viewport} tabIndex={0} aria-label="Research tree canvas. Drag to pan. Use plus and minus to zoom."
      onPointerDown={startPan}
      onPointerMove={event => { const drag = dragged.current; if (drag) setView(current => ({ ...current, x: drag.originX + event.clientX - drag.x, y: drag.originY + event.clientY - drag.y })); }}
      onPointerUp={() => { dragged.current = null; }} onPointerCancel={() => { dragged.current = null; }}
      onWheel={event => {
        if (event.ctrlKey || event.metaKey) zoomBy(event.deltaY < 0 ? 1.08 : 1 / 1.08);
        else setView(current => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
      }}
      onKeyDown={event => {
        if (event.target !== event.currentTarget) return;
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault(); setView(current => ({ ...current, x: current.x + (event.key === "ArrowLeft" ? 60 : event.key === "ArrowRight" ? -60 : 0), y: current.y + (event.key === "ArrowUp" ? 60 : event.key === "ArrowDown" ? -60 : 0) }));
        } else if (event.key === "+" || event.key === "=") zoomBy(1.2); else if (event.key === "-") zoomBy(1 / 1.2); else if (event.key === "0") fit();
      }}>
      <div className="tree-plane" style={{ width: result.width, height: result.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
        <svg className="tree-connections" width={result.width} height={result.height} aria-hidden="true">
          {result.nodes.map(node => {
            const parent = result.nodes.find(item => item.record.id === node.parent);
            if (!parent) return null;
            const x1 = parent.x + WIDTH, y1 = parent.y + HEIGHT / 2, x2 = node.x, y2 = node.y + HEIGHT / 2, mid = (x1 + x2) / 2;
            return <path key={node.record.id} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} />;
          })}
        </svg>
        {result.nodes.map(({ record, x, y, children }) => {
          const count = attentionCounts(record.id, collapsed.has(record.id));
          const isRoot = !record.parent_id;
          const done = record.status === "completed" || record.status === "abandoned";
          const state = count.blocked ? "blocked" : count.unread ? "unread" : done ? "done" : record.status === "paused" ? "paused" : count.running ? "running" : queuedNodes.has(record.id) || (!isRoot && Number(record.visits || 0) === 0) ? "queued" : "idle";
          const agentState = runningNodes.has(record.id) ? "running" : state === "queued" ? "queued" : state === "done" ? "done" : "idle";
          const title = isRoot ? "Converse" : field(record, "title", "question", "goal") || project.title;
          return <div className={`research-node state-${state} ${selectedId === record.id ? "selected" : ""}`} key={record.id} style={{ left: x, top: y, width: WIDTH, height: HEIGHT }}>
            <button className="research-node-main" aria-label={`${title}, ${state === "unread" ? "check me" : state}`} aria-pressed={selectedId === record.id} onClick={() => { setSelectedId(record.id); setCopied(false); }}>
              <span className="research-node-top"><span>{isRoot ? <Sprout size={13} /> : <GitBranch size={12} />}{isRoot ? "Converse" : label(record.type || "inquiry")}</span><span className={`node-agent-status ${agentState}`} title={agentState === "running" ? "Agent running on this node" : agentState === "queued" ? "Agent work is queued" : agentState === "done" ? "This node has completed its purpose" : "No agent running on this node"}><i />{agentState === "running" ? "live" : agentState}</span></span>
              <strong>{title}</strong>
              <span className="research-node-bottom">{state === "blocked" ? "Needs your input" : state === "unread" ? "Check me" : state === "running" ? "Researching" : state === "queued" ? "Queued" : state === "done" ? "Done" : state === "idle" ? "Idle" : label(state)}{collapsed.has(record.id) && (count.running + count.unread + count.blocked > 0) && <small>{count.blocked ? `${count.blocked} waiting` : count.unread ? `${count.unread} unread` : `${count.running} active`}</small>}</span>
            </button>
            {children > 0 && <button className="node-collapse" aria-label={`${collapsed.has(record.id) ? "Expand" : "Collapse"} ${title}`} aria-expanded={!collapsed.has(record.id)} onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(record.id)) next.delete(record.id); else next.add(record.id); return next; })}>{collapsed.has(record.id) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}<span>{children}</span></button>}
          </div>;
        })}
      </div>
      {!data.tree.length && <div className="canvas-empty"><Sprout size={30} strokeWidth={1.1} /><p>Your research will take shape here.</p><button className="text-link" onClick={() => onDiscuss()}>Open Converse <ChevronRight size={13} /></button></div>}
    </div>
    <div className="canvas-footer"><div className="canvas-legend"><span><i className="running" />{activeJobs ? `${activeJobs} working` : "No active work"}</span><span><i className="unread" />Check me</span><span><i className="blocked" />Needs input</span></div><div className="canvas-controls"><button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.2)}><Minus size={14} /></button><span>{Math.round(view.zoom * 100)}%</span><button aria-label="Zoom in" onClick={() => zoomBy(1.2)}><Plus size={14} /></button><button aria-label="Fit research tree" onClick={fit}><Expand size={14} /></button></div></div>
    {selected && <aside className="node-inspector" aria-label="Research node details">
      <header><span className="eyebrow">{selected.parent_id ? "Research direction" : "Converse"}</span><button className="icon-button" aria-label="Close node details" onClick={() => setSelectedId(null)}><X size={15} /></button></header>
      <div className="node-inspector-scroll">
        <h2>{selected.parent_id ? field(selected, "title", "question", "goal") || project.title : "Converse"}</h2>
        <div className="node-id-row"><code title={selected.id}>{selected.id}</code><button className="icon-button" aria-label="Copy node ID" onClick={() => void navigator.clipboard.writeText(selected.id).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); }).catch(error => onError(errorText(error)))}>{copied ? <Check size={14} /> : <Copy size={14} />}</button></div>
        {selectedAttention.map(item => <article className={`node-attention ${item.pauses_subtree || item.type === "permission" ? "blocking" : ""}`} key={item.id}><strong>{item.pauses_subtree || item.type === "permission" ? "Waiting for your input" : "Research update"}</strong><Markdown>{field(item, "summary", "description")}</Markdown><small>{item.pauses_subtree || item.type === "permission" ? "Discuss in Converse to resolve this. Reading does not resume work." : "Work continues while you review this update."}</small></article>)}
        {field(selected, "summary", "description") && <div className="node-summary"><Markdown>{field(selected, "summary", "description")}</Markdown></div>}
        {field(selected, "question") && field(selected, "question") !== field(selected, "title") && <div className="node-summary"><Markdown>{field(selected, "question")}</Markdown></div>}
        <dl className="node-facts"><div><dt>Status</dt><dd>{selected.status === "completed" || selected.status === "abandoned" ? "Done" : runningNodes.has(selected.id) ? "Running" : queuedNodes.has(selected.id) || (selected.parent_id && Number(selected.visits || 0) === 0) ? "Queued" : selected.status === "paused" ? "Paused" : "Idle"}</dd></div>{selected.estimated_cost !== undefined && <div><dt>Next effort</dt><dd>{money(selected.estimated_cost)}</dd></div>}</dl>
        <NodeRecords data={data} node={selected} />
      </div>
      <footer><button className="button primary" onClick={() => onDiscuss({ nodeId: selected.id, title: field(selected, "title", "question", "goal") || project.title, attentionIds: selectedAttention.map(item => item.id) })}><MessageSquare size={14} />Discuss in Converse</button></footer>
    </aside>}
  </div>;
}

function NodeRecords({ data, node }: { data: ProjectData; node: RecordItem }) {
  const holons = data.holarchy.filter(item => item.assigned_node_id === node.id);
  const holonIds = new Set(holons.map(item => item.id));
  const belongs = (item: RecordItem) => item.node_id === node.id || item.origin_node_id === node.id || holonIds.has(String(item.holon_id || item.origin_holon_id));
  const groups = [{ name: "Evidence", records: data.evidence.filter(belongs) }, { name: "Claims", records: data.claims.filter(belongs) }, { name: "Experiments", records: data.experiments.filter(belongs) }, { name: "Researchers", records: holons }];
  return <div className="node-records">{groups.filter(group => group.records.length).map(group => <details key={group.name}><summary>{group.name}<span>{group.records.length}</span></summary>{group.records.map(item => <article key={item.id}><strong>{field(item, "title", "statement", "claim", "text", "name", "goal") || group.name.slice(0, -1)}</strong>{field(item, "summary", "content", "description") && <Markdown>{field(item, "summary", "content", "description")}</Markdown>}{field(item, "url", "source_url") && /^https?:\/\//.test(field(item, "url", "source_url")) && <a href={field(item, "url", "source_url")} target="_blank" rel="noreferrer">Open source <ExternalLink size={12} /></a>}<small>{label(item.status)}{item.created_at ? ` · ${date(item.created_at)}` : ""}</small></article>)}</details>)}</div>;
}
