# Sapling implementation plan

Status: implemented and validated in the local Windows build on 2026-09-19. This document preserves the architectural intent and acceptance criteria; `docs/status.md` records shipped behavior and remaining integration limits.

## Objective

Build a conversational research partner connected to the original recursive autoresearch runtime. The human and root coordinator develop a direction together; continuous research begins on agreement, can be paused, and remains steerable through the same conversation. The Graphite research tree fills the workspace and Converse slides out from the right.

Keep the implementation parsimonious: extend existing state, structured decisions, tools, scheduler, and UI components before introducing a new abstraction.

## Architecture to preserve

- Separate researcher hierarchy, scientific research tree, and claim/evidence commons.
- The same general research loop at every level, including the human-accessible root. Researchers can recursively form groups; there is no fixed pipeline of specialist roles.
- Original allocation policy: model estimates of prospective research value, an exploration bonus, cost-aware prioritization, progressive widening, and reassessment as relevant evidence changes. Do not replace this with MCTS.
- Structured decisions govern execution. Runtime code enforces ownership, permissions, budgets, concurrency, visibility, and cancellation.
- Canonical database records; immutable evidence, artifacts, and human inputs; bounded model contexts with references back to underlying records.
- Shared empirical evidence with scoped interpretations and independence groups. Shared knowledge does not imply forced consensus.
- Hierarchical reporting and selective evidence routing. Idle researchers do not poll the model.
- One project-selected research model and reasoning setting, with provider support extended to Baseten. Dollars remain the primary budget metric; decision/outcome data is retained without training.
- Windows, single user, local execution. Remote execution, SSH, multi-user support, model training, and a dedicated formal-proof subsystem remain outside this implementation.

## Baseline audit that drove the implementation

The repository already implements recursive delegation, allocation and widening, claims/evidence, context construction, job leases, budget reservations, controls, attention, local execution, retrieval, and live conversation. Retain these mechanisms.

The audit identifies the following work:

- The first human message currently activates the project. Conversation must not grant permission for continuous research automatically.
- Ordinary root chat currently resolves all pending non-permission root attention items. Resolution must target the relevant item and must not clear unrelated blockers.
- Scheduling checks project activity in several places. Paused projects currently need a separate way to allow bounded conversational work, enforced through the whole execution path.
- Evidence versions and decision validity are too broadly coupled. Unrelated evidence must not repeatedly invalidate useful work throughout the project.
- Context and research behavior need stronger use of the existing claim/evidence links, possible outcomes, completion, and routing contracts.
- The workspace still uses top-level sections and a separate attention response surface instead of a tree with one conversational input.
- Model settings and runtime originally supported OpenAI only. The completed implementation adds Baseten behind the same structured-decision contract.
- Retrieval originally used lexical vectors and experiments lacked explicit lineage. The completed implementation adds cached local semantic retrieval with lexical fallback, parent snapshots, and versioned evaluation metadata. Native execution remains an untrusted security boundary, as recorded in `docs/status.md`.

## 1. Make the research lifecycle correct

Primary files: `src/sapling/api.py`, `runtime.py`, `worker.py`, `store.py`, `config.py`; lifecycle tests in `tests/test_api.py`, `test_runtime.py`, `test_worker.py`, and `test_conversation.py`.

1. Define continuous-research state separately from whether the root is available to converse. Reuse project records and the existing control-version mechanism.
2. Start with conversation only. When a useful direction exists, the root asks, "Should we start performing pair research?" Record the invitation and the user's agreement before starting continuous allocations. Resolve agreement in conversational context, not by matching the word "yes" anywhere in a message.
3. Carry work purpose through existing job/decision payloads: bounded work for a conversational request or continuous research. Descendants inherit their request scope, budget, and stopping condition. A child cannot expand its authority or silently restart continuous work.
4. Extend the existing execution-eligibility policy and apply it consistently at enqueue, claim, dispatch, active execution, and decision application. Make turn coalescing scope-aware: a conversational wake must not disappear into a paused research job. Child completion, evidence routing, retries, and budget changes must inherit the appropriate scope. Give user-facing conversation priority without starving independent research.
5. Pause prevents new autonomous allocations immediately. Work already executing may reach the end of its current bounded action; expose that state and retain its result without launching follow-up work. Do not imply that arbitrary subprocesses support checkpointing. Explicit terminate cancels the selected work and descendants.
6. Keep conversational Stop, branch pause/terminate, and project pause/resume distinct. Stop cancels the selected conversational request, not all root jobs. Include the work scope and its control version in decision fences so a quick pause/resume cannot admit a pre-pause decision. Steering invalidates obsolete decisions only in the affected scope; independent work remains valid.
7. Resume preserves unresolved attention, remaining budget, and prior evidence. An ordinary chat message cannot resume a paused campaign or clear its blockers accidentally.
8. Add explicit, idempotent migrations/defaults for new fields. Preserve existing projects and histories; migration must never start work or infer consent from legacy activity flags.

