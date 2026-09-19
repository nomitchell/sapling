# Sapling product decisions

Confirmed with Noah, 2026-09-19.

- Build the general research product described in the full technical plan. There is no seeded research task, prescribed demo, or deadline-driven reduction in scope.
- Windows, single user, local application and local execution. Remote execution, SSH and Modal are deferred until requested.
- Research state is canonical in the database. Model sessions are replaceable context projections.
- Keep the holarchy, research tree and claim/evidence commons distinct.
- Preserve the original model-guided research allocation policy: prospective value, exploration, cost, progressive widening, and evidence-driven reassessment. MCTS is not a required replacement.
- Every researcher uses the same general loop and can recursively form a group. Preserve independent interpretations and grounded evidence sharing; do not add a fixed specialist pipeline or separate consensus/memory system.
- Account for usage in dollars initially. Preserve raw token and execution usage as well.
- Permission modes: ask by category, approve routine work and ask for risky actions, and explicit unrestricted mode. Cadence controls scientific consultation independently of execution permissions.
- Support papers, general web search, imported documents and private sources supplied by the user. Prefer free discovery services.
- Use placeholders in checked-in configuration. The user supplied an OpenAI key for live testing; save credentials in the OS vault, never source control. Never fabricate a live research response when a key is absent.
- Use an inexpensive default for testing, with user-selectable model and reasoning effort. Current default: GPT-5.4 nano, low reasoning, with reviewed editable pricing.
- Save branch-prioritization decisions, human overrides and outcomes. Defer model training.
- GitHub repository and tracking: https://github.com/nomitchell/sapling.
- Graphite (charcoal and mint) is the chosen default; Fieldnotes, Observatory and Studio remain selectable appearance options.
- Create projects by title only. The title is excluded from model context. Research direction develops through conversation into a model-maintained brief with recorded revisions.
- The target workspace is a persistent scientific research tree with Converse sliding from the right, replacing the earlier Converse/Research/Activity top-level tabs. Keep the application viewport fixed and scroll individual content regions.
- Continuous pair research starts after the root asks whether to begin and the user agrees. Pausing autoresearch leaves conversation and bounded, potentially parallel planning/literature work available. Such work cannot silently resume the campaign.
- Scientific questions and replies share one conversational input. Node details provide Copy ID and Discuss in Converse. Execution approvals remain explicit inline controls. Conversational Stop targets the current conversational request; project/subtree pause and termination have distinct scopes.
- Nonblocking check-me attention stays highlighted until read while research continues. Blocking attention pauses the affected branch until resolved through chat; reading alone does not unblock it, and other branches continue when unaffected.
- Shared understanding uses the existing claim/evidence commons and bounded context builder. The visible brief and summaries project existing goal/guidance/knowledge records; they do not introduce a second authoritative memory store.
- Baseten open-source models are an additional provider target using the same structured research decision contract. Keep a single selected research model per project and expose only reasoning controls supported by that model.
- Render Markdown, tables, task lists, code and equations directly in chat. Show actual execution stages and tool queries; do not invent or expose private chain-of-thought text.

Implementation defaults exposed in settings: OpenAI provider, selectable presets/custom model identifier and reasoning effort, local artifact storage, Docker execution (local processes in native mode), and project-scoped permission grants. These defaults can be changed without changing the research architecture.

These decisions include agreed work that is not yet implemented. See [implementation-plan.md](implementation-plan.md) for the build sequence and [status.md](status.md) for shipped and verified behavior.

## Integration references

- OpenAlex API: https://help.openalex.org/api/ — basic queries without a key; a free key increases allowance. Search quotas must be surfaced rather than represented as unlimited.
- SearXNG JSON API: https://docs.searxng.org/dev/search_api.html — self-hosted general search; enable JSON explicitly. Availability depends on upstream engines.
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Permission model reference: https://developers.openai.com/codex/security/
- Current model catalog: https://developers.openai.com/api/docs/models
- Interaction references: https://cursor.com/docs/agent/security/run-modes and https://code.claude.com/docs/en/interactive-mode
- Markdown and math rendering: https://github.com/remarkjs/react-markdown and https://github.com/remarkjs/remark-math
