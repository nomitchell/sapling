"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ArrowDownToLine, ArrowRight, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, Circle, CircleHelp, CirclePause, ClipboardList, ExternalLink, FlaskConical, FolderOpen, GitBranch, Leaf, Loader2, Menu, MessageSquare, Network, Paperclip, Play, Plus, RefreshCw, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Sprout, Trash2, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { api, Credential, date, defaults, errorText, field, label, Message, money, Project, RecordItem, ResearchEvent, ResearchSettings, Stats } from "@/lib/api";
import { SettingsForm } from "./settings-form";

type Tab = "workspace" | "tree" | "holarchy" | "commons" | "experiments" | "attention" | "ops";
type ProjectData = { messages: Message[]; tree: RecordItem[]; holarchy: RecordItem[]; claims: RecordItem[]; evidence: RecordItem[]; experiments: RecordItem[]; attention: RecordItem[]; events: ResearchEvent[]; stats: Stats; artifacts: RecordItem[] };
const emptyData: ProjectData = { messages: [], tree: [], holarchy: [], claims: [], evidence: [], experiments: [], attention: [], events: [], stats: {}, artifacts: [] };
const tabs: { id: Tab; label: string; icon: typeof Leaf }[] = [{ id: "workspace", label: "Workspace", icon: MessageSquare }, { id: "tree", label: "Research tree", icon: GitBranch }, { id: "holarchy", label: "Holarchy", icon: Network }, { id: "commons", label: "Commons", icon: BookOpen }, { id: "experiments", label: "Experiments", icon: FlaskConical }, { id: "attention", label: "Attention", icon: CircleHelp }, { id: "ops", label: "Ops", icon: SlidersHorizontal }];