Acceptance: a greeting starts no campaign; agreement starts one once; pause stops further autonomous allocations; a parallel literature request still completes while paused; all its descendants stay bounded; a late model result cannot defeat pause; resume does not replay completed actions.

## 2. Unify attention and steering through Converse

Primary files: `src/sapling/api.py`, `runtime.py`, `worker.py`; `web/src/lib/api.ts` and conversation/node components.

1. Extend existing attention records with independent read and resolution state. Reuse existing attention classification and permission enforcement.
2. A check-me update leaves work running and highlights its node until that update is opened/read. A new update makes the node unread again.
3. A blocking item pauses the affected branch/subtree. Reading it does not unblock it. Resolving one item does not bypass other blockers, a paused ancestor, the project pause, or a required execution approval.
4. Include relevant open attention items and selected node references in the root's context. Let structured decisions target specific records and route guidance to their owning researchers, with an auditable reference to the user's input.
5. Extend the existing message contract with explicit node/attention references. Support Copy ID and Discuss in Converse. Resolve pasted identifiers within the current project; clarify ambiguous scope before a consequential action.
6. Move every scientific free-text response to the existing composer. Keep exact-action permission approval controls inline in that conversation; do not treat unrelated conversational agreement as an execution approval.
7. Confirm consequential controls with their actual outcome and scope. The tree reflects durable state, including cancellation still in progress.

Acceptance: reading a check-me clears only its unread indicator; reading a blocker leaves its branch waiting; guidance to one node affects the intended branch; unrelated siblings keep working; a second blocker remains enforced; no attention/node panel has another chat input.

## 3. Complete the existing scientific loop

Primary files: `src/sapling/runtime.py`, `retrieval.py`, `store.py`, `worker.py`, `integrations/search.py`, and `integrations/execution.py`.

### Knowledge and decisions

- Use the existing project goal and recorded guidance as the canonical agreed direction. Derive the visible brief and root summary from that state plus referenced claims; do not add a separate memory or brief database.
- Include relevant support, contradictions, claim scope, and provenance in bounded contexts. Preserve visibility rules when retrieving history, building summaries, and reporting upward.
- Use `possible_outcomes` to connect substantive research proposals to scientific interpretations and next decisions. Do not require a new hypothesis or tree node for every routine read or tool call.
- Following meaningful results, use existing claim updates, value assessments, node updates, and completion decisions to continue, redirect, stop, or request human judgment. A negative result can be useful; model agreement is not an additional empirical observation.
- Reassess affected frontiers through existing evidence routing and references. Keep control/cancellation fencing distinct from scientific evidence freshness. Uncertain relevance can trigger conservative reassessment without cancelling every in-flight turn.
- Verify cost units in the existing priority rule: the current `max(1, estimated_cost)` floor makes sub-dollar costs indistinguishable. Document and test a normalization that preserves the original policy's cost-sensitive intent.
- Keep dollar budget and active concurrency central. Offer automatic research depth rather than requiring a guessed project depth; preserve explicit advanced limits and necessary provider/process bounds. A token cap bounds a model call, not scientific project complexity.

### Retrieval and execution

- Reuse stored source artifacts and extracted passages across researchers. Keep exact citation URLs and artifact references available beyond the most recent tool results. Surface inaccessible sources and discovery-only evidence accurately.
- Add genuine semantic candidate retrieval behind the existing retrieval interface, with cached vectors and the lexical fallback. Use a Windows-compatible local option where practical; benchmark relevance on paraphrased scientific queries before choosing it. Keep the model's existing batched impact assessment and visibility filters.
- Make experiment parent snapshots/artifacts explicit so a branch can refine and reproduce prior work using the current execution backend. Record command, inputs, environment, seeds when provided, code snapshot, outputs, and parent references.
- Provide a small evaluation boundary in the existing execution path: an evaluator and its configuration are versioned separately from the candidate workspace. Agents may write general scientific code, but changing the evaluator must create a distinguishable evaluation version. Preserve reported-versus-independently-checked result labels; native host processes alone do not provide a security boundary against tampering.
- Keep process execution usable on Windows. Docker-specific isolation checks remain explicit integration checks; do not silently claim they passed while Docker is unavailable. No domain-specific proof workflow is required.

Acceptance: a contradictory finding updates a scoped claim, reaches the relevant branch, changes its next allocation, and reaches the root with evidence references. Independent siblings do not receive premature interpretations. An experiment can be reproduced from its recorded inputs, and evaluator changes cannot masquerade as an improvement under the old evaluation.

## 4. Build the Graphite tree workspace

Primary files: `web/src/components/workspace.tsx`, `research-views.tsx`, `conversation.tsx`, `settings-panel.tsx`, `web/src/app/workspace.css`, and `web/src/lib/api.ts`.

