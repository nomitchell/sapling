# Implementation status

This file distinguishes shipped behavior, validation, and remaining integration work. It is not a claim of research quality.

## Implemented

- Local application launcher, Windows native mode, PostgreSQL/pgvector + SearXNG Compose configuration.
- Empty, persistent project workspace and root conversation with server-sent updates.
- Recursive coordinators, bounded structured decisions, deterministic frontier allocation and progressive widening.
- Budgets/reservations, delegation/refunds, stale-result fencing, durable leases and attention on interrupted jobs.
- Separate holarchy/tree/commons, immutable evidence/artifacts/inputs/events, claims and visibility rules.
- Impact-assessed evidence routing with lexical vector candidate retrieval.
- Permission modes, exact-action approval, per-project category grants, local and Docker execution adapters.
- Hash-verified source/experiment artifacts and Windows descendant-process cleanup.
- OpenAlex paper search, SearXNG web search, DDGS native fallback, public source capture, private file uploads.
- Root chat, tree/holarchy inspection and controls, commons, experiments, attention, Ops, settings, credential vault and decision export.
- Decision snapshots, explicit human node preferences and linked attention overrides; no training.
- Account-aware model presets, custom model IDs, compatible reasoning choices and editable token prices. Default: GPT-5.4 nano, low reasoning. Keys use the OS credential vault.

## Verified in this workspace

- 82 backend API, runtime, worker and adapter tests passed against SQLite and actual Windows subprocesses (2026-09-19).
- Frontend TypeScript and production build; dependency audit with no known issues at build time.
- Browser project creation, root message persistence, live event updates, missing-credential attention and tree inspection.
- Live keyless OpenAlex and DDGS searches.
- Live OpenAI coordinator connection using GPT-5.4 nano with low reasoning: 4,353 input tokens, 222 output tokens, $0.0011481 usage. It returned a valid decision and completed the isolated check. This validates the integration, not scientific research quality.

## Integration still requiring external readiness

- Sustained scientific research behavior needs evaluation on real research projects; the live connection check did not evaluate scientific quality.
- PostgreSQL/pgvector and Docker experiments: configuration/adapters are present, but Docker Desktop on this machine exits because its stale userAnalyticsOtlpHttp.sock IPC file cannot be accessed. Native mode remains usable. No machine-wide workaround or Docker reset was performed.
- SearXNG live checks depend on Docker startup; the live DDGS fallback works.

## Deliberate scope and current limitations

- Remote/SSH/Modal execution, training, and multi-user access are deferred by user instruction.
- Candidate vectors are lexical hashes, not semantic model embeddings.
- Experiments use independent Git repositories and snapshots, not shared worktree lineage. Evaluation metadata is retained, but no trusted hidden evaluator service is implemented yet. Self-reported metrics are marked untrusted.
- Private sources currently enter through uploads; there are no authenticated private-source connectors.
- PostgreSQL persistence has an initial schema version and per-domain JSON records. Future schema changes need explicit migrations.
- Model validation and tests cannot guarantee scientific correctness, causal validity or reproducibility of arbitrary researcher-generated experiments.
