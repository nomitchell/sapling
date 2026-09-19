# Sapling — Full Technical Implementation Plan

Sapling is a **research-grade autonomous research system with a human-accessible root coordinator**. The user talks to one coordinator, but underneath it Sapling can dynamically create researchers, research groups, and recursively nested groups; search many scientific directions in parallel; execute code and experiments; accumulate claims and evidence; move discoveries between branches; and continuously reallocate compute.

The UI is conversational-first, but the core product is the autoresearch runtime.

## System architecture

Sapling runs as a local application composed of a Python research runtime and a browser-based interface.

```text
                    Browser UI
                        │
                  localhost HTTP
                     + SSE
                        │
                        ▼
              SAPLING PYTHON RUNTIME
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
     agents          research         persistence
                   orchestration
        │               │
        │         ┌─────┴─────┐
        │         ▼           ▼
        │      local       remote
        │    execution    execution
        │         │           │
        └─────────┴───────────┘
                  │
                reality
```

Running:

```text
sapling
```

starts the Python backend, workers, required local services, and web frontend, then opens the application in the user's browser.

The initial stack is:

```text
Python 3.12
FastAPI
Pydantic
PostgreSQL + pgvector
Next.js / React / TypeScript
Git
Docker
Server-Sent Events
S3-compatible or local artifact storage
```

For local development, everything runs through Docker Compose except experiments that need direct host access.

Research execution is independent from the web frontend. Sapling can run Python, modify repositories, use GPUs, launch Docker containers, interact with the filesystem, and dispatch remote compute because those operations happen through the local Python runtime.

A later desktop version can wrap the same architecture with Tauri while launching the Python runtime as a sidecar.

---

## Core research representation

Sapling maintains three distinct structures.

### Dynamic holarchy

The holarchy answers:

> Who is responsible for this research?

Every research unit is a `Holon`.

```python
class Holon:
    id: UUID
    project_id: UUID
    parent_id: UUID | None

    goal: str
    summary: str

    coordinator_session_id: str

    assigned_node_id: UUID

    budget_total: float
    budget_remaining: float

    depth: int
    status: HolonStatus
```

A holon with no child holons behaves as an individual researcher.

A holon with children behaves as a research group and coordinates them.

Any holon can dynamically become a group:

```text
Before

H17
│
researcher


After

H17
├── H21
├── H22
└── H23
```

From H17's parent, H17 remains one research unit.

This gives Sapling recursive organizational scaling.

---

### Research tree

The research tree answers:

> Where are we searching?

A `ResearchNode` is one coherent scientific direction.

```python
class ResearchNode:
    id: UUID
    project_id: UUID

    parent_id: UUID | None
    owning_holon_id: UUID

    title: str
    direction: str
    rationale: str
    interpretation: str

    status: NodeStatus

    visits: int
    budget_spent: float

    value_estimate: float | None
    value_confidence: float | None
    evidence_epoch: int
```

A node can be broad:

```text
Investigate alternative optimization mechanisms
```

or extremely concrete:

```text
Hold update-to-weight ratio constant and test whether
the gated-residual improvement disappears at depth 24.
```

The primary structure remains a tree so ancestry, search statistics, context inheritance, and budget accounting stay simple.

Scientific influence across branches is captured with sparse references:

```python
class ResearchReference:
    source_node_id: UUID
    target_node_id: UUID

    relation: Literal[
        "inspired_by",
        "uses_evidence_from",
        "derived_from",
    ]
```

The result is effectively a tree-backed DAG.

---

### Claim and evidence commons

The commons answers:

> What might be true, and what evidence do we actually have?

A claim contains:

```python
class Claim:
    id: UUID
    project_id: UUID

    statement: str
    scope: dict

    origin_type: Literal["agent", "human", "external"]

    origin_holon_id: UUID | None
    origin_node_id: UUID | None

    status: Literal[
        "open",
        "supported",
        "contested",
        "rejected",
    ]
```

A hypothesis is simply an open claim with little evidence.

Evidence contains:

```python
class Evidence:
    id: UUID
    project_id: UUID

    type: Literal[
        "experiment",
        "source",
        "computation",
        "proof_fragment",
        "observation",
    ]

    summary: str
    scope: dict

    producer_holon_id: UUID
    producer_node_id: UUID

    artifact_ids: list[UUID]
```

Claims reference evidence through:

```python
class ClaimEvidence:
    claim_id: UUID
    evidence_id: UUID

    relation: Literal[
        "supports",
        "contradicts",
    ]
```

Evidence remains append-only.

Claims and interpretations can evolve.