function Mark({ size = 30 }: { size?: number }) { return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M16 29V14M16 22C7 23 3 17 4 10C12 9 17 13 16 22ZM16 15C15 7 21 2 28 3C29 10 24 16 16 15Z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /><path d="M16 22L8 14M16 15L24 7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /></svg>; }
function Status({ value }: { value?: string }) { return <span className={`status status-${value || "idle"}`}><span />{label(value || "ready")}</span>; }
function Empty({ icon: Icon = Sprout, title, children, action }: { icon?: typeof Leaf; title: string; children: ReactNode; action?: ReactNode }) { return <div className="empty-state"><div className="empty-icon"><Icon size={26} strokeWidth={1.35} /></div><h3>{title}</h3><p>{children}</p>{action}</div>; }
function Markdown({ children }: { children: string }) { return <div className="markdown"><ReactMarkdown components={{ a: props => <a {...props} target="_blank" rel="noopener noreferrer" /> }}>{children}</ReactMarkdown></div>; }
function Modal({ title, subtitle, children, onClose, wide = false }: { title: string; subtitle?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = ref.current;
    element?.querySelector<HTMLElement>("input,button,select,textarea")?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab" && element) {
        const focusable = Array.from(element.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]'));
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", handler);
    return () => { document.removeEventListener("keydown", handler); previous?.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><div ref={ref} role="dialog" aria-modal="true" aria-label={title} className={`modal ${wide ? "wide" : ""}`}><header className="modal-header"><div><span className="eyebrow">Sapling / Configuration</span><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={20} /></button></header>{children}</div></div>;
}

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("workspace");
  const [data, setData] = useState<ProjectData>(emptyData);
  const [globalSettings, setGlobalSettings] = useState<ResearchSettings>(defaults);
  const [health, setHealth] = useState<"loading" | "online" | "offline">("loading");
  const [streaming, setStreaming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [showSettings, setShowSettings] = useState<"global" | "project" | null>(null);
  const [showDelete, setShowDelete] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [working, setWorking] = useState(false);
  const selected = projects.find(project => project.id === selectedId) || null;
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const pending = data.attention.filter(item => !["resolved", "approved", "denied", "dismissed", "completed"].includes(String(item.status)));

  const loadProjects = useCallback(async () => {
    const list = await api<Project[]>("/projects");
    setProjects(list);
    setHealth("online");
    return list;
  }, []);
  const refreshData = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setDataLoading(true);
    const paths = ["messages", "tree", "holarchy", "claims", "evidence", "experiments", "attention", "events", "stats", "artifacts"] as const;
    const results = await Promise.allSettled(paths.map(path => api(`/projects/${id}/${path}`)));
    if (selectedRef.current !== id) return;
    const next: Partial<ProjectData> = {};
    let firstError = "";
    results.forEach((result, index) => {
      if (result.status === "fulfilled") Object.assign(next, { [paths[index]]: result.value });
      else if (!firstError) firstError = errorText(result.reason);
    });
    setData(previous => ({ ...previous, ...next }));
    if (firstError && !quiet) setError(firstError);
    setDataLoading(false);
  }, []);

  const initialize = useCallback(async () => {
    setLoading(true); setError("");
    const results = await Promise.allSettled([loadProjects(), api<Partial<ResearchSettings>>("/settings")]);
    if (results[0].status === "fulfilled") {
      const list = results[0].value;
      const saved = window.localStorage.getItem("sapling-project");
      setSelectedId(current => current || (list.find(item => item.id === saved)?.id ?? list[0]?.id ?? null));
    } else { setHealth("offline"); setError("The research runtime is unavailable. Start the local Sapling server, then reconnect."); }
    if (results[1].status === "fulfilled") setGlobalSettings({ ...defaults, ...results[1].value });
    setLoading(false);
  }, [loadProjects]);
  useEffect(() => { void initialize(); }, [initialize]);
  useEffect(() => {
    setData(emptyData); setError("");
    if (!selectedId) return;
    window.localStorage.setItem("sapling-project", selectedId);
    void refreshData(selectedId);
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource(`/api/projects/${selectedId}/events/stream`);
    source.onopen = () => setStreaming(true);
    source.onerror = () => setStreaming(false);
    source.addEventListener("research", () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => { void refreshData(selectedId, true); void loadProjects().catch(() => {}); }, 250);
    });
    const poll = setInterval(() => { void refreshData(selectedId, true); void loadProjects().catch(() => setHealth("offline")); }, 15000);
    return () => { source.close(); clearInterval(poll); if (debounce) clearTimeout(debounce); setStreaming(false); };
  }, [selectedId, refreshData, loadProjects]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(timer); }, [notice]);

  async function runAction(action: () => Promise<void>) {
    setWorking(true); setError("");
    try { await action(); } catch (err) { setError(errorText(err)); } finally { setWorking(false); }
  }
  const refresh = () => { if (selectedId) void refreshData(selectedId); void loadProjects().catch(err => setError(errorText(err))); };
  const active = selected && ["running", "active", "researching"].includes(selected.status);

  return <div className="app-shell">
    {sidebarOpen && <div className="sidebar-scrim" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
      <button className="brand" onClick={() => { setSelectedId(null); setSidebarOpen(false); }} aria-label="Sapling home"><Mark /><span>sapling<span className="brand-period">.</span></span></button>
      <div className="sidebar-caption">A place for ideas to grow.</div>
      <button className="new-project-button" onClick={() => setShowCreate(true)} disabled={health !== "online"}><Plus size={17} /><span>New project</span><span className="shortcut">+</span></button>
      <div className="sidebar-section"><span>Your projects</span><span>{projects.length.toString().padStart(2, "0")}</span></div>
      {projects.length > 5 && <div className="project-search"><Search size={14} /><input aria-label="Find a project" placeholder="Find a project" value={query} onChange={event => setQuery(event.target.value)} /></div>}
      <nav className="project-list" aria-label="Projects">{projects.filter(project => project.title.toLowerCase().includes(query.toLowerCase())).map(project => <button key={project.id} className={`project-nav ${project.id === selectedId ? "selected" : ""}`} onClick={() => { setSelectedId(project.id); setTab("workspace"); setSidebarOpen(false); }}><span className={`project-dot ${["running", "active", "researching"].includes(project.status) ? "active" : ""}`} /><span>{project.title}</span>{project.id === selectedId && <ChevronRight size={13} />}</button>)}{!projects.length && !loading && <div className="sidebar-empty">Your research starts here.<br />Create a project to begin.</div>}</nav>
      <div className="sidebar-bottom"><div className="local-runtime"><span className={`runtime-dot ${health}`} /><div><strong>Local workspace</strong><small>{health === "online" ? "Runtime connected" : health === "loading" ? "Connecting…" : "Runtime offline"}</small></div><ShieldCheck size={17} /></div><button className="sidebar-settings" onClick={() => setShowSettings("global")}><Settings2 size={17} /><span>Settings & connections</span></button><div className="sidebar-footnote"><span>SAPLING</span><span>v0.1 / local</span></div></div>
    </aside>

    <main className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar"><Menu size={20} /></button><span className="breadcrumb-root">Workspace</span><ChevronRight size={13} /><span>{selected?.title || "Overview"}</span></div><div className="topbar-right"><span className="local-tag"><span />On your machine</span><button className="icon-button" aria-label="Refresh workspace" title="Refresh workspace" onClick={health === "offline" ? initialize : refresh}><RefreshCw size={15} className={loading || dataLoading ? "spinning" : ""} /></button></div></header>
      {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span>{health === "offline" && <button onClick={initialize}>Reconnect</button>}<button className="icon-button" onClick={() => setError("")} aria-label="Dismiss error"><X size={15} /></button></div>}
      {notice && <div className="notice" role="status"><Check size={16} />{notice}</div>}
      {loading ? <div className="loading-page"><Mark size={40} /><span>Opening your workspace…</span></div> : !selected ? <Welcome projects={projects} onCreate={() => setShowCreate(true)} onSelect={id => { setSelectedId(id); setTab("workspace"); }} onSettings={() => setShowSettings("global")} online={health === "online"} /> : <>
        <section className="project-header"><div className="project-title-group"><div className="eyebrow">Research project <span className="eyebrow-slash">/</span> <Status value={selected.status} /></div><h1>{selected.title}</h1><p>{selected.goal}</p></div><div className="project-actions"><button className="icon-button outlined" title="Project settings" aria-label="Project settings" onClick={() => setShowSettings("project")}><Settings2 size={17} /></button><button className={active ? "button secondary" : "button primary"} disabled={working} onClick={() => void runAction(async () => { await api(`/projects/${selected.id}/${active ? "pause" : "resume"}`, { method: "POST" }); await loadProjects(); await refreshData(selected.id, true); })}>{working ? <Loader2 size={15} className="spinning" /> : active ? <CirclePause size={15} /> : <Play size={15} />}{active ? "Pause research" : "Start research"}</button></div></section>
        <nav className="workspace-tabs" aria-label="Project sections">{tabs.map(item => <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)} aria-current={tab === item.id ? "page" : undefined}><item.icon size={15} /><span>{item.label}</span>{item.id === "attention" && pending.length > 0 && <span className="tab-badge">{pending.length}</span>}</button>)}</nav>
        <div className="project-body">
          {tab === "workspace" && <Chat key={selected.id} project={selected} data={data} pending={pending.length} streaming={streaming} onAttention={() => setTab("attention")} onSettings={() => setShowSettings("project")} onSent={() => { refresh(); }} onError={setError} onNotice={setNotice} />}
          {tab === "tree" && <ResearchTree records={data.tree} onChange={refresh} onError={setError} onNotice={setNotice} />}
          {tab === "holarchy" && <Holarchy records={data.holarchy} onChange={refresh} onError={setError} />}
          {tab === "commons" && <Commons claims={data.claims} evidence={data.evidence} artifacts={data.artifacts} />}
          {tab === "experiments" && <Experiments records={data.experiments} />}
          {tab === "attention" && <Attention records={data.attention} onChange={refresh} onError={setError} />}
          {tab === "ops" && <Operations project={selected} data={data} onDelete={() => setShowDelete(true)} />}
        </div>
      </>}
    </main>

    {showCreate && <CreateProject settings={globalSettings} onClose={() => setShowCreate(false)} onCreated={async project => { await loadProjects(); setSelectedId(project.id); setTab("workspace"); setShowCreate(false); }} />}
    {showSettings && <SettingsDialog project={showSettings === "project" ? selected : null} defaults={globalSettings} onClose={() => setShowSettings(null)} onSaved={async next => { if (showSettings === "global") setGlobalSettings(next); await loadProjects(); setNotice("Settings saved."); }} />}
    {showDelete && selected && <Modal title="Archive this project?" onClose={() => setShowDelete(false)}><div className="modal-content"><p className="delete-description">This pauses <strong>{selected.title}</strong> and removes it from your project list. Research history and files are retained locally.</p><div className="modal-footer"><button className="button secondary" onClick={() => setShowDelete(false)}>Keep project</button><button className="button danger" disabled={working} onClick={() => void runAction(async () => { await api(`/projects/${selected.id}`, { method: "DELETE" }); setShowDelete(false); setSelectedId(null); await loadProjects(); })}>{working ? "Deleting…" : "Delete project"}</button></div></div></Modal>}
  </div>;
}

