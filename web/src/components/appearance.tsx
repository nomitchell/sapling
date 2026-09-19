"use client";

import {
ArrowUp,
BookOpen,
Check,
GitBranch,
PanelLeft,
Plus,
Search,
Settings2,
Sparkles,
} from "lucide-react";
import { useEffect,useState } from "react";

export const designs = [
  {
    id: "graphite",
    name: "Graphite",
    mode: "Dark",
    accent: "#a9ceba",
    background: "#191b1c",
    description:
      "A quiet editor. Charcoal, mist, and mint. Compact controls and a generous conversation column.",
    font: "sans",
    layout: "Compact sidebar · centered conversation",
  },
  {
    id: "fieldnotes",
    name: "Fieldnotes",
    mode: "Light",
    accent: "#315b46",
    background: "#f7f5ee",
    description:
      "A working notebook. Warm ivory and pine, with an editorial serif for ideas and discoveries.",
    font: "serif",
    layout: "Notebook sidebar · editorial conversation",
  },
  {
    id: "observatory",
    name: "Observatory",
    mode: "Dark",
    accent: "#77cbd8",
    background: "#111b29",
    description:
      "A focused research station. Midnight blue and ice, precise labels, and crisp ruled panels.",
    font: "mono",
    layout: "Instrument sidebar · structured conversation",
  },
  {
    id: "studio",
    name: "Studio",
    mode: "Light",
    accent: "#4167cf",
    background: "#fafbfc",
    description:
      "An open canvas. Cool white, slate, and cobalt. Airy spacing and a softly rounded composer.",
    font: "sans",
    layout: "Airy sidebar · minimal conversation",
  },
] as const;
export type Theme = (typeof designs)[number]["id"];

export function useAppearance() {
  const [theme, setTheme] = useState<Theme>("graphite");
  useEffect(() => {
    const stored = window.localStorage.getItem("sapling-theme");
    if (designs.some((item) => item.id === stored)) setTheme(stored as Theme);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const select = (value: Theme) => {
    setTheme(value);
    window.localStorage.setItem("sapling-theme", value);
  };
  return [theme, select] as const;
}

export function Appearance({
  theme,
  onChange,
}: {
  theme: Theme;
  onChange: (theme: Theme) => void;
}) {
  return (
    <div className="appearance-options">
      {designs.map((design) => (
        <button
          type="button"
          key={design.id}
          className={`theme-option ${theme === design.id ? "selected" : ""}`}
          onClick={() => onChange(design.id)}
        >
          <span
            className="theme-swatch"
            style={{ background: design.background, color: design.accent }}
          >
            <PanelLeft size={26} />
            <span style={{ background: design.accent }} />
          </span>
          <span>
            <strong>{design.name}</strong>
            <small>
              {design.mode} · {design.layout.split(" · ")[0]}
            </small>
          </span>
          {theme === design.id && <Check size={15} />}
        </button>
      ))}
      <a className="text-link" href="/designs" target="_blank">
        Compare full design examples ↗
      </a>
    </div>
  );
}

export function DesignExamples() {
  const [selected, setSelected] = useState<Theme>("graphite");
  const [, setTheme] = useAppearance();
  const design = designs.find((item) => item.id === selected)!;
  return (
    <main className="design-gallery">
      <header>
        <div>
          <span className="eyebrow">Sapling / Design studies</span>
          <h1>Four ways to think together.</h1>
          <p>
            Same research partner. Four different working environments. These
            are visual examples, not a live research session.
          </p>
        </div>
        <a href="/" className="button secondary">
          Back to Sapling
        </a>
      </header>
      <div className="design-choices">
        {designs.map((item) => (
          <button
            key={item.id}
            className={item.id === selected ? "chosen" : ""}
            onClick={() => setSelected(item.id)}
          >
            <span
              style={{ background: item.background, borderColor: item.accent }}
            />
            {item.name}
            <small>{item.mode}</small>
          </button>
        ))}
      </div>
      <div className="design-summary">
        <div>
          <h2>{design.name}</h2>
          <p>{design.description}</p>
        </div>
        <button
          className="button primary"
          onClick={() => {
            setTheme(selected);
            window.location.href = "/";
          }}
        >
          Use this design
        </button>
      </div>
      <section
        className="design-example"
        data-theme={selected}
        aria-label={`${design.name} design preview`}
      >
        <aside className="design-sidebar">
          <strong className="brand-word">
            sapling<span>•</span>
          </strong>
          <button>
            <Plus size={14} /> New project
          </button>
          <span className="sidebar-label">Projects</span>
          <div className="preview-selected">Efficient robustness</div>
          <div className="preview-project">Representation learning</div>
          <div className="preview-project">Reading notes</div>
          <footer>
            <Settings2 size={14} /> Settings
          </footer>
        </aside>
        <div className="design-workspace">
          <header>
            <span>Efficient robustness</span>
            <div>
              <span className="active">Converse</span>
              <span>Research</span>
              <span>Activity</span>
            </div>
            <Settings2 size={15} />
          </header>
          <div className="design-messages">
            <span className="preview-date">A conversation, taking shape</span>
            <article className="preview-user">
              If a diffusion model learns from 50k CIFAR-10 images, can we get
              robustness without generating millions more?
            </article>
            <article className="preview-assistant">
              <span>
                <Sparkles size={16} /> Sapling
              </span>
              <p>
                That’s a useful distinction to explore:{" "}
                <strong>
                  having the information and making it accessible to a learner
                  are different problems.
                </strong>
              </p>
              <p>
                Let’s separate what synthetic data adds from what the training
                procedure makes easier. That gives us a clearer question about
                efficiency.
              </p>
              <div className="preview-plan">
                <span>
                  <Search size={14} /> Literature search
                </span>
                <span>
                  <GitBranch size={14} /> Competing explanations
                </span>
                <span>
                  <BookOpen size={14} /> Shared research brief
                </span>
              </div>
            </article>
          </div>
          <footer className="preview-composer">
            <div>
              <span>Think through a question together…</span>
              <button aria-label="Example send button">
                <ArrowUp size={17} />
              </button>
            </div>
            <small>
              GPT-5.4 nano <span>Low reasoning</span>
            </small>
          </footer>
        </div>
      </section>
      <div className="design-grid">
        {designs.map((item) => (
          <button
            data-theme={item.id}
            onClick={() => setSelected(item.id)}
            key={item.id}
          >
            <div className="mini-window">
              <div />
              <section>
                <span />
                <span />
                <span />
                <i />
              </section>
            </div>
            <h3>
              {item.name} <small>{item.mode}</small>
            </h3>
            <p>{item.description}</p>
          </button>
        ))}
      </div>
    </main>
  );
}
