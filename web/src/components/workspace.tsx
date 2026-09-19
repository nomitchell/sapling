"use client";
import {
  api,
  date,
  defaults,
  emptyData,
  errorText,
  Project,
  ProjectData,
  ResearchSettings,
} from "@/lib/api";
import {
  Activity,
  ArrowRight,
  CirclePause,
  Folder,
  GitBranch,
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
import {
  Commons,
  Experiments,
  Holarchy,
  Operations,
  ResearchTree,
} from "./research-views";
import { SettingsPanel } from "./settings-panel";
import { Markdown, Modal } from "./ui";

type Tab = "converse" | "research" | "activity";
type ResearchTab =
  "brief" | "tree" | "knowledge" | "researchers" | "experiments";
const researchTabs = [
  { id: "brief", label: "Direction" },
  { id: "tree", label: "Tree" },
  { id: "knowledge", label: "Knowledge" },
  { id: "researchers", label: "Researchers" },
  { id: "experiments", label: "Experiments" },
] as const;

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [data, setData] = useState<ProjectData>(emptyData);
  const [tab, setTab] = useState<Tab>("converse");
  const [researchTab, setResearchTab] = useState<ResearchTab>("brief");
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
  selectedRef.current = selectedId;
  const loadProjects = useCallback(async () => {
    const list = await api<Project[]>("/projects");
    setProjects(list);
    setOnline(true);
    return list;
  }, []);
  const refreshData = useCallback(async (id: string) => {
    const paths = [
      "messages",
      "tree",
      "holarchy",
      "claims",
      "evidence",
      "experiments",
      "attention",
      "events",
      "stats",
      "artifacts",
    ] as const;
    const results = await Promise.allSettled(
      paths.map((path) => api(`/projects/${id}/${path}`)),
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
    if (failure) setError(failure);
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
    void refreshData(selectedId);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource(`/api/projects/${selectedId}/events/stream`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("research", () => {
      if (!timer)
        timer = setTimeout(() => {
          timer = undefined;
          refresh();
        }, 150);
    });
    const poll = setInterval(refresh, 5000);
    return () => {
      source.close();
      clearInterval(poll);
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
    setTab("converse");
    setSidebar(false);
  }
  async function control() {
    if (!selected) return;
    try {
      await api(
        `/projects/${selected.id}/${selected.status === "active" ? "pause" : "resume"}`,
        { method: "POST" },
      );
      refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }
  const pending = data.attention.filter(
    (item) =>
      item.status === "pending" &&
      !(
        item.type === "research_decision" &&
        item.holon_id === selected?.root_holon_id
      ),
  );
  const hasResearch =
    data.tree.length > 1 ||
    data.holarchy.length > 1 ||
    data.experiments.length > 0;
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
          {selected && (
            <nav className="main-tabs" aria-label="Project sections">
              {(
                [
                  { id: "converse", name: "Converse", icon: MessageSquare },
                  { id: "research", name: "Research", icon: GitBranch },
                  { id: "activity", name: "Activity", icon: Activity },
                ] as const
              ).map((item) => (
                <button
                  key={item.id}
                  className={tab === item.id ? "active" : ""}
                  onClick={() => setTab(item.id)}
                  aria-current={tab === item.id ? "page" : undefined}
                >
                  <item.icon size={14} />
                  {item.name}
                  {item.id === "activity" && pending.length > 0 && (
                    <span className="tab-badge">{pending.length}</span>
                  )}
                </button>
              ))}
            </nav>
          )}
          <div className="topbar-actions">
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
          <div className="view-shell">
            {tab === "converse" && (
              <Conversation
                key={selected.id}
                project={selected}
                data={data}
                connected={connected}
                onRefresh={refresh}
                onSettings={() => openSettings("project", "models")}
                onError={setError}
              />
            )}
            {tab === "research" && (
              <>
                <nav className="subnav" aria-label="Research sections">
                  {researchTabs.map((item) => (
                    <button
                      key={item.id}
                      className={researchTab === item.id ? "active" : ""}
                      onClick={() => setResearchTab(item.id)}
                    >
                      {item.label}
                    </button>
                  ))}
                  {hasResearch && (
                    <button
                      className="research-toggle"
                      aria-label={
                        selected.status === "active"
                          ? "Pause research"
                          : "Resume research"
                      }
                      onClick={() => void control()}
                    >
                      {selected.status === "active" ? (
                        <CirclePause size={14} />
                      ) : (
                        <Play size={14} />
                      )}
                      <span>
                        {selected.status === "active"
                          ? "Pause research"
                          : "Resume research"}
                      </span>
                    </button>
                  )}
                </nav>
                <div className="view-scroll">
                  {researchTab === "brief" && (
                    <ResearchBrief project={selected} data={data} />
                  )}{" "}
                  {researchTab === "tree" && (
                    <ResearchTree
                      records={data.tree}
                      onChange={refresh}
                      onError={setError}
                      onNotice={setNotice}
                    />
                  )}{" "}
                  {researchTab === "knowledge" && (
                    <Commons
                      claims={data.claims}
                      evidence={data.evidence}
                      artifacts={data.artifacts}
                    />
                  )}{" "}
                  {researchTab === "researchers" && (
                    <Holarchy
                      records={data.holarchy}
                      onChange={refresh}
                      onError={setError}
                    />
                  )}{" "}
                  {researchTab === "experiments" && (
                    <Experiments records={data.experiments} />
                  )}
                </div>
              </>
            )}
            {tab === "activity" && (
              <div className="view-scroll">
                {pending.length > 0 && (
                  <div className="activity-notices">
                    {pending.map((item) => (
                      <div key={item.id}>
                        <p>
                          {String(
                            item.summary || "Review this research update",
                          )}
                        </p>
                        <button onClick={() => setTab("converse")}>
                          {item.type === "permission"
                            ? "Review in conversation"
                            : "Continue in conversation"}{" "}
                          →
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <Operations
                  project={selected}
                  data={data}
                  onDelete={() => setArchive(true)}
                />
              </div>
            )}
          </div>
        )}
      </main>
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
        <Modal title="Archive project?" onClose={() => setArchive(false)}>
          <div className="modal-content">
            <p>Research stops. Your conversation and records remain saved.</p>
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
                  void api(`/projects/${selected.id}`, { method: "DELETE" })
                    .then(() => {
                      setArchive(false);
                      setSelectedId(null);
                      refresh();
                    })
                    .catch((err) => setError(errorText(err)))
                }
              >
                Archive
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