1. Replace the main Converse/Research/Activity navigation with a persistent scientific tree and a right-hand sliding Converse drawer. Keep project switching and settings compact.
2. Render meaningful research nodes and ancestry, with collapse/expand, pan/zoom, selection, and live state updates. Preserve the user's view during updates. Show parallel activity and collapsed-subtree summaries; do not conflate the research tree with the researcher hierarchy.
3. Use Graphite colors with readable labels/icons for running, unread check-me, blocked, paused, and completed states. A collapsed branch exposes attention beneath it without requiring a separate inbox.
4. Selecting a node exposes its question, current work, evidence, attention, and Copy ID/Discuss controls. Secondary evidence, artifacts, and researcher details are accessible here instead of permanent top-level tabs.
5. Preserve the existing Markdown conversation, expandable chronological progress/tool activity, live thinking status, steering, and conversational Stop. Preserve drafts, node references, and transcript position when the drawer closes. Make project research state and pause/resume visible without suggesting there is already a campaign on an empty project.
6. Keep the viewport fixed; scroll the conversation and detail content internally. On narrow screens, Converse still slides from the right. Support keyboard operation and focus restoration; color is not the only status signal.
7. Preserve title-only creation, deletion, uploads, history, model/reasoning selection, and compact settings. Remove obsolete navigation and duplicate response controls after replacements work. Confirm and remove the unused older `settings-form.tsx` rather than maintaining a second settings implementation.

Acceptance: the tree remains the main workspace; opening, closing, or resizing Converse preserves context; node discussion uses the same composer; attention is legible on expanded and collapsed branches; long chat and evidence never require scrolling the entire page.

## 5. Add Baseten through the existing model boundary

Primary files: `src/sapling/integrations/model.py`, `worker.py`, `model_catalog.py`, `config.py`, `credentials.py`, `api.py`, and the existing model/settings controls.

- Extract only the shared model-turn interface needed to return the existing structured decision and usage record. Implement Baseten as a second adapter; avoid a general plugin framework.
- Verify the current official endpoints, available open-source model options, structured-output support, reasoning controls, usage reporting, and cancellation behavior during implementation. Show only supported controls for each model.
- Reuse the credential vault, provider-aware catalog, user-selectable model, pricing, budget reservations, retries, and cancellation. Maintain one selected model per project; record the model used for each decision.
- Add adapter contract tests for valid and malformed output, usage, errors, and cancellation. Unsupported features must produce an honest limitation rather than a simulated capability.
- Prepare the complete integration with a placeholder and request a real Baseten key when live verification is ready. Do not expose or overwrite existing credentials.

Acceptance: both adapters yield the same internal decision contract; provider/model switching persists correctly; costs remain accounted for; unsupported reasoning choices are unavailable; Baseten's live status remains unverified until an authenticated run succeeds.

## 6. Verify the complete collaboration and update tracking

- Run focused backend checks with each behavioral change, then the full backend suite and frontend typecheck/production build after integration. Test state transitions and meaningful failure paths rather than duplicating implementation details.
- Use deterministic model/tool fixtures for race conditions: pause during a model turn, rapid pause/resume, completion after steering, multiple blockers, duplicate delivery, restart with mixed-scope queued work, failed tools, and exhausted reservations. Replace tests that currently require blanket attention resolution on chat. Do not depend on an LLM to produce a particular race or action during testing.
- Use the browser plugin for a real local session with the existing credentials and a bounded validation budget: converse, delegate a literature task, agree to pair research, inspect parallel branches, steer by node reference, acknowledge an update, resolve a blocker through chat, pause, continue planning, resume, and return after reload.
- Validate recursive delegation, evidence transfer, independent interpretations, and budget returns as real runtime behavior. Verify literature claims against the stored source passages and inspect experiment outputs; activity alone is not success.
- Use temporary validation projects without modifying the user's existing research history. The product remains general; validation scenarios do not become seeded project content or a prescribed demo.
- Fix issues exposed by these checks before calling the loop complete. Record exact verified behavior and remaining external blockers in `docs/status.md`; keep decisions and this checklist consistent with what ships.

## Delivery order and scope control

Implement steps 1 and 2 first because their contracts govern every surface. Then complete the scientific-loop work in step 3 and build the workspace against those contracts. The provider adapter can proceed independently once the model-turn contract is stable. Finish with integrated verification.

Use small, reviewable changes with acceptance checks at each boundary. Keep the original technical plan as the reference, this file as the execution checklist, and the status document as the evidence of completion. Do not add a separate synthesis service, consensus engine, permanent critic, task-specific agent pipeline, or additional messaging surface.

- [x] 1. Research lifecycle and migrations
- [x] 2. Attention and conversational steering
- [x] 3. Scientific loop, retrieval, and execution integrity
- [x] 4. Graphite tree workspace and right-hand Converse
- [x] 5. Baseten adapter and model controls (authenticated live turn awaits a key)
- [x] 6. Integrated automated and browser verification