function Welcome({ projects, onCreate, onSelect, onSettings, online }: { projects: Project[]; onCreate: () => void; onSelect: (id: string) => void; onSettings: () => void; online: boolean }) {
  return <div className="welcome"><div className="welcome-heading"><span className="eyebrow">Your research, taking root</span><h1>Good questions<br />deserve room to <em>grow.</em></h1><p>A workspace for open-ended research. Follow an idea, explore its branches, and build on what you discover.</p><button className="button primary welcome-create" onClick={onCreate} disabled={!online}><Plus size={16} />Create a project<ArrowRight size={17} /></button></div><div className="botanical" aria-hidden="true"><svg viewBox="0 0 340 380" fill="none"><path d="M157 357C150 284 178 259 169 199C162 154 167 122 190 76" stroke="currentColor" strokeWidth="1.5" /><path d="M170 248C105 255 65 218 67 163C120 158 174 186 170 248ZM169 193C226 197 271 161 266 101C207 109 171 139 169 193ZM174 127C137 109 132 69 152 32C185 49 202 88 174 127ZM160 310C207 306 237 278 231 241C191 250 165 272 160 310Z" stroke="currentColor" strokeWidth="1.4" /><path d="M169 248L91 187M169 193L242 127M174 127L156 58M161 309L215 261" stroke="currentColor" strokeWidth=".8" /><circle cx="170" cy="248" r="4" fill="var(--paper)" stroke="currentColor" /><circle cx="169" cy="193" r="4" fill="var(--paper)" stroke="currentColor" /><circle cx="174" cy="127" r="4" fill="var(--paper)" stroke="currentColor" /><path d="M135 362H183M144 369H175" stroke="currentColor" strokeWidth="1.1" /></svg><span>IDEAS ARE LIVING THINGS.</span></div><section className="welcome-projects"><div className="section-heading"><div><span className="eyebrow">Research notebook</span><h2>{projects.length ? "Your projects" : "A fresh page"}</h2></div><span className="count-label">{projects.length.toString().padStart(2, "0")} projects</span></div>{projects.length ? <div className="project-table">{projects.map((project, index) => <button onClick={() => onSelect(project.id)} key={project.id}><span className="row-number">{(index + 1).toString().padStart(2, "0")}</span><span className="project-row-title"><strong>{project.title}</strong><small>{project.goal}</small></span><Status value={project.status} /><span className="project-row-cost">{money(project.budget_spent)}</span><ArrowRight size={17} /></button>)}</div> : <div className="welcome-empty"><FolderOpen size={24} strokeWidth={1.2} /><div><strong>No projects yet</strong><p>Bring a question, an ambition, or a problem worth exploring.</p></div><span>Start anywhere.</span></div>}</section><div className="welcome-footer"><span><ShieldCheck size={14} />Saved locally. Models and search use external services.</span><button onClick={onSettings}>Configure models & connections<ArrowRight size={14} /></button></div></div>;
}