Every underlying artifact is separately stored and hashed:

```python
class Artifact:
    id: UUID

    type: str
    uri: str
    sha256: str

    metadata: dict
```

So Sapling can trace:

```text
scientific claim
      ↓
evidence
      ↓
experiment
      ↓
Git commit
      ↓
metrics / logs / artifact
```

---

## The recursive research algorithm

Every holon runs the same basic research loop.

It receives:

```text
its research goal
instructions from its parent
its local research tree
its children
recent local evidence
relevant global evidence
important claims
remaining budget
human guidance if relevant
```

The coordinator then decides what research should happen next.

It can:

```text
work directly
expand an existing research direction
create a new direction
delegate work to a researcher
create a research group
request more budget
publish evidence or claims
communicate with another group
terminate an unproductive direction
```

The coordinator returns structured decisions rather than runtime control embedded in prose.

```python
class HolonDecision:
    updated_summary: str

    branch_proposals: list[BranchProposal]
    work_orders: list[WorkOrder]

    claim_proposals: list[ClaimProposal]
    claim_updates: list[ClaimUpdate]

    child_holon_requests: list[ChildHolonRequest]

    parent_messages: list[HolonMessage]
    peer_channel_requests: list[PeerChannelRequest]

    completion: HolonCompletion | None
```

Each proposed branch includes:

```python
class BranchProposal:
    parent_node_id: UUID

    title: str
    direction: str
    rationale: str

    possible_outcomes: list[PossibleOutcome]

    estimated_cost: float
```

Possible outcomes explicitly connect experiments to future decisions.

```text
Hypothesis:
The improvement is caused by update-scale stabilization.

Experiment:
match update-to-weight ratio across both architectures.

Outcome A:
effect disappears

Meaning:
architectural explanation weakens

Next:
investigate scaling rule directly


Outcome B:
effect persists

Meaning:
update scale is insufficient

Next:
investigate residual dynamics
```

This forces research proposals to contain an actual scientific reason for consuming compute.

---

## Search and compute allocation

Every holon owns a local research frontier.

The selected project model evaluates the prospective value of additional research on each branch.

```python
class ResearchValueAssessment:
    node_id: UUID

    value: float
    confidence: float

    reasoning: str
```

The question posed to the model is essentially:

> Given everything currently known, how valuable would another bounded unit of research on this direction be?

The model considers:

```text
potential scientific insight
potential direct improvement
importance of unresolved uncertainty
possible downstream consequences
current evidence
estimated research cost
```

Sapling adds an exploration bonus:

$$
U_i =
c\sqrt{
\frac{\log(1+N)}
     {1+n_i}
}
$$

and computes:

$$
priority_i =
\frac{V_i + U_i}
     {\sqrt{\max(1,C_i)}}
$$

where:

```text
V_i = prospective research value
N   = total local allocations
n_i = allocations to this branch
C_i = estimated cost
```

The scientific value estimate comes from the model.

Exploration, cost accounting, concurrency, and budget enforcement come from deterministic software.

### Progressive widening

Research spaces have effectively unlimited possible children.

A node may maintain approximately:

$$
K(N)=\max(3,\lfloor2\sqrt{1+N}\rfloor)
$$

active children after `N` research allocations.

Early research therefore explores a few broad possibilities.

As investment increases, more directions can open.

Human guidance may explicitly force a new branch regardless of the current widening limit.

### Non-stationary branch values

Research value is not permanent.

An unrelated branch may discover something that radically changes another branch's prospects.

Each project therefore maintains an `evidence_epoch`.

Important evidence, claim revisions, or human interventions can invalidate affected branch values.

Before significant new allocation, stale branches are reassessed against the latest scientific state.

---

## Dynamic research groups

When a coordinator identifies several reasonably independent high-value avenues, it can request child holons.

```python
class ChildHolonRequest:
    research_node_id: UUID

    objective: str
    child_objectives: list[str]

    requested_budget: float
```

For example:

```text
Question:
Why does the architecture improve only at greater depth?

Subproblems:
- residual dynamics
- update scale
- parameter-count control
- initialization interaction
```

The coordinator can create four child research units.

Those children can recursively decompose further.

Every child receives part of its parent's budget.

A child may return unused budget or request additional budget.

Organizational growth is therefore constrained by actual research allocation.

---

## Communication between researchers

The default topology is hierarchical.

```text
parent coordinator
        ↕
child coordinator
        ↕
researchers / subgroups
```

A scientific message contains:

```python
class HolonMessage:
    sender_holon_id: UUID
    recipient_holon_id: UUID

    summary: str

    claim_refs: list[UUID]
    evidence_refs: list[UUID]
    node_refs: list[UUID]

    importance: float
```

