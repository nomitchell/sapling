"use client";
import {
  api,
  date,
  defaults,
  emptyData,
  errorText,
  loadEvents,
  mergeResearchEvent,
  money,
  ConversationReference,
  Project,
  ProjectData,
  ResearchSettings,
  ResearchEvent,
} from "@/lib/api";
import {
  ArrowRight,
  CirclePause,
  Folder,
  ChevronDown,
  Loader2,
  Menu,
  MessageSquare,
  Moon,
  Play,
  Plus,
  Settings2,
  Sun,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useAppearance } from "./appearance";
import { Conversation } from "./conversation";
import { ResearchCanvas } from "./research-canvas";
import {
  Commons,
  Experiments,
  Holarchy,
  Operations,
} from "./research-views";
import { SettingsPanel } from "./settings-panel";
import { Markdown, Modal } from "./ui";

type Inspection = "direction" | "knowledge" | "researchers" | "experiments" | "activity";

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [data, setData] = useState<ProjectData>(emptyData);
  const [converseOpen, setConverseOpen] = useState(true);
  const [reference, setReference] = useState<ConversationReference | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const converseToggle = useRef<HTMLButtonElement>(null);
  const [globalSettings, setGlobalSettings] =
    useState<ResearchSettings>(defaults);
  const [loading, setLoading] = useState(true);
  const [online, setOnline] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [create, setCreate] = useState(false);
  const [settings, setSettings] = useState<"global" | "project" | null>(null);
  const [settingsSection, setSettingsSection] = useState<"general" | "models">(
    "general",
  );
  const [sidebar, setSidebar] = useState(false);
  const [archive, setArchive] = useState(false);
  const [theme, setTheme] = useAppearance();
  const selected = projects.find((item) => item.id === selectedId) || null;
  const selectedRef = useRef(selectedId);
  const eventCursor = useRef("0");
  selectedRef.current = selectedId;
  const loadProjects = useCallback(async () => {
    const list = await api<Project[]>("/projects");
    setProjects(list);
    setOnline(true);
    return list;
  }, []);
  const refreshData = useCallback(async (id: string, includeEvents = false) => {
    const paths = [
      "messages",
      "tree",
      "holarchy",
      "claims",
      "evidence",
      "experiments",
      "attention",
      "stats",
      "artifacts",
      ...(includeEvents ? ["events" as const] : []),
    ] as const;
    const results = await Promise.allSettled(
      paths.map((path) =>
        path === "events" ? loadEvents(id) : api(`/projects/${id}/${path}`),
      ),
    );
    if (selectedRef.current !== id) return;
    const update: Partial<ProjectData> = {};
    let failure = "";
    results.forEach((result, index) => {
      if (result.status === "fulfilled")
        Object.assign(update, { [paths[index]]: result.value });
      else failure = errorText(result.reason);
    });
    setData((current) => ({ ...current, ...update }));
    if (includeEvents && Array.isArray(update.events) && update.events.length) {
      eventCursor.current = update.events.at(-1)?.id || "0";
    }
    if (failure) setError(failure);
    return update;
  }, []);
  const refresh = useCallback(() => {
    void loadProjects().catch(() => setOnline(false));
    if (selectedRef.current) void refreshData(selectedRef.current);
  }, [loadProjects, refreshData]);
  useEffect(() => {
    let active = true;
    void Promise.all([loadProjects(), api<ResearchSettings>("/settings")])
      .then(([list, prefs]) => {
        if (!active) return;
        setGlobalSettings({ ...defaults, ...prefs });
        const saved = window.localStorage.getItem("sapling-project");
        if (list.some((item) => item.id === saved)) setSelectedId(saved);
      })
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoading(false));
    return () => {
      active = false;
    };
  }, [loadProjects]);
  useEffect(() => {
    setData(emptyData);
    setError("");
    if (!selectedId) return;
    window.localStorage.setItem("sapling-project", selectedId);
    let source: EventSource | undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let eventPoll: ReturnType<typeof setInterval> | undefined;
    eventCursor.current = "0";
    const mergeEventBatch = (batch: ResearchEvent[]) => {
      if (cancelled || selectedRef.current !== selectedId || !batch.length) return;
      const currentCursor = Number(eventCursor.current) || 0;
      const unseen = batch.filter((event) => (Number(event.id) || 0) > currentCursor);
      if (!unseen.length) return;
      const newest = Math.max(
        currentCursor,
        ...unseen.map((event) => Number(event.id) || 0),
      );
      eventCursor.current = String(newest);
      setData((current) => ({
        ...current,
        events: unseen.reduce(mergeResearchEvent, current.events),
      }));
    };
    const pollEvents = async () => {
      const after = eventCursor.current;
      const batch = await api<ResearchEvent[]>(
        `/projects/${selectedId}/events?after=${after}`,
      );
      mergeEventBatch(batch);
    };
    void refreshData(selectedId, true).then(() => {
      if (cancelled) return;
      const liveOrigin = process.env.NEXT_PUBLIC_SAPLING_API_URL ||
        `${window.location.protocol}//${window.location.hostname}:8000`;
      source = new EventSource(
        `${liveOrigin}/projects/${selectedId}/events/stream?after=${eventCursor.current}`,
      );
      source.onopen = () => setConnected(true);
      source.onerror = () => setConnected(false);
      source.addEventListener("research", (message) => {
        const event = JSON.parse((message as MessageEvent).data) as ResearchEvent;
        mergeEventBatch([event]);
        if (!timer)
          timer = setTimeout(() => {
            timer = undefined;
            refresh();
          }, 150);
      });
      // Some development proxies buffer EventSource bodies. A small cursor-based
      // poll keeps live model text and token counters moving in that case while
      // remaining nearly free when SSE is delivering normally.
      eventPoll = setInterval(() => {
        void pollEvents().catch(() => undefined);
      }, 750);
    }).catch((err) => setError(errorText(err)));
    const poll = setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      source?.close();
      clearInterval(poll);
      if (eventPoll) clearInterval(eventPoll);
      if (timer) clearTimeout(timer);
      setConnected(false);
    };
  }, [selectedId, refresh, refreshData]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 3500);
    return () => clearTimeout(timer);
  }, [notice]);
  function openSettings(
    scope: "global" | "project",
    section: "general" | "models" = "general",
  ) {
    setSettingsSection(section);
    setSettings(scope);
  }
  function select(id: string) {
    setSelectedId(id);
    setConverseOpen(true);
    setReference(null);
    setInspection(null);
    setSidebar(false);
  }
  async function control() {
    if (!selected) return;
    try {
      await api(
        `/projects/${selected.id}/${selected.research_state === "running" ? "pause" : "resume"}`,
        { method: "POST" },
      );
      refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }
  function discuss(next?: ConversationReference) {
    if (next) setReference(next);
    setConverseOpen(true);
  }
  const researchState = selected?.research_state || "planning";
  return (
    <div className="workspace-app">
      <aside className={`app-sidebar ${sidebar ? "open" : ""}`}>
        <div className="sidebar-top">
          <button
            className="brand-word"
            onClick={() => {
              setSelectedId(null);
              setSidebar(false);
            }}
            aria-label="Sapling home"
          >
            sapling<span>•</span>
          </button>
          {sidebar && (
            <button
              className="icon-button"
              aria-label="Close sidebar"
              onClick={() => setSidebar(false)}
            >
              <X size={16} />
            </button>
          )}
        </div>
        <button
          className="sidebar-new"
          onClick={() => setCreate(true)}
          disabled={!online}
        >
          <Plus size={15} />
          New project
        </button>
        <span className="sidebar-label">Projects</span>
        <nav className="sidebar-projects" aria-label="Projects">
          {projects.map((item) => (
            <button
              key={item.id}
              onClick={() => select(item.id)}
              className={selectedId === item.id ? "selected" : ""}
            >
              <Folder size={14} />
              <span>{item.title}</span>
            </button>
          ))}
        </nav>
        <footer className="sidebar-footer">
          <button onClick={() => openSettings("global")}>
            <Settings2 size={15} />
            Settings
          </button>
          <span title={online ? "Runtime connected" : "Runtime offline"}>
            {online ? "Local" : "Offline"}
          </span>
        </footer>
      </aside>
      <main className="app-content">
        <header className="app-topbar">
          <button
            className="icon-button mobile-sidebar-toggle"
            onClick={() => setSidebar(true)}
            aria-label="Open sidebar"
          >
            <Menu size={18} />
          </button>
          <span className="project-name">{selected?.title || "Workspace"}</span>
          {selected && <><span className={`research-mode ${researchState}`}><i />{researchState === "running" ? "Autoresearch" : researchState === "paused" ? "Autoresearch paused" : "Planning together"}</span>{researchState !== "planning" && <button className="campaign-control icon-button" aria-label={researchState === "running" ? "Pause autoresearch" : "Resume autoresearch"} title={researchState === "running" ? "Pause autoresearch · keep conversing" : "Resume autoresearch"} onClick={() => void control()}>{researchState === "running" ? <CirclePause size={16} /> : <Play size={15} />}</button>}</>}
          <div className="topbar-actions">
            {selected && <><span className="workspace-budget" title="Model usage / project budget">{money(selected.budget_spent)} <span>/ {money(selected.budget_total)}</span></span><div className="workspace-menu"><button className="icon-button" aria-label="Project resources" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><ChevronDown size={16} /></button>{menuOpen && <div className="workspace-menu-items">{(["direction", "knowledge", "researchers", "experiments", "activity"] as const).map(item => <button key={item} onClick={() => { setInspection(item); setMenuOpen(false); }}>{item === "direction" ? "Research direction" : item === "knowledge" ? "Shared knowledge" : item[0].toUpperCase() + item.slice(1)}</button>)}</div>}</div></>}
            <button
              className="icon-button theme-toggle"
              title="Toggle dark mode"
              aria-label="Toggle dark mode"
              onClick={() =>
                setTheme(
                  theme === "graphite" || theme === "observatory"
                    ? "studio"
                    : "graphite",
                )
              }
            >
              {theme === "graphite" || theme === "observatory" ? (
                <Sun size={15} />
              ) : (
                <Moon size={15} />
              )}
            </button>
            {selected && (
              <button
                className="icon-button"
                aria-label="Project settings"
                onClick={() => openSettings("project")}
              >
                <Settings2 size={16} />
              </button>
            )}
            {selected && <button ref={converseToggle} className={`converse-toggle ${converseOpen ? "active" : ""}`} aria-label="Toggle Converse" aria-expanded={converseOpen} aria-controls="converse-drawer" onClick={() => setConverseOpen(!converseOpen)}><MessageSquare size={15} /><span>Converse</span></button>}
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button
              className="icon-button"
              onClick={() => setError("")}
              aria-label="Dismiss error"
            >
              <X size={15} />
            </button>
          </div>
        )}
        {loading ? (
          <div className="loading-page">
            <Loader2 size={22} className="spinning" />
            Opening workspace…
          </div>
        ) : !selected ? (
          <section className="workspace-home">
            <span className="eyebrow">Sapling / Your research partner</span>
            <h1>Room for a good question.</h1>
            <p>
              Think out loud, explore the literature, and follow an idea
              together.
            </p>
            <button
              className="button primary"
              disabled={!online}
              onClick={() => setCreate(true)}
            >
              <Plus size={15} />
              New project
            </button>
            <div className="home-projects">
              {projects.map((item) => (
                <button key={item.id} onClick={() => select(item.id)}>
                  <Folder size={17} />
                  <span>{item.title}</span>
                  <small>{date(item.created_at)}</small>
                  <ArrowRight size={15} />
                </button>
              ))}
            </div>
            <div className="home-footnote">
              Saved locally. Models and search use external services.
            </div>
          </section>
        ) : (
          <div className={`canopy-workspace ${converseOpen ? "converse-open" : ""}`}>
            <ResearchCanvas key={selected.id} project={selected} data={data} onDiscuss={discuss} onRefresh={refresh} onError={setError} onKnowledge={() => setInspection("knowledge")} />
            <aside id="converse-drawer" className="converse-drawer" aria-label="Converse" inert={!converseOpen} aria-hidden={!converseOpen}>
              <header className="converse-heading"><div><MessageSquare size={15} /><strong>Converse</strong><span>Your research partner</span></div><button className="icon-button" aria-label="Close Converse" onClick={() => { setConverseOpen(false); converseToggle.current?.focus(); }}><X size={16} /></button></header>
              <Conversation key={selected.id} project={selected} data={data} connected={connected} onRefresh={refresh} onSettings={() => openSettings("project", "models")} onError={setError} reference={reference} onClearReference={() => setReference(null)} visible={converseOpen} />
            </aside>
          </div>
        )}
      </main>
      {inspection && selected && <Modal title={inspection === "direction" ? "Research direction" : inspection === "knowledge" ? "Shared knowledge" : inspection[0].toUpperCase() + inspection.slice(1)} onClose={() => setInspection(null)} wide><div className="resource-inspection">
        {inspection === "direction" && <ResearchBrief project={selected} data={data} />}
        {inspection === "knowledge" && <Commons claims={data.claims} evidence={data.evidence} artifacts={data.artifacts} />}
        {inspection === "researchers" && <Holarchy records={data.holarchy} onChange={refresh} onError={setError} />}
        {inspection === "experiments" && <Experiments records={data.experiments} />}
        {inspection === "activity" && <Operations project={selected} data={data} onDelete={() => { setInspection(null); setArchive(true); }} />}
      </div></Modal>}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {create && (
        <NewProject
          onClose={() => setCreate(false)}
          onCreated={async (project) => {
            await loadProjects();
            setCreate(false);
            select(project.id);
          }}
        />
      )}
      {settings && (
        <SettingsPanel
          project={settings === "project" ? selected : null}
          globalSettings={globalSettings}
          onClose={() => setSettings(null)}
          onDelete={() => {
            setSettings(null);
            setArchive(true);
          }}
          onSaved={() => {
            refresh();
            void api<ResearchSettings>("/settings").then(setGlobalSettings);
          }}
          initialSection={settingsSection}
          theme={theme}
          onTheme={setTheme}
        />
      )}
      {archive && selected && (
        <Modal
          title={`Delete “${selected.title}”?`}
          onClose={() => setArchive(false)}
        >
          <div className="modal-content">
            <p>
              This stops its researchers and permanently removes its
              conversation and research records. Cached files and local
              experiment folders remain on disk.
            </p>
            <div className="modal-footer">
              <button
                className="button secondary"
                onClick={() => setArchive(false)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                onClick={() =>
                  void api(`/projects/${selected.id}?permanent=true`, {
                    method: "DELETE",
                  })
                    .then(() => {
                      setArchive(false);
                      setSelectedId(null);
                      refresh();
                    })
                    .catch((err) => setError(errorText(err)))
                }
              >
                Delete project
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function NewProject({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (project: Project) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ title: title.trim() }),
      });
      await onCreated(project);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="New project" onClose={onClose}>
      <form className="modal-content" onSubmit={create}>
        <label>
          Title
          <input
            autoFocus
            required
            maxLength={200}
            aria-label="Project title"
            placeholder="A name for your workspace"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-footer">
          <button type="button" className="button secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" disabled={busy || !title.trim()}>
            {busy ? "Creating…" : "Create project"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResearchBrief({
  project,
  data,
}: {
  project: Project;
  data: ProjectData;
}) {
  const revisions = data.events.filter(
    (event) => event.type === "RESEARCH_DIRECTION_UPDATED",
  );
  return (
    <section className="brief">
      <span className="eyebrow">Shared understanding</span>
      <h2>Where we’re going.</h2>
      <p>
        A living brief, developed through your conversation. Change direction
        simply by talking about it.
      </p>
      <div className="brief-current">
        {project.goal && revisions.length ? (
          <Markdown>{project.goal}</Markdown>
        ) : (
          <p>
            The direction will take shape as you talk. There’s nothing to fill
            out here.
          </p>
        )}
      </div>
      {revisions.length > 0 && (
        <div className="brief-history">
          <h3>How it evolved</h3>
          {[...revisions].reverse().map((event) => (
            <details key={event.id}>
              <summary>{date(event.created_at)}</summary>
              <Markdown>{String(event.payload.direction || "")}</Markdown>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