function CreateProject({ settings, onClose, onCreated }: { settings: ResearchSettings; onClose: () => void; onCreated: (project: Project) => Promise<void> }) {
  const [title, setTitle] = useState(""); const [goal, setGoal] = useState(""); const [config, setConfig] = useState(settings); const [advanced, setAdvanced] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { const project = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ title: title.trim(), goal: goal.trim(), settings: config }) }); await onCreated(project); } catch (err) { setError(errorText(err)); } finally { setBusy(false); } }
  return <Modal title="Plant an idea." subtitle="Give your research a home. You can refine the direction as you go." onClose={onClose}><form className="modal-content" onSubmit={submit}><label>Project name<input autoFocus required maxLength={200} placeholder="What are you working on?" value={title} onChange={event => setTitle(event.target.value)} /></label><label>Research direction<textarea required rows={4} placeholder="Describe what you want to understand, investigate, or build. Include any constraints that matter." value={goal} onChange={event => setGoal(event.target.value)} /></label><button type="button" className="disclosure" onClick={() => setAdvanced(!advanced)}>{advanced ? <ChevronDown size={16} /> : <ChevronRight size={16} />}Research settings<span>{money(config.budget_total)} budget · {label(config.permission_mode)}</span></button>{advanced && <SettingsForm value={config} onChange={setConfig} compact />}{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-footer"><span className="field-note">Research starts when you are ready.</span><button className="button primary" disabled={busy || !title.trim() || !goal.trim()}>{busy ? <Loader2 size={15} className="spinning" /> : <Sprout size={16} />}Create project</button></div></form></Modal>;
}

function SettingsDialog({ project, defaults: initialDefaults, onClose, onSaved }: { project: Project | null; defaults: ResearchSettings; onClose: () => void; onSaved: (settings: ResearchSettings) => Promise<void> }) {
  const [config, setConfig] = useState<ResearchSettings>({ ...initialDefaults, ...(project?.settings || {}), ...(project ? { budget_total: project.budget_total } : {}) });
  const [title, setTitle] = useState(project?.title || "");
  const [goal, setGoal] = useState(project?.goal || "");
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!project) void api<Credential[] | Record<string, Credential>>("/credentials")
      .then(data => setCredentials(Array.isArray(data) ? data : Object.entries(data).map(([provider, value]) => ({ ...value, provider }))))
      .catch(err => setError(errorText(err)));
  }, [project]);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const body = project ? { title: title.trim(), goal: goal.trim(), settings: config } : config;
      await api(project ? "/projects/" + project.id : "/settings", { method: "PATCH", body: JSON.stringify(body) });
      await onSaved(config); onClose();
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return <Modal title={project ? "Project settings" : "Make yourself at home."} subtitle={project ? "Tune this project’s researchers, budget, and permissions." : "Connect a model and set defaults for new projects."} onClose={onClose}>
    <form onSubmit={save} className="modal-content">
      {project ? <><label>Project name<input required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} /></label><label>Research direction<textarea required rows={3} maxLength={20000} value={goal} onChange={event => setGoal(event.target.value)} /></label></> : <>
        <CredentialConnection provider="openai" title="OpenAI" description="Model provider" connected={credentials.some(item => item.provider === "openai" && (item.configured || item.present))} onSaved={() => setCredentials(previous => [...previous.filter(item => item.provider !== "openai"), { provider: "openai", configured: true }])} />
        <CredentialConnection provider="openalex" title="OpenAlex" description="Scholarly literature" connected={credentials.some(item => item.provider === "openalex" && (item.configured || item.present))} onSaved={() => setCredentials(previous => [...previous.filter(item => item.provider !== "openalex"), { provider: "openalex", configured: true }])} />
      </>}
      <SettingsForm value={config} onChange={setConfig} />
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button></div>
    </form>
  </Modal>;
}