Children report upward when:

```text
their scientific interpretation changes
important evidence appears
a major hypothesis fails
they need more budget
their research program finishes
they discover something with wider implications
```

The interpretation is summarized.

The underlying evidence is referenced directly rather than repeatedly summarized.

### Direct peer communication

Research groups can open temporary communication channels when useful.

```python
class PeerChannel:
    holon_a_id: UUID
    holon_b_id: UUID

    reason: str

    expires_at: datetime
    remaining_messages: int
```

This lets two distant groups directly compare ideas without converting the overall topology into all-to-all messaging.

Peer channels are temporary and purpose-specific.

---

## Evidence routing

All evidence enters the shared commons.

It is not globally pushed into every context.

Routing has two steps.

First, pgvector retrieves approximately 10–20 active holons potentially relevant to the new evidence based on:

```text
current goal
open questions
current claims
research-node descriptions
```

Then the project model evaluates recipients in one batch:

> Would knowing this evidence plausibly change this group's research direction or an important belief?

The result is:

```python
class RoutingAssessment:
    holon_id: UUID

    impact: Literal[0, 1, 2, 3]

    reason: str
```

Interpretation:

```text
0 irrelevant
1 useful background
2 likely to change research behavior
3 strategically important
```

Items scoring `2` or `3` are delivered.

The receiving group gets a short explanation and evidence reference, then can inspect the full artifact itself.

### Preserving independent thinking

Evidence and interpretations propagate differently.

Empirical evidence becomes globally retrievable immediately.

New hypotheses and interpretations begin with:

```text
local
subtree
campaign
```

visibility.

A coordinator promotes them when broader sharing becomes useful.

A coordinator can also create an `independence_group` containing several sibling researchers.

Those siblings see the same underlying evidence but not one another's hypotheses until each has produced an independent result.

Afterward, they exchange interpretations and synthesize.

---

## Research execution

Research environments use a common backend interface.

```python
class ExecutionBackend(Protocol):
    async def create_workspace(...)
    async def run(...)
    async def cancel(...)
    async def collect_artifacts(...)
```

Initial implementations:

```text
LocalProcessBackend
LocalDockerBackend
ModalBackend
```

`LocalProcessBackend` is useful during development.

`LocalDockerBackend` is the normal safe local execution backend.

`ModalBackend` provides remote CPU/GPU scaling.

The scientific orchestration code does not care where an experiment runs.

### Code experiment environment

For ML/code research, every experiment has:

```python
class ExperimentManifest:
    experiment_id: UUID

    holon_id: UUID
    research_node_id: UUID

    parent_commit: str

    proposed_change: str
    prediction: str

    seed: int
    budget_seconds: int

    evaluator_version: str
    dataset_version: str
    environment_version: str
```

Execution flow:

```text
research proposal
       ↓
create Git worktree
       ↓
agent edits code
       ↓
commit patch
       ↓
build sandbox
       ↓
python experiment
       ↓
capture metrics/logs/artifacts
       ↓
publish evidence
       ↓
notify owning holon
```

The evaluator, hidden data, resource limits, and result parser live outside the editable repository.

An experiment returns:

```text
exit status
runtime
metrics
logs
Git commit
code diff
seed
environment
artifact hashes
```

That gives Sapling a reproducible empirical substrate.

---

## Literature and non-code research

Literature search is available to any holon at any time.

```text
search_literature(query)
open_source(source)
```

Opened literature becomes an artifact.

Claims derived from literature can directly cite that source as evidence.

The root can therefore do literature review collaboratively with the human while deeper descendants independently investigate other papers.

Research does not move through fixed stages.

A branch may alternate naturally among:

```text
literature
theory
experimentation
analysis
replication
new hypothesis generation
```

depending on what the science requires.

The same generic architecture can later support:

```text
MathEnvironment
SimulationEnvironment
FormalProofEnvironment
```

without changing the holonic/search architecture.

---

## Model architecture

A project selects one model.

All Sapling agents in that project use that model.

Project settings contain:

```text
provider
model
reasoning effort
API credential
```

The model picker behaves like a standard application model selector.

Provider support is implemented behind:

```python
class ModelRuntime(Protocol):
    async def create_session(...)
    async def turn(...)
    async def cancel(...)
    async def usage(...)
```

For the OpenAI implementation, each persistent coordinator can maintain a durable model session.

Research state still lives in Sapling's database.

Model context is a bounded projection of that state, not the canonical source of truth.

---

## Project and human interaction

