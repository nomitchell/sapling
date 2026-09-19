# Sapling

A local research workspace with a conversational root coordinator, recursively delegated researchers, a branching research tree, and an evidence commons.

Sapling starts empty. Bring your own research objective; there is no scripted campaign or simulated research output. Without a real model key, projects, documents, history, settings and inspection work, while research waits on a configuration attention item.

## Run on Windows

Requires Python 3.12+, Node.js 22+, and Git. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Copy-Item .env.example .env
cd web
npm ci
npm run build
cd ..
.\.venv\Scripts\sapling.exe --native
```

This launches the runtime and browser interface at **http://127.0.0.1:3000**. The API listens on loopback port 8000. `--native` uses a local SQLite database and local process execution, with explicit permission requests in balanced mode. Native processes run with your operating-system rights; this is not an OS sandbox.

For PostgreSQL + pgvector, SearXNG, and container experiments, start Docker Desktop and run:

```powershell
docker pull python:3.12-slim
.\.venv\Scripts\sapling.exe
```

The launcher starts the Compose database/search services. Their ports are bound to loopback. Database files live in the `sapling_research-db` Docker volume; content-addressed artifacts and workspaces live under `.sapling/`. Native storage is `.sapling/native.sqlite3`. The two modes have separate databases; switching modes does not migrate projects.

Use `--dev` for Next.js development, `--no-browser` to avoid opening a browser, `--no-services` for an existing PostgreSQL/search installation, or `--api-only` to run without the frontend. The launcher expects this source checkout (including `web/`) to remain in place.

## Connect and use

1. Open **Settings → Connections**. Enter an OpenAI key; Sapling stores it in the operating-system credential vault. The checked-in environment example contains a placeholder. Real keys are never returned by the API or stored in project records.
2. Choose a model and reasoning effort. The default is **GPT-5.4 nano with low reasoning**. Reviewed presets include editable input, cached-input and output prices; account availability is checked when credentials are configured. Custom model IDs are supported and require explicit prices. Project settings copy defaults when created; changing global defaults does not rewrite existing projects.
3. Create a project with a title only. Titles are excluded from model context. Start talking in **Converse**; a greeting or exploratory idea is sufficient. Project settings contain budget, permission and model controls.
4. **Research** contains Direction, Tree, Knowledge, Researchers and Experiments. The living direction is maintained from conversation and has a visible revision history. **Activity** contains events and decision export. Attach PDFs or text documents as private sources.
5. Reply to scientific questions in the same chat. Approve or deny permissions explicitly in inline action cards. Thinking, tool queries and elapsed time appear above the composer. Sending while busy steers the next step; **Stop** or Esc cancels the conversational researcher, leaving background researchers running. Project-wide pause remains in Research.

Graphite is the default dark appearance. Settings offers three alternatives, including two light themes; `/designs` contains visual comparisons. Chat supports Markdown headings, lists, tables, task lists, source links, code copying and KaTeX equations. The application stays within the viewport while chat and research panels scroll internally.

Output limits include reasoning tokens. The default allowance is 8,192 tokens; existing projects keep their configured limit. If a response exhausts it, lower reasoning or increase the limit in Research settings. Known usage is retained for failed and obsolete responses. A malformed structured decision gets one bounded repair attempt; no invalid actions are executed. Cancellation with unknown provider usage conservatively charges the request's calculated reservation, marked as estimated in Activity.

Research content is stored locally. Model context is sent to the configured API provider, and search queries/source requests use external services. Uploaded private documents can enter model context when relevant. A local application is not an offline model.

## Permissions and cadence

Execution permission and scientific cadence are separate.

| Mode | Behavior |
| --- | --- |
| Ask by category | Each ungranted capability needs approval. Grants can be remembered for the project. |
| Balanced | Workspace operations, public-source retrieval, search and bounded offline containers can proceed. Host execution, external files and container network access require approval. |
| Full autonomy | All recognized capabilities are allowed within that project. Host code still has the user's OS rights. |

Approval records include the exact work order and requested capability categories. An approval cannot be reused for a different action or another project. Container experiments have CPU/memory/process/time limits, a read-only image, a writable experiment directory, and no network by default. Git metadata and captured logs live outside the directory mounted into experiments. Provider credentials are not inherited by experiment processes. GPU access can be requested as an experiment argument when the local Docker/GPU setup supports it.

Collaboration cadence has four choices: Collaborative, Balanced, Independent, and Autonomous. Lower cadence asks for more scientific input; higher cadence permits default scientific decisions. Cadence never overrides execution permissions or dollar reservations.

## Research runtime

- Holons, research nodes, and claims/evidence are separate persistent structures.
- Every coordinator returns a validated structured decision. Code enforces ownership, finite budgets, maximum depth, progressive widening and branch selection.
- Prospective branch value is model-assessed. Exploration and estimated cost determine allocation priorities. New evidence invalidates old assessments.
- Children receive budget transfers and return unused allocations. Model calls reserve budget before dispatch; known usage is settled even when a result becomes stale. An ambiguous provider failure conservatively consumes its reservation and produces attention rather than silently retrying a potentially paid call.
- Evidence, source artifacts, human inputs and events are append-only. Artifact downloads verify SHA-256. Claims evolve through recorded events and evidence links.
- Empirical evidence is globally retrievable within the project. Interpretations use local/subtree/campaign visibility; independence groups limit hypothesis sharing until independent results are ready.
- Evidence routing first retrieves candidates, then asks the project model to assess impact. Current candidate vectors use deterministic lexical token hashing: pgvector cosine distance on PostgreSQL, the same vectors locally on SQLite. These are **not semantic neural embeddings**. The retrieval boundary can accept a semantic embedder later.
- Jobs are persisted, leased and heartbeated. Interrupted jobs require inspection instead of automatically replaying arbitrary code or an ambiguously billed request. One active job per holon and per-project concurrency caps are enforced; the local scheduler has a global ceiling of 32 jobs.
- Context is a bounded projection of stored goals, summaries, frontier, children, evidence, claims, messages and budget. Model session IDs are recorded for provenance; every turn rebuilds from canonical state.

Local code experiments materialize source in isolated directories, record Git snapshots, execute bounded commands, and retain source, logs, exit state, environment, hashes and result artifacts. Imported source directories are copied with secrets/common dependency directories excluded and require the external-files capability. Each experiment has its own Git repository; this version does not yet maintain a shared experiment worktree lineage across repeated runs. Model-produced metrics are explicitly marked untrusted: no task-specific hidden benchmark evaluator is bundled.

## Sources

- [OpenAlex](https://help.openalex.org/api/): paper discovery, author/year/DOI/open-access metadata, and resolution to open full text. When a key is configured, paper reading prefers OpenAlex's cached machine-readable full text, then falls back to the best open-access host.
- [SearXNG](https://docs.searxng.org/dev/search_api.html): preferred self-hosted general web search. Compose enables its JSON API.
- [DDGS](https://github.com/deedy5/ddgs): no-key native fallback when SearXNG is unavailable. Results retain the actual provider and fallback reason. Upstream availability and rate limits vary.
- Public HTML/PDF/text source opening saves original bytes, extracted text and retrieval provenance. Private/local sources can be uploaded explicitly. Authenticated private-service connectors are not yet included.

Private-network URLs, credential-bearing URLs, unsupported schemes, excessive downloads and redirect chains are rejected. Source URL validation is not a firewall against an attacker controlling DNS; use container/firewall isolation where needed.

## Decision data

The Ops view exports JSONL snapshots containing bounded research state, candidate actions, model ranking, allocation ranking, human overrides and eventual results. Explicit node preferences and responses to linked attention items become human input records. Data collection is implemented; training, fine-tuning and distillation are intentionally deferred, as are SSH/remote execution and multi-user access.

## Development and checks

```powershell
# Use a new test directory on Windows if the shared temp directory has stale ACLs.
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-run-local
.\.venv\Scripts\python.exe -m ruff check --isolated --select E9,F63,F7,F82 src tests
cd web
npm run typecheck
npm run build
npm audit
```

Tests exercise actual SQLite transactions, API requests, structured model substitutes, permission replay, recursive budget conservation, stale decisions, independent hypotheses, evidence routing, subprocess execution, timeouts and process-tree cleanup. They do not spend API credits. PostgreSQL and Docker integration require a healthy Docker Desktop. A real OpenAI key is required to validate live coordinator behavior; passing offline tests is not evidence of scientific research quality. For an explicit connection check using an isolated database and a $0.20 project cap, run `python scripts/live_check.py --confirm-live` with the virtual environment active. This spends API credits and does not start a research campaign.

See [the original technical plan](docs/technical-plan.md), [confirmed product decisions](docs/decisions.md), and [implementation status](docs/status.md). Project tracking: [nomitchell/sapling](https://github.com/nomitchell/sapling).
