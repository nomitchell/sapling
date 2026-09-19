# Sapling product decisions

Confirmed with Noah, 2026-09-19.

- Build the general research product described in the full technical plan. There is no seeded research task, prescribed demo, or deadline-driven reduction in scope.
- Windows, single user, local application and local execution. Remote execution, SSH and Modal are deferred until requested.
- Research state is canonical in the database. Model sessions are replaceable context projections.
- Keep the holarchy, research tree and claim/evidence commons distinct.
- Account for usage in dollars initially. Preserve raw token and execution usage as well.
- Permission modes: ask by category, approve routine work and ask for risky actions, and explicit unrestricted mode. Cadence controls scientific consultation independently of execution permissions.
- Support papers, general web search, imported documents and private sources supplied by the user. Prefer free discovery services.
- Use placeholders in checked-in configuration. The user supplied an OpenAI key for live testing; save credentials in the OS vault, never source control. Never fabricate a live research response when a key is absent.
- Use an inexpensive default for testing, with user-selectable model and reasoning effort. Current default: GPT-5.4 nano, low reasoning, with reviewed editable pricing.
- Save branch-prioritization decisions, human overrides and outcomes. Defer model training.
- GitHub repository and tracking: https://github.com/nomitchell/sapling.
- Graphite (charcoal and mint) is the chosen default; Fieldnotes, Observatory and Studio remain selectable appearance options.
- Create projects by title only. The title is excluded from model context. Research direction develops through conversation into a model-maintained brief with recorded revisions.
- Primary navigation is Converse, Research and Activity. Keep the application viewport fixed and scroll individual content regions.
- Scientific questions and replies share one conversational input. Execution approvals remain explicit inline controls. Stop targets the root's active work; steering invalidates its obsolete decisions without cancelling independent researchers.
- Render Markdown, tables, task lists, code and equations directly in chat. Show actual execution stages and tool queries; do not invent or expose private chain-of-thought text.

Implementation defaults exposed in settings: OpenAI provider, selectable presets/custom model identifier and reasoning effort, local artifact storage, Docker execution (local processes in native mode), and project-scoped permission grants. These defaults can be changed without changing the research architecture.

## Integration references

- OpenAlex API: https://help.openalex.org/api/ — basic queries without a key; a free key increases allowance. Search quotas must be surfaced rather than represented as unlimited.
- SearXNG JSON API: https://docs.searxng.org/dev/search_api.html — self-hosted general search; enable JSON explicitly. Availability depends on upstream engines.
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Permission model reference: https://developers.openai.com/codex/security/
- Current model catalog: https://developers.openai.com/api/docs/models
- Interaction references: https://cursor.com/docs/agent/security/run-modes and https://code.claude.com/docs/en/interactive-mode
- Markdown and math rendering: https://github.com/remarkjs/react-markdown and https://github.com/remarkjs/remark-math