The application opens with:

```text
Projects
Settings
```

Settings contains global defaults for:

```text
model
reasoning effort
cadence
BYOK keys
research budget
experiment limits
execution backend
notifications
```

A project copies those defaults but can override them independently.

Opening a project lands directly in conversation with the root coordinator.

The root coordinator is simultaneously:

```text
the human's scientific collaborator
the top research coordinator
the root allocator
the global synthesizer
```

The user can:

```text
explain an idea
ask questions
challenge assumptions
suggest hypotheses
request literature investigation
change priorities
express skepticism
redirect compute
ask for replication
ask what the system currently believes
```

User messages are stored as immutable inputs.

```python
class HumanInput:
    id: UUID
    project_id: UUID

    text: str
    timestamp: datetime
```

The root decides how that message should influence the research organization.

For example:

> I think the current explanation is wrong. Could initialization explain this instead?

may cause:

```text
new open claim
new research branch
messages to relevant subgroups
reevaluation of affected node priorities
```

The human interacts with scientific judgment rather than worker orchestration.

---

## Cadence and attention

Projects expose a `cadence` value representing how independently Sapling should continue without human input.

Internally:

```text
0.0 = consultative
1.0 = highly autonomous
```

Every development potentially relevant to the human gets an attention evaluation:

```python
class AttentionAssessment:
    importance: float
    decision_value: float

    summary: str

    possible_responses: list[str]

    default_action: str | None
```

Examples include:

```text
major contradiction
surprising discovery
important branch-allocation decision
central assumption failure
large budget request
unexpected opportunity
```

Routine scientific work simply continues.

Important developments become attention items.

For very consequential choices, the affected subtree may pause depending on cadence while unrelated branches continue running.

At higher autonomy, the system selects a default and continues while notifying the user.

The root eventually tells the user things like:

> Since you were away, 83 experiments completed. Two findings materially changed the search. One may deserve your judgment.

The user can respond through the same chat.

---

## Persistence and asynchronous jobs

Postgres stores:

```text
users
provider_credentials
user_settings
projects

holons
research_nodes
research_references

claims
evidence
claim_evidence
artifacts

experiments

holon_messages
peer_channels

human_inputs
attention_items

jobs
events

decision_snapshots
```

The job queue also lives in Postgres.

Workers lease work with:

```sql
SELECT id
FROM jobs
WHERE state = 'queued'
ORDER BY priority DESC, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Jobs have expiring leases so crashed workers do not permanently own tasks.

Holons are event-driven.

They wake when:

```text
a child finishes
an experiment finishes
new routed evidence arrives
the parent sends a message
the human speaks
their budget changes
their queue becomes empty
```

Thousands of idle holons therefore cost essentially nothing.

---

## Event stream and observability

Every important operation creates an immutable event.

Examples:

```text
HOLON_CREATED
HOLON_COMPLETED

NODE_CREATED
NODE_REVALUED
BRANCH_SELECTED

WORK_ASSIGNED

EXPERIMENT_STARTED
EXPERIMENT_FINISHED

CLAIM_CREATED
CLAIM_UPDATED

EVIDENCE_PUBLISHED
EVIDENCE_ROUTED

PEER_CHANNEL_OPENED

MESSAGE_SENT

ATTENTION_CREATED
HUMAN_INPUT

BUDGET_REALLOCATED

MODEL_TURN
MODEL_ERROR
```

The web app subscribes over:

```text
GET /projects/{id}/events/stream
```

using Server-Sent Events.

The same event stream drives:

```text
chat updates
research tree
live agents
research groups
running experiments
attention inbox
budgets
usage metrics
debug/Ops view
```

The Ops view shows the actual running organization:

```text
root
├── architecture          8 agents
│   ├── residuals         3
│   └── normalization     4
├── optimization         11
└── exploratory           6
```

plus live research events, token usage, experiment status, errors, and communication.

---

## Context management

Research state stays outside the LLM.

A `HolonContextBuilder` creates the context for every coordinator turn.

It includes:

```text
goal
parent directive

local scientific summary

active frontier
child holon summaries

important claims

recent local evidence

important routed evidence

relevant retrieved historical evidence

recent human guidance

remaining budget
```

Each section has a token budget.

Older information is progressively summarized while retaining evidence and node IDs.

A coordinator can retrieve raw supporting evidence when needed.

The model therefore works with a compact scientific view while Sapling retains complete provenance externally.

---

## Human-data and distillation loop

Sapling records important search decisions.

```python
class DecisionSnapshot:
    id: UUID

    project_id: UUID
    holon_id: UUID

    compressed_state: dict
    candidate_actions: list[dict]

    model_ranking: list[UUID]

    human_override: dict | None

    eventual_outcome: dict | None
