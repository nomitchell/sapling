# Implementation status

Validated on Windows on 2026-09-19. This records implemented behavior and its practical limits; it is not a claim that arbitrary research output is scientifically correct.

## Implemented

- A title-only local project opens into the Graphite research tree. Converse is the single text surface and slides from the right; the tree, node inspector, shared knowledge, experiments, researcher hierarchy, resources, and settings remain available without a page-level scroll.
- Markdown conversation supports headings, lists, tables, links, code, and math. Model progress and tool calls are kept in a collapsed chronological **Thinking & actions** stream. The composer shows live work, becomes Stop while a conversational request runs, and accepts steering while work is active.
- The root remains conversational during planning. Continuous work begins only after Sapling asks **“Should we start performing pair research?”** and the user agrees in a later message. Campaign pause, conversation Stop, branch controls, and project deletion have separate semantics.
- Recursive researchers retain the original value, exploration, cost, and progressive-widening allocation policy. Independent branches can be delegated recursively; explicit conversational delegation creates persistent campaign researchers with bounded budgets. Temporary conversational researchers are scoped to that request and release active nodes and unused budget when stopped or exhausted.
- Human guidance, pause, resume, terminate, and delegate controls are grounded in the latest message and referenced node IDs. Check-me attention continues work and remains lit until read; blocking attention pauses its subtree until resolved through Converse.
- Shared claims and evidence preserve provenance, support, contradiction, visibility, source URLs, and independent interpretations. FastEmbed provides cached local semantic candidate retrieval with a deterministic lexical fallback.
- Literature and web research use OpenAlex, Tavily when connected, and free fallbacks. Sources are captured as hash-verified artifacts, extracted into passages, reused across researchers, and kept in a persistent citation catalog.
- Conversation investigations are bounded to ten model turns and six tool actions by default, then forced to synthesize available evidence. Repeated searches and reads are visible to the model, response-only campaign loops are stopped, invalid runtime references receive one repair attempt, and campaign turns cannot apply human controls.
- OpenAI and Baseten share one structured-decision boundary, provider-aware model catalog, reasoning controls, usage accounting, and credential vault. Current OpenAI and Baseten presets are selectable; custom identifiers require explicit prices.
- Windows native execution and Docker execution adapters record commands, inputs, code snapshots, outputs, environment details, seeds when provided, parent experiment lineage, evaluation versions, and reported versus independently checked metrics.
- Durable jobs use leases and stale-result fences. Interruptions create scoped attention without replaying an unknown side effect. Orphaned reservations are conservatively charged and reconciled on recovery. Startup now waits for the API before exposing the web app, avoiding transient proxy failures.

## Verified in this workspace

- 143 backend API, runtime, worker, provider, retrieval, lineage, and Windows subprocess tests pass against SQLite.
- Ruff, frontend TypeScript, the optimized Next.js production build, and `git diff --check` pass.
- Live OpenAI conversation with GPT-5.6 Luna and Tavily-backed source discovery completed through the local app. A greeting stayed conversational; a bounded literature request opened a primary paper and produced a linked Markdown synthesis before asking for consent to start pair research.
- Browser testing covered title-only creation, exact consent, pause/resume, conversation while paused, targeted attention resolution, right-hand drawer behavior, tree inspection, node references, Stop/steering behavior, dark mode, settings, provider/model/reasoning controls, connections, and a fixed 390×844 mobile viewport with no document overflow.
- A live steering request created three persistent $0.05 campaign researchers in one conversational turn while keeping the campaign paused. On resume, all three ran independently and lit their respective nodes with attention. The validation campaign was paused afterward.
- Baseten adapter behavior, wire compatibility, model switching, reasoning mapping, and accounting are covered by automated tests. An authenticated Baseten model turn has not been run because no Baseten key is configured.

## Remaining integration limits

- Docker Desktop is unavailable on this machine because its local IPC state cannot be opened. Native Windows execution is verified; Docker and PostgreSQL/pgvector remain configured but have not completed a live integration run here.
- Native processes run with the current Windows account and are not a security boundary. Evaluation lineage distinguishes evaluator versions and untrusted self-reported metrics, but a hostile experiment can only be isolated by a functioning container or later remote execution service.
- SSH/remote compute, multi-user access, and model training remain deferred by product decision.
- Long-running scientific quality still needs benchmark evaluation across real projects. Runtime invariants, citations, provenance, and reproducibility reduce failure modes but cannot establish the validity of an agent-generated scientific conclusion.