function CredentialConnection({ provider, title, description, connected, onSaved }: { provider: string; title: string; description: string; connected: boolean; onSaved: () => void }) {
  const [key, setKey] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function save() {
    setBusy(true); setError("");
    try { await api("/credentials", { method: "POST", body: JSON.stringify({ provider, key: key.trim() }) }); setKey(""); onSaved(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return <div className="connection-section"><div className="connection-heading"><div className="connection-icon">{provider === "openai" ? <Sparkles size={18} /> : <BookOpen size={18} />}</div><div><strong>{title}</strong><small>{description}</small></div><span className={connected ? "connection-status connected" : "connection-status"}>{connected ? "Connected" : "Not connected"}</span></div><label>{title} API key<div className="key-field"><input type="password" aria-label={title + " API key"} placeholder={connected ? "Replace your API key" : "Enter your API key"} value={key} onChange={event => setKey(event.target.value)} autoComplete="new-password" /><button className="button secondary" type="button" disabled={key.trim().length < 5 || busy} onClick={save}>{busy ? "Saving…" : "Save key"}</button></div><small>Saved in your operating-system credential vault. Excluded from research exports.</small></label>{error && <p className="form-error" role="alert">{error}</p>}</div>;
}

function Chat({ project, data, pending, streaming, onAttention, onSettings, onSent, onError, onNotice }: { project: Project; data: ProjectData; pending: number; streaming: boolean; onAttention: () => void; onSettings: () => void; onSent: () => void; onError: (error: string) => void; onNotice: (notice: string) => void }) {
  const [draft, setDraft] = useState(""); const [sending, setSending] = useState(false); const [uploading, setUploading] = useState(false); const input = useRef<HTMLInputElement>(null); const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [data.messages.length]);
  useEffect(() => { setDraft(""); }, [project.id]);
  async function send(event?: FormEvent) { event?.preventDefault(); if (!draft.trim() || sending) return; setSending(true); try { await api(`/projects/${project.id}/messages`, { method: "POST", body: JSON.stringify({ text: draft.trim() }) }); setDraft(""); onSent(); } catch (err) { onError(errorText(err)); } finally { setSending(false); } }
  async function upload(files: FileList | null) { if (!files?.length) return; setUploading(true); try { for (const file of Array.from(files)) { const body = new FormData(); body.append("file", file); await api(`/projects/${project.id}/artifacts`, { method: "POST", body }); } onNotice(`${files.length === 1 ? "File" : `${files.length} files`} added to the project.`); onSent(); } catch (err) { onError(errorText(err)); } finally { setUploading(false); if (input.current) input.current.value = ""; } }
  const budget = Number(project.budget_total) || 0; const spent = Number(project.budget_spent) || 0; const progress = budget > 0 ? Math.min(100, spent / budget * 100) : 0;
  return <div className="chat-layout"><section className="chat-panel"><div className="panel-overline"><span><span className={`runtime-dot ${streaming ? "online" : "loading"}`} />Root researcher</span><span>{streaming ? "Live updates" : "Reconnecting updates"}</span></div><div className="messages" aria-live="polite">{!data.messages.length ? <div className="chat-welcome"><div className="chat-welcome-mark"><Mark size={40} /></div><span className="eyebrow">The beginning of something</span><h2>Let’s see where<br />this takes us.</h2><p>Share context, attach a paper, or give your research a direction. Your root researcher will keep the bigger picture in view.</p><div className="chat-start-note"><Circle size={7} />Send a message or start research when your model and budget are ready.</div></div> : data.messages.map(message => <article className={`message message-${message.role}`} key={message.id}><div className="message-header"><span className="message-avatar">{message.role === "user" ? "Y" : <Mark size={19} />}</span><strong>{message.role === "user" ? "You" : message.role === "assistant" ? "Root researcher" : label(message.role)}</strong><time>{date(message.created_at)}</time></div><div className="message-content"><Markdown>{message.text}</Markdown></div></article>)}<div ref={bottom} /></div><form className="composer" onSubmit={send}><textarea aria-label="Message the root researcher" placeholder="Add a thought, ask a question, change direction…" value={draft} rows={2} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><div className="composer-bottom"><input ref={input} type="file" accept=".pdf,.txt,.md,.csv,.json,.html,.htm" multiple hidden onChange={event => void upload(event.target.files)} /><button type="button" className="attach-button" onClick={() => input.current?.click()} disabled={uploading}>{uploading ? <Loader2 size={15} className="spinning" /> : <Paperclip size={15} />}<span>{uploading ? "Adding files…" : "Attach context"}</span></button><span className="composer-hint">Shift + Enter for a new line</span><button type="submit" className="send-button" aria-label="Send message" disabled={!draft.trim() || sending}>{sending ? <Loader2 size={17} className="spinning" /> : <ArrowUp size={19} />}</button></div></form><div className="composer-caption"><button className="composer-model" onClick={onSettings} aria-label="Change model and reasoning effort" title="Change model and reasoning effort">{project.settings?.model || "Choose a model"}<span>·</span>{project.settings?.reasoning_effort == null ? "model default" : label(project.settings.reasoning_effort) + " reasoning"}<ChevronDown size={11} /></button><span>·</span>{label(project.settings?.permission_mode || "balanced")} permissions</div></section><aside className="context-rail"><div className="rail-section"><div className="rail-heading">Project pulse<span className="little-line" /></div><div className="pulse-stats"><div><strong>{data.tree.length.toString().padStart(2, "0")}</strong><span>Branches</span></div><div><strong>{data.holarchy.length.toString().padStart(2, "0")}</strong><span>Researchers</span></div><div><strong>{data.experiments.length.toString().padStart(2, "0")}</strong><span>Experiments</span></div><div><strong>{data.claims.length.toString().padStart(2, "0")}</strong><span>Claims</span></div></div></div><div className="rail-section"><div className="rail-heading">Research budget<span>USD</span></div><div className="budget-number">{money(spent)}<span>/ {money(budget)}</span></div><div className="budget-track"><span style={{ width: `${progress}%` }} /></div><p className="rail-note">{money(Math.max(0, budget - spent))} remaining</p></div><div className="rail-section"><div className="rail-heading">Needs your attention{pending > 0 && <span className="tab-badge">{pending}</span>}</div>{pending ? <button className="attention-prompt" onClick={onAttention}><CircleHelp size={18} /><span>{pending} {pending === 1 ? "decision is" : "decisions are"} waiting<small>Review requests to keep research moving.</small></span><ArrowRight size={15} /></button> : <div className="all-clear"><Check size={15} /><span>All clear for now.<small>Decisions will appear here.</small></span></div>}</div><div className="rail-section"><div className="rail-heading">Recent activity</div>{data.events.length ? <div className="mini-events">{data.events.slice(-4).reverse().map(event => <div key={event.id}><span className="event-dot" /><div><strong>{label(event.type)}</strong><time>{date(event.created_at)}</time></div></div>)}</div> : <p className="rail-note">Your research story will unfold here.</p>}</div><div className="rail-botanical"><Sprout size={27} strokeWidth={1.1} /><span>One question can open<br />a whole new branch.</span></div></aside></div>;
}

function ViewHeading({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) { return <div className="view-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>{children}</div>; }
function DetailPanel({ record, onClose, actions }: { record: RecordItem; onClose: () => void; actions?: ReactNode }) {
  return <aside className="record-detail"><div className="detail-heading"><span className="eyebrow">Research record</span><button className="icon-button" onClick={onClose} aria-label="Close details"><X size={17} /></button></div><h3>{field(record, "title", "name", "question", "text", "claim") || "Details"}</h3><Status value={record.status} />{actions && <div className="detail-actions">{actions}</div>}<dl>{Object.entries(record).filter(([key, value]) => !["id", "title", "status"].includes(key) && value !== null && value !== "" && value !== undefined).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{typeof value === "object" ? <pre>{JSON.stringify(value, null, 2)}</pre> : /code|stdout|stderr|traceback/.test(key) ? <pre>{String(value)}</pre> : <Markdown>{String(value)}</Markdown>}</dd></div>)}</dl></aside>;
}

function ResearchTree({ records, onChange, onError, onNotice }: { records: RecordItem[]; onChange: () => void; onError: (message: string) => void; onNotice: (message: string) => void }) {
  const [focused, setFocused] = useState<string | null>(null); const selected = records.find(item => item.id === focused); const ids = new Set(records.map(item => item.id));
  const roots = records.filter(item => !item.parent_id || !ids.has(String(item.parent_id))); const visited = new Set<string>();
  function renderNode(node: RecordItem, depth = 0): ReactNode { if (visited.has(node.id) || depth > 50) return null; visited.add(node.id); const children = records.filter(item => item.parent_id === node.id); return <div className="tree-branch" key={node.id}><button className={`tree-node ${focused === node.id ? "focused" : ""}`} onClick={() => setFocused(node.id)}><span className="node-symbol">{depth === 0 ? <Sprout size={20} /> : <GitBranch size={17} />}</span><span className="node-text"><span className="eyebrow">{depth === 0 ? "Root" : `Branch ${depth}`} {node.type ? ` / ${label(node.type)}` : ""}</span><strong>{field(node, "title", "question", "name", "goal") || "Research branch"}</strong>{field(node, "summary", "description") && <small>{field(node, "summary", "description")}</small>}</span><Status value={node.status} /><ChevronRight size={16} /></button>{children.length > 0 && <div className="tree-children">{children.map(child => renderNode(child, depth + 1))}</div>}</div>; }
  return <section className="data-view"><ViewHeading eyebrow="Branching inquiry" title="Room to explore." description="Follow each line of inquiry, from the original question to the findings it produces."><span className="count-label">{records.length} branches</span></ViewHeading>{!records.length ? <Empty icon={GitBranch} title="Every tree starts with a question.">Research branches will appear as your researchers explore the project.</Empty> : <div className={`split-view ${selected ? "has-detail" : ""}`}><div className="tree-canvas">{roots.map(node => renderNode(node))}{records.filter(node => !visited.has(node.id)).map(node => renderNode(node))}</div>{selected && <DetailPanel record={selected} onClose={() => setFocused(null)} actions={<NodePriority key={selected.id} nodeId={selected.id} onChange={onChange} onError={onError} onNotice={onNotice} />} />}</div>}</section>;
}

function NodePriority({ nodeId, onChange, onError, onNotice }: { nodeId: string; onChange: () => void; onError: (message: string) => void; onNotice: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  async function prioritize() {
    setBusy(true);
    try {
      await api("/nodes/" + nodeId + "/prioritize", { method: "POST", body: JSON.stringify({}) });
      onNotice("Priority sent to the root researcher.");
      onChange();
    } catch (err) { onError(errorText(err)); } finally { setBusy(false); }
  }
  return <button className="button secondary" disabled={busy} onClick={prioritize}>{busy ? <Loader2 size={14} className="spinning" /> : <ArrowUp size={14} />}{busy ? "Sending priority..." : "Prioritize this direction"}</button>;
}

function Holarchy({ records, onChange, onError }: { records: RecordItem[]; onChange: () => void; onError: (message: string) => void }) {
  const [focused, setFocused] = useState<string | null>(null); const selected = records.find(item => item.id === focused);
  return <section className="data-view"><ViewHeading eyebrow="The research collective" title="Independent minds. Shared direction." description="Each researcher owns a piece of the question and contributes to the whole."><span className="count-label">{records.length} researchers</span></ViewHeading>{!records.length ? <Empty icon={Network} title="Your research collective is taking shape.">Researchers appear here when the project begins. Inspect their roles, progress, and relationships.</Empty> : <div className={`split-view ${selected ? "has-detail" : ""}`}><div className="researcher-list">{records.map((item, index) => <button key={item.id} className={`researcher-row ${focused === item.id ? "focused" : ""}`} onClick={() => setFocused(item.id)}><span className="researcher-index">H{(index + 1).toString().padStart(2, "0")}</span><div><span className="eyebrow">{field(item, "role", "kind") || "Researcher"}</span><h3>{field(item, "title", "name", "goal") || "Researcher"}</h3><p>{field(item, "summary", "objective", "description")}</p><small>{item.parent_id ? `Reports to ${records.find(parent => parent.id === item.parent_id)?.title || "parent researcher"}` : "Root researcher"}</small></div><Status value={item.status} /><ChevronRight size={16} /></button>)}</div>{selected && <DetailPanel record={selected} onClose={() => setFocused(null)} actions={<HolonControl record={selected} onChange={onChange} onError={onError} />} />}</div>}</section>;
}

function HolonControl({ record, onChange, onError }: { record: RecordItem; onChange: () => void; onError: (error: string) => void }) {
  const [busy, setBusy] = useState(false);
  const paused = record.status === "paused";
  async function control() {
    setBusy(true);
    try { await api("/holons/" + record.id + "/" + (paused ? "resume" : "pause"), { method: "POST" }); onChange(); }
    catch (err) { onError(errorText(err)); } finally { setBusy(false); }
  }
  return <button className="button secondary" disabled={busy || record.status === "completed"} onClick={control}>{busy ? <Loader2 size={14} className="spinning" /> : paused ? <Play size={14} /> : <CirclePause size={14} />}{paused ? "Resume researcher & descendants" : "Pause researcher & descendants"}</button>;
}

function Commons({ claims, evidence, artifacts }: { claims: RecordItem[]; evidence: RecordItem[]; artifacts: RecordItem[] }) {
  const [section, setSection] = useState("claims"); const [query, setQuery] = useState(""); const [focused, setFocused] = useState<string | null>(null); const records = section === "claims" ? claims : section === "evidence" ? evidence : artifacts; const filtered = records.filter(item => JSON.stringify(item).toLowerCase().includes(query.toLowerCase())); const selected = records.find(item => item.id === focused);
  return <section className="data-view"><ViewHeading eyebrow="Shared knowledge" title="A growing body of understanding." description="Claims, sources, and files — collected in one place, grounded in the research." /><div className="data-toolbar"><div className="segmented">{[{ id: "claims", name: "Claims", count: claims.length }, { id: "evidence", name: "Evidence", count: evidence.length }, { id: "files", name: "Files", count: artifacts.length }].map(item => <button className={section === item.id ? "active" : ""} onClick={() => { setSection(item.id); setFocused(null); }} key={item.id}>{item.name}<span>{item.count}</span></button>)}</div><div className="search-field"><Search size={15} /><input aria-label="Search knowledge" placeholder="Search the commons" value={query} onChange={event => setQuery(event.target.value)} /></div></div>{!filtered.length ? <Empty icon={BookOpen} title={query ? "No matching records." : section === "claims" ? "Understanding takes shape here." : section === "evidence" ? "A place for the evidence." : "Bring your own context."}>{query ? "Try another search term." : section === "claims" ? "As research progresses, claims and their supporting evidence become a shared foundation." : section === "evidence" ? "Papers, web sources, and experimental observations will appear here." : "Attach documents from the workspace to make them available to your researchers."}</Empty> : <div className={`split-view ${selected ? "has-detail" : ""}`}><div className="knowledge-list">{filtered.map((item, index) => <article className="knowledge-row" key={item.id}><span className="row-number">{(index + 1).toString().padStart(2, "0")}</span><div><button className="text-title" onClick={() => setFocused(item.id)}>{field(item, "title", "text", "claim", "statement", "filename", "name") || `Research ${section === "claims" ? "claim" : "source"}`}</button>{field(item, "summary", "description", "content", "abstract") && <p>{field(item, "summary", "description", "content", "abstract")}</p>}<div className="record-meta"><span>{label(item.kind || item.type || item.source || section.slice(0, -1))}</span>{item.confidence !== undefined && <span>{Math.round(Number(item.confidence) * 100)}% confidence</span>}{item.status && <Status value={item.status} />}</div></div>{section === "files" ? <a className="icon-button outlined" href={`/api/artifacts/${item.id}/download`} title="Download file" aria-label="Download file"><ArrowDownToLine size={16} /></a> : /^https?:\/\//.test(field(item, "url", "source_url")) ? <a className="icon-button" href={field(item, "url", "source_url")} target="_blank" rel="noopener noreferrer" title="Open source" aria-label="Open source"><ExternalLink size={16} /></a> : <button className="icon-button" onClick={() => setFocused(item.id)} aria-label="View record"><ChevronRight size={16} /></button>}</article>)}</div>{selected && <DetailPanel record={selected} onClose={() => setFocused(null)} />}</div>}</section>;
}

function Experiments({ records }: { records: RecordItem[] }) {
  const [focused, setFocused] = useState<string | null>(null); const selected = records.find(item => item.id === focused);
  return <section className="data-view"><ViewHeading eyebrow="Ideas meet evidence" title="The workbench." description="Inspect executed code, results, and observations from your researchers."><span className="count-label">{records.length} experiments</span></ViewHeading>{!records.length ? <Empty icon={FlaskConical} title="Ready when an idea needs testing.">Experiments will appear here with their code, execution logs, and results.</Empty> : <div className={`split-view ${selected ? "has-detail" : ""}`}><div className="experiment-list"><div className="table-header"><span>Experiment</span><span>Status</span><span>Created</span><span /></div>{records.map((item, index) => <button key={item.id} className="experiment-row" onClick={() => setFocused(item.id)}><span><span className="row-number">E{(index + 1).toString().padStart(2, "0")}</span><strong>{field(item, "title", "name", "hypothesis") || "Experiment"}</strong><small>{field(item, "summary", "description")}</small></span><Status value={item.status} /><time>{date(item.created_at)}</time><ChevronRight size={15} /></button>)}</div>{selected && <DetailPanel record={selected} onClose={() => setFocused(null)} />}</div>}</section>;
}

function Attention({ records, onChange, onError }: { records: RecordItem[]; onChange: () => void; onError: (error: string) => void }) {
  const [showResolved, setShowResolved] = useState(false); const pending = records.filter(item => !["resolved", "approved", "denied", "dismissed", "completed"].includes(String(item.status))); const visible = showResolved ? records : pending;
  return <section className="data-view attention-view"><ViewHeading eyebrow="A human in the loop" title="Your perspective matters." description="Review permissions and help researchers make the decisions that need you."><button className="button secondary" onClick={() => setShowResolved(!showResolved)}>{showResolved ? "Show pending" : "Show all"}<span>{showResolved ? pending.length : records.length}</span></button></ViewHeading>{!visible.length ? <Empty icon={Check} title="Nothing needs your attention.">When research reaches a decision or requests permission, you’ll find it here.</Empty> : <div className="attention-list">{visible.map(item => <AttentionItem key={item.id} item={item} onChange={onChange} onError={onError} />)}</div>}</section>;
}

function AttentionItem({ item, onChange, onError }: { item: RecordItem; onChange: () => void; onError: (error: string) => void }) {
  const [response, setResponse] = useState(""); const [remember, setRemember] = useState(false); const [busy, setBusy] = useState(false); const resolved = ["resolved", "approved", "denied", "dismissed", "completed"].includes(String(item.status)); const permission = /permission|approval/.test(field(item, "type", "kind", "category")) || item.action !== undefined;
  async function respond(approve?: boolean) { setBusy(true); try { await api(`/attention/${item.id}/respond`, { method: "POST", body: JSON.stringify({ response, approve, remember }) }); onChange(); } catch (err) { onError(errorText(err)); } finally { setBusy(false); } }
  return <article className={`attention-item ${resolved ? "resolved" : ""}`}><div className="attention-item-header"><span className="eyebrow">{permission ? <ShieldCheck size={14} /> : <CircleHelp size={14} />}{label(item.type || item.kind || "Decision")}</span><Status value={item.status} /></div><h3>{field(item, "title", "question") || (permission ? "Permission requested" : "Researcher needs your input")}</h3><Markdown>{field(item, "description", "message", "prompt", "body", "reason")}</Markdown>{item.work_order !== undefined && <div className="permission-request"><span className="eyebrow">Action category: {label(item.category)}</span><pre>{JSON.stringify(item.work_order, null, 2)}</pre></div>}{item.payload !== undefined && <details className="request-details"><summary>Request details</summary><pre>{typeof item.payload === "string" ? item.payload : JSON.stringify(item.payload, null, 2)}</pre></details>}{!resolved && <><label className="response-label"><span>{permission ? "A note for the researcher (optional)" : "Your response"}</span><textarea rows={2} placeholder={permission ? "Add context or constraints…" : "Share your guidance…"} value={response} onChange={event => setResponse(event.target.value)} /></label><div className="attention-actions">{permission && <label className="checkbox-label"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)} />Allow this category for this project</label>}<div>{permission && <button className="button secondary" disabled={busy} onClick={() => void respond(false)}>Deny</button>}<button className="button primary" disabled={busy || (!permission && !response.trim())} onClick={() => void respond(permission ? true : undefined)}>{busy ? "Sending…" : permission ? "Approve" : "Send response"}<ArrowRight size={14} /></button></div></div></>}{resolved && field(item, "response", "resolution") && <p className="resolved-response">{field(item, "response", "resolution")}</p>}</article>;
}

function Operations({ project, data, onDelete }: { project: Project; data: ProjectData; onDelete: () => void }) {
  const [query, setQuery] = useState(""); const events = data.events.filter(event => `${event.type} ${JSON.stringify(event.payload)}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="data-view"><ViewHeading eyebrow="Behind the research" title="An open notebook of activity." description="Follow the event log, inspect resource use, and export your research data." /><div className="ops-summary"><div><span>Model</span><strong>{project.settings?.model || "Not configured"}</strong></div><div><span>Execution</span><strong>{label(project.settings?.execution_backend || "docker")}</strong></div><div><span>Permissions</span><strong>{label(project.settings?.permission_mode || "balanced")}</strong></div><div><span>Estimated spending</span><strong>{money(project.budget_spent)} <small>/ {money(project.budget_total)}</small></strong></div></div><div className="data-toolbar"><h3 className="toolbar-title">Event log <span>{data.events.length}</span></h3><div className="search-field"><Search size={15} /><input aria-label="Filter events" placeholder="Filter activity" value={query} onChange={event => setQuery(event.target.value)} /></div></div>{!events.length ? <Empty icon={ClipboardList} title={query ? "No matching events." : "The notebook is open."}>{query ? "Try another filter." : "Actions, decisions, and runtime events will be recorded here as research progresses."}</Empty> : <div className="event-log">{[...events].reverse().map(event => <details key={event.id}><summary><time>{date(event.created_at)}</time><span className="event-dot" /><strong>{label(event.type)}</strong><ChevronDown size={14} /></summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>)}</div>}<div className="ops-footer"><div><h3>Research data</h3><p>Export the decisions and observations collected for future training.</p><a className="button secondary" href={`/api/projects/${project.id}/training-data/export`} download><ArrowDownToLine size={15} />Export training data</a></div><button className="button danger-quiet" onClick={onDelete}><Trash2 size={15} />Archive project</button></div></section>;
}