```

Suppose the model wants:

```text
A > B > C
```

and the human says:

> B is much more interesting. Stop overvaluing incremental benchmark gains.

The record becomes a clean research-policy preference example:

```text
state S

candidate A
candidate B

preferred:
B
```

For the hackathon, the first training target should be **branch prioritization**.

We can fine-tune or distill a smaller model on:

```text
research state
+
candidate branches
+
human preferences
```

and evaluate agreement on held-out decisions.

This demonstrates:

```text
frontier research model
        ↓
large autonomous search
        ↓
human scientific judgment
        ↓
high-quality research-policy data
        ↓
small specialized model
        ↓
improved research prioritization
```

The same infrastructure can later train:

```text
evidence routing
attention classification
decomposition
branch valuation
```

---

## API surface

The HTTP interface remains compact.

Projects:

```text
GET    /projects
POST   /projects
GET    /projects/{id}
PATCH  /projects/{id}
DELETE /projects/{id}
```

Conversation:

```text
GET  /projects/{id}/messages
POST /projects/{id}/messages
```

Research:

```text
GET /projects/{id}/tree
GET /projects/{id}/holarchy
GET /projects/{id}/claims
GET /projects/{id}/evidence
GET /projects/{id}/attention
GET /projects/{id}/stats
```

Inspection:

```text
GET /holons/{id}
GET /nodes/{id}
GET /claims/{id}
GET /evidence/{id}
GET /experiments/{id}
GET /artifacts/{id}
```

Control:

```text
POST /projects/{id}/pause
POST /projects/{id}/resume

POST /holons/{id}/pause
POST /holons/{id}/resume

POST /attention/{id}/respond
```

Settings:

```text
GET   /settings
PATCH /settings

GET    /credentials
POST   /credentials
DELETE /credentials/{id}
```

Realtime:

```text
GET /projects/{id}/events/stream
```

Training:

```text
GET  /projects/{id}/training-data/stats
POST /projects/{id}/distill
GET  /training-runs/{id}
```

---

## End-to-end example

The user creates a project and chooses:

```text
Model: GPT-5.6
Effort: High
Cadence: Autonomous
Execution: Local GPU
```

Then says:

> I think gated residual networks may scale differently with depth. I'm not sure whether the effect is representational or optimization-related.

The root coordinator discusses the idea with the user and searches relevant literature.

During the conversation it creates initial research nodes:

```text
gated-residual mechanism
optimization dynamics
depth scaling
related prior work
```

It decides the mechanism and optimization directions deserve independent parallel research.

Two child holons are created.

The optimization holon identifies three independent questions and recursively creates its own research group.

Researchers begin executing Python experiments through local Docker containers.

One optimizer researcher discovers that update-to-weight ratio changes sharply with model depth.

That experiment publishes evidence `E37`.

The evidence router detects that `E37` could alter the gated-residual group's research behavior and sends it there.

That group's coordinator reevaluates its local search.

The residual-stability direction becomes less attractive.

A new update-scale hypothesis becomes highly valuable.

It opens a new branch and launches controlled experiments.

The root receives only the relevant compressed conclusion:

> Architecture and optimization are converging on a depth-dependent update-scale explanation, but the evidence is still provisional.

The human responds:

> Check whether initialization is confounding this before we spend much more compute.

The root records that guidance and routes it to the relevant groups.

Those groups independently decide how to test it.

Several runs complete.

The explanation survives one initialization regime but disappears under another.

The claim's scope narrows.

The search tree is revalued.

Compute shifts toward understanding why initialization changes the result.

The human leaves the application.

Sapling continues researching.

Groups split and terminate dynamically.

Evidence continues moving between relevant subtrees.

An hour later, Sapling generates an attention item:

> A low-performing exploratory branch found a reproducible effect that contradicts the current explanation. It may be scientifically more important than its benchmark score suggests. I have allocated a small verification budget while keeping the main search running.

The user returns, reads the report, discusses it with the root coordinator, and redirects more resources toward the anomaly.

The system continues from there.

Throughout the campaign, Sapling records the model's branch priorities, the user's interventions, and downstream research outcomes.

Those records can then be used to train a smaller research-policy model.

That is the full implementation: **a local Python autoresearch runtime capable of recursively scaling a parallel research organization, executing real code and experiments locally or remotely, searching a branching scientific space, propagating grounded evidence across that space, and exposing the root of the entire system directly to a human researcher through conversation.**
