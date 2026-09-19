import asyncio
import math

import pytest
from sqlalchemy import select

from sapling.runtime import (
    HolonContextBuilder,
    HolonDecision,
    ResearchControl,
    RoutingBatch,
    RuntimeRejected,
    apply_decision,
    branch_priority,
    claim_visible,
    execute_work_order,
    route_evidence,
    run_turn,
    widening_limit,
)
from sapling.store import Store, events


@pytest.fixture
def store():
    result = Store("sqlite+pysqlite:///:memory:")
    result.initialize()
    with result.transaction() as tx:
        tx.create(
            "projects",
            {
                "id": "p",
                "title": "Research",
                "goal": "Understand optimization",
                "status": "active",
                "settings": {"max_turn_cost_usd": 1, "cadence": 0.5},
                "budget_total": 20,
                "budget_spent": 0,
                "root_holon_id": "h",
                "evidence_epoch": 0,
                "control_epoch": 0,
            },
        )
        tx.create(
            "holons",
            {
                "id": "h",
                "project_id": "p",
                "parent_id": None,
                "goal": "Understand optimization",
                "summary": "",
                "assigned_node_id": "n",
                "budget_total": 20,
                "budget_remaining": 20,
                "depth": 0,
                "status": "active",
            },
        )
        tx.create(
            "research_nodes",
            {
                "id": "n",
                "project_id": "p",
                "parent_id": None,
                "owning_holon_id": "h",
                "title": "Optimization",
                "direction": "Test optimization mechanisms",
                "status": "active",
                "visits": 0,
                "estimated_cost": 1,
                "value_estimate": 0.5,
                "value_confidence": 0.5,
                "evidence_epoch": 0,
            },
        )
    yield result
    result.engine.dispose()


def decision(**kwargs):
    return HolonDecision(updated_summary="Current research summary", **kwargs)


def apply(store, value, hid="h"):
    with store.transaction() as tx:
        holon = tx.get("holons", hid)
        project = tx.get("projects", holon["project_id"])
        context = HolonContextBuilder().build(tx, holon, project)
        return apply_decision(tx, project, holon, value, context)


def get(store, kind, identifier):
    with store.transaction() as tx:
        return tx.get(kind, identifier)


def rows(store, kind):
    with store.transaction() as tx:
        return tx.list(kind, project_id="p")


def event_types(store):
    with store.transaction() as tx:
        return list(tx.conn.execute(select(events.c.type)).scalars())


def test_continuous_research_cannot_apply_human_controls(store):
    result = apply(
        store,
        decision(
            response="Continue the scientific work.",
            research_control=ResearchControl(action="pause", human_input_id="invented"),
        ),
    )
    assert result["work_order"] is None
    assert "HUMAN_CONTROLS_IGNORED" in event_types(store)
    assert get(store, "projects", "p")["status"] == "active"


def add_branch(store, nid="b", value=0.8, cost=1):
    with store.transaction() as tx:
        return tx.create(
            "research_nodes",
            {
                "id": nid,
                "project_id": "p",
                "parent_id": "n",
                "owning_holon_id": "h",
                "title": nid,
                "direction": "optimization",
                "status": "active",
                "visits": 0,
                "estimated_cost": cost,
                "value_estimate": value,
                "evidence_epoch": 0,
            },
        )


def add_child(store, hid, nid, group=None, budget=2):
    with store.transaction() as tx:
        tx.create(
            "research_nodes",
            {
                "id": nid,
                "project_id": "p",
                "parent_id": "n",
                "owning_holon_id": hid,
                "title": nid,
                "direction": "optimization",
                "status": "active",
                "visits": 0,
                "estimated_cost": 1,
                "value_estimate": 0.5,
                "evidence_epoch": 0,
            },
        )
        tx.create(
            "holons",
            {
                "id": hid,
                "project_id": "p",
                "parent_id": "h",
                "goal": "Understand optimization",
                "summary": "",
                "assigned_node_id": nid,
                "budget_total": budget,
                "budget_remaining": budget,
                "depth": 1,
                "status": "active",
                "independence_group": group,
            },
        )
        parent = tx.get("holons", "h")
        tx.update("holons", "h", {"budget_remaining": parent["budget_remaining"] - budget})


def work(nid="n", kind="search_literature", **kwargs):
    return {
        "node_id": nid,
        "kind": kind,
        "arguments": {"query": "optimization"},
        "rationale": "Resolve the uncertainty",
        **kwargs,
    }


def test_priority_formula_exploration_cost_and_widening():
    expected = (0.7 + 0.5 * math.sqrt(math.log(11) / 3)) / 2
    assert branch_priority(0.7, 10, 2, 4) == pytest.approx(expected)
    assert branch_priority(0.7, 10, 0, 1) > branch_priority(0.7, 10, 9, 1)
    assert widening_limit(0) == 3
    assert widening_limit(8) == 6
    with pytest.raises(ValueError):
        branch_priority(1, 0, 0, float("nan"))


def test_same_node_actions_preserve_model_order_and_avoid_repeated_reads(store):
    with store.transaction() as tx:
        tx.create("artifacts", {"id": "paper", "project_id": "p"})
    read = work(kind="read_artifact", arguments={"artifact_id": "paper"})
    search = work(kind="search_web", arguments={"query": "diffusion robustness"})
    assert apply(store, decision(work_orders=[search, read]))["work_order"]["kind"] == "search_web"
    with store.transaction() as tx:
        tx.update("holons", "h", {"recent_tool_results": [
            {"kind": "read_artifact", "arguments": {"artifact_id": "paper"}}
        ]})
    assert apply(store, decision(work_orders=[read, search]))["work_order"]["kind"] == "search_web"


def test_scheduler_selects_priority_and_saves_distillation_snapshot(store):
    add_branch(store, "costly", value=1, cost=100)
    add_branch(store, "useful", value=0.8, cost=1)
    applied = apply(store, decision(work_orders=[work("costly"), work("useful")]))
    assert applied["work_order"]["node_id"] == "useful"
    snapshot = rows(store, "decision_snapshots")[0]
    assert snapshot["model_ranking"][0] == "costly"
    assert snapshot["allocation_ranking"][0] == "useful"
    assert snapshot["human_override"] is None
    assert snapshot["eventual_outcome"] is None


def test_progressive_widening_rolls_back_entire_decision(store):
    proposals = [
        {"key": f"b{i}", "parent_node_id": "n", "title": str(i), "direction": "test", "rationale": "reason"}
        for i in range(4)
    ]
    with pytest.raises(RuntimeRejected, match="widening"):
        apply(store, decision(branch_proposals=proposals))
    assert len(rows(store, "research_nodes")) == 1
    assert not rows(store, "decision_snapshots")


def test_stale_values_require_reassessment_before_allocation(store):
    with store.transaction() as tx:
        tx.update("projects", "p", {"evidence_epoch": 2})
    with pytest.raises(RuntimeRejected, match="stale"):
        apply(store, decision(work_orders=[work()]))
    applied = apply(
        store,
        decision(
            node_assessments=[
                {"node_id": "n", "value": 0.6, "confidence": 0.7, "reasoning": "Changed evidence"}
            ],
            work_orders=[work()],
        ),
    )
    assert applied["work_order"]["node_id"] == "n"
    assert get(store, "research_nodes", "n")["evidence_epoch"] == 2


@pytest.mark.asyncio
async def test_stale_allocation_gets_one_repair_without_executing_work(store):
    with store.transaction() as tx:
        tx.update("projects", "p", {"evidence_epoch": 2})
    async def model(context, schema, settings):
        return {"decision": decision(work_orders=[work()]).model_dump(), "cost_usd": 0.001}
    async def dispatch(*args):
        pytest.fail("Invalid work must never execute")
    assert (await run_turn(store, "h", model, dispatch))["status"] == "retrying"
    assert "node_assessments" in get(store, "holons", "h")["runtime_feedback"]
    assert (await run_turn(store, "h", model, dispatch))["status"] == "rejected"


@pytest.mark.asyncio
async def test_invalid_runtime_reference_gets_one_bounded_repair(store):
    async def model(context, schema, settings):
        return {
            "decision": decision(
                work_orders=[
                    work(node_id="invented-node")
                ]
            ).model_dump(),
            "cost_usd": 0.001,
        }

    async def dispatch(*args):
        pytest.fail("Invalid work must never execute")

    first = await run_turn(store, "h", model, dispatch)
    assert first["status"] == "retrying"
    assert "invented-node" in get(store, "holons", "h")["runtime_feedback"]
    second = await run_turn(store, "h", model, dispatch)
    assert second["status"] == "rejected"


def test_recursive_delegation_conserves_money_and_returns_unused_budget(store):
    add_branch(store)
    result = apply(
        store,
        decision(
            child_holon_requests=[
                {"research_node_id": "b", "objective": "Investigate mechanism", "requested_budget": 5}
            ]
        ),
    )
    child = result["child_holon_ids"][0]
    assert get(store, "holons", "h")["budget_remaining"] == 15
    assert get(store, "holons", child)["budget_remaining"] == 5
    assert get(store, "research_nodes", "b")["owning_holon_id"] == child
    nested = apply(
        store,
        decision(
            branch_proposals=[
                {
                    "key": "sub",
                    "parent_node_id": "b",
                    "title": "Subproblem",
                    "direction": "Independent mechanism",
                    "rationale": "Separate question",
                }
            ],
            child_holon_requests=[
                {"research_node_id": "sub", "objective": "Nested investigation", "requested_budget": 2}
            ],
        ),
        child,
    )
    grandchild = nested["child_holon_ids"][0]
    assert get(store, "holons", grandchild)["depth"] == 2
    with pytest.raises(RuntimeRejected, match="children"):
        apply(store, decision(completion={"summary": "Done"}), child)
    apply(store, decision(completion={"summary": "Nested finding"}), grandchild)
    apply(store, decision(completion={"summary": "Finding"}), child)
    assert get(store, "holons", "h")["budget_remaining"] == 20
    assert get(store, "holons", child)["budget_remaining"] == 0
    assert get(store, "projects", "p")["budget_spent"] == 0


def test_child_allocation_cannot_exceed_unreserved_parent_money(store):
    add_branch(store)
    with store.transaction() as tx:
        tx.update("holons", "h", {"budget_reserved": 18})
    with pytest.raises(RuntimeRejected, match="budget"):
        apply(
            store,
            decision(
                child_holon_requests=[{"research_node_id": "b", "objective": "Work", "requested_budget": 3}]
            ),
        )
    assert len(rows(store, "holons")) == 1
    assert get(store, "holons", "h")["budget_remaining"] == 20


@pytest.mark.parametrize("field", ["work", "claim", "artifact", "message", "budget"])
def test_cross_project_references_are_rejected_transactionally(store, field):
    with store.transaction() as tx:
        tx.create(
            "holons", {"id": "outsider", "project_id": "other", "parent_id": "h", "budget_remaining": 0}
        )
        tx.create("research_nodes", {"id": "foreign", "project_id": "other", "owning_holon_id": "h"})
        tx.create("claims", {"id": "foreign-claim", "project_id": "other", "origin_holon_id": "h"})
        tx.create("artifacts", {"id": "foreign-artifact", "project_id": "other"})
    kwargs = {
        "work": {"work_orders": [work("foreign")]},
        "claim": {"claim_updates": [{"claim_id": "foreign-claim", "status": "supported"}]},
        "artifact": {
            "work_orders": [work(kind="read_artifact", arguments={"artifact_id": "foreign-artifact"})]
        },
        "message": {"parent_messages": [{"recipient_holon_id": "outsider", "summary": "Leak"}]},
        "budget": {
            "budget_transfers": [{"child_holon_id": "outsider", "amount": 1, "reason": "Bad transfer"}]
        },
    }[field]
    with pytest.raises(RuntimeRejected, match="cross-project"):
        apply(store, decision(**kwargs))
    assert not rows(store, "decision_snapshots")
    assert get(store, "holons", "h")["summary"] == ""


def test_claim_updates_are_scoped_and_empirical_evidence_cannot_be_invented(store):
    add_child(store, "a", "na")
    add_child(store, "b", "nb")
    with store.transaction() as tx:
        tx.create(
            "claims",
            {
                "id": "claim-a",
                "project_id": "p",
                "statement": "Hypothesis",
                "origin_holon_id": "a",
                "visibility": "campaign",
            },
        )
    with pytest.raises(RuntimeRejected, match="originating"):
        apply(store, decision(claim_updates=[{"claim_id": "claim-a", "status": "rejected"}]), "b")
    with pytest.raises(RuntimeRejected, match="Empirical"):
        apply(store, decision(evidence_proposals=[{"type": "experiment", "summary": "Invented success"}]))
    assert not rows(store, "evidence")


def test_independent_siblings_share_evidence_but_not_hypotheses(store):
    add_child(store, "a", "na", group="h:g")
    add_child(store, "b", "nb", group="h:g")
    with store.transaction() as tx:
        claim = tx.create(
            "claims",
            {
                "id": "ca",
                "project_id": "p",
                "statement": "Private hypothesis",
                "origin_holon_id": "a",
                "visibility": "campaign",
            },
        )
        tx.create(
            "evidence",
            {
                "id": "ea",
                "project_id": "p",
                "summary": "Optimization observation",
                "producer_holon_id": "a",
                "producer_node_id": "na",
            },
        )
        assert not claim_visible(tx, claim, tx.get("holons", "b"))
        ctx = HolonContextBuilder().build(tx, tx.get("holons", "b"), tx.get("projects", "p"))
        assert not ctx["claims"]
        assert ctx["retrieved_evidence"][0]["id"] == "ea"
    with pytest.raises(RuntimeRejected, match="Independent"):
        apply(store, decision(parent_messages=[{"recipient_holon_id": "b", "summary": "My hypothesis"}]), "a")
    with store.transaction() as tx:
        tx.update("holons", "a", {"independent_result_ready": True})
        tx.update("holons", "b", {"independent_result_ready": True})
        assert claim_visible(tx, claim, tx.get("holons", "b"))


def test_peer_communication_requires_a_limited_channel(store):
    add_child(store, "a", "na")
    add_child(store, "b", "nb")
    message = {"recipient_holon_id": "b", "summary": "Compare evidence"}
    with pytest.raises(RuntimeRejected, match="channel"):
        apply(store, decision(parent_messages=[message]), "a")
    apply(
        store,
        decision(
            peer_channel_requests=[{"holon_id": "b", "reason": "Compare", "message_limit": 1}],
            parent_messages=[message],
        ),
        "a",
    )
    with pytest.raises(RuntimeRejected, match="channel"):
        apply(store, decision(parent_messages=[message]), "a")


@pytest.mark.parametrize("cadence,paused", [(0, True), (1, False)])
def test_attention_cadence_only_pauses_affected_subtree(store, cadence, paused):
    add_child(store, "a", "na")
    add_child(store, "b", "nb")
    with store.transaction() as tx:
        tx.update("projects", "p", {"settings": {"cadence": cadence}})
    result = apply(
        store,
        decision(
            attention_assessments=[
                {
                    "importance": 0.9,
                    "decision_value": 0.8,
                    "summary": "An important choice",
                    "default_action": "Run bounded check",
                }
            ],
            work_orders=[work("na")],
        ),
        "a",
    )
    assert result["paused"] is paused
    assert (result["work_order"] is None) is paused
    assert get(store, "holons", "b")["status"] == "active"
    assert rows(store, "attention_items")[0]["status"] == "pending"


async def test_paid_stale_turn_discards_actions_but_keeps_usage(store):
    async def model(context, schema, settings):
        assert settings["max_cost_usd"] == 1
        with store.transaction() as tx:
            assert tx.get("projects", "p")["budget_reserved"] == 1
            tx.update("projects", "p", {"control_epoch": 1})
        return {
            "decision": decision(response="Must not appear").model_dump(),
            "cost_usd": 0.25,
            "usage": {"output_tokens": 10},
        }

    result = await run_turn(store, "h", model, None)
    assert result["status"] == "stale"
    assert get(store, "projects", "p")["budget_spent"] == 0.25
    assert get(store, "projects", "p")["budget_reserved"] == 0
    assert not rows(store, "messages")
    assert not rows(store, "decision_snapshots")


async def test_pause_during_model_prevents_tool_start_and_accounts_cost(store):
    called = []

    async def model(context, schema, settings):
        with store.transaction() as tx:
            tx.update("projects", "p", {"status": "paused"})
        return {"decision": decision(work_orders=[work()]).model_dump(), "cost_usd": 0.1}

    async def dispatch(*args):
        called.append(args)

    result = await run_turn(store, "h", model, dispatch)
    assert result["status"] == "stale"
    assert not called
    assert get(store, "projects", "p")["budget_spent"] == 0.1


async def test_unknown_model_failure_charges_reservation_known_preflight_charges_zero(store):
    async def unknown(*args):
        raise TimeoutError("Provider may have processed request")

    await run_turn(store, "h", unknown, None)
    assert get(store, "projects", "p")["budget_spent"] == 1
    assert get(store, "projects", "p")["budget_reserved"] == 0
    with store.transaction() as tx:
        tx.update("holons", "h", {"status": "active"})

    async def known(*args):
        error = ValueError("No request was issued")
        error.cost_usd = 0
        raise error

    await run_turn(store, "h", known, None)
    assert get(store, "projects", "p")["budget_spent"] == 1


async def test_permission_blocks_repeated_model_and_tool_calls(store):
    calls = []

    async def model(*args):
        calls.append("model")
        return {"decision": decision(work_orders=[work()]).model_dump(), "cost_usd": 0.1}

    async def dispatch(*args):
        calls.append("tool")
        return {"status": "awaiting_permission", "cost_usd": 0}

    await run_turn(store, "h", model, dispatch)
    assert get(store, "holons", "h")["status"] == "awaiting_permission"
    result = await run_turn(store, "h", model, dispatch)
    assert result["status"] == "skipped"
    assert calls == ["model", "tool"]
    assert get(store, "research_nodes", "n")["visits"] == 0


async def test_tool_results_publish_append_only_provenance_and_snapshot_outcome(store):
    async def model(*args):
        return {"decision": decision(work_orders=[work()]).model_dump(), "cost_usd": 0.1}

    async def dispatch(order, holon, project):
        with store.transaction() as tx:
            tx.create("artifacts", {"id": "ar", "project_id": "p", "sha256": "hash", "uri": "source"})
        return {
            "summary": "Source read",
            "evidence": [
                {"type": "source", "summary": "Observed optimization result", "artifact_ids": ["ar"]}
            ],
            "cost_usd": 0,
        }

    result = await run_turn(store, "h", model, dispatch)
    assert result["status"] == "active"
    evidence = rows(store, "evidence")[0]
    assert evidence["artifact_ids"] == ["ar"]
    assert evidence["producer_holon_id"] == "h"
    assert evidence["producer_node_id"] == "n"
    assert get(store, "projects", "p")["evidence_epoch"] == 1
    assert get(store, "research_nodes", "n")["visits"] == 1
    assert rows(store, "decision_snapshots")[0]["eventual_outcome"]["evidence_ids"] == [evidence["id"]]
    with store.transaction() as tx, pytest.raises(ValueError, match="append-only"):
        tx.update("evidence", evidence["id"], {"summary": "Changed"})


async def test_two_stage_routing_delivers_only_high_impact_and_never_foreign_holons(store):
    add_child(store, "a", "na")
    add_child(store, "b", "nb")
    with store.transaction() as tx:
        tx.create(
            "evidence",
            {
                "id": "e",
                "project_id": "p",
                "summary": "Optimization mechanism observation",
                "producer_holon_id": "h",
                "producer_node_id": "n",
            },
        )

    async def model(context, schema, settings):
        assert schema is RoutingBatch
        assert {c["holon_id"] for c in context["candidates"]} == {"a", "b"}
        return {
            "decision": {
                "assessments": [
                    {"holon_id": "a", "impact": 3, "reason": "Changes interpretation"},
                    {"holon_id": "b", "impact": 1, "reason": "Background"},
                ]
            },
            "cost_usd": 0.05,
        }

    result = await route_evidence(store, "e", model)
    assert result["recipients"] == ["a"]
    assert len(rows(store, "holon_messages")) == 1

    async def malicious(*args):
        return {
            "decision": {"assessments": [{"holon_id": "foreign", "impact": 3, "reason": "Leak"}]},
            "cost_usd": 0.05,
        }

    result = await route_evidence(store, "e", malicious)
    assert result["status"] == "rejected"
    assert len(rows(store, "holon_messages")) == 1
    assert get(store, "projects", "p")["budget_spent"] == 0.1


def test_context_excludes_credentials_and_bounds_large_sections(store):
    with store.transaction() as tx:
        tx.update(
            "projects",
            "p",
            {
                "settings": {
                    "api_key": "secret-value",
                    "nested": {"authorization": "bearer"},
                    "model": "chosen",
                }
            },
        )
        for i in range(30):
            tx.create("human_inputs", {"project_id": "p", "text": "x" * 10000})
        context = HolonContextBuilder().build(tx, tx.get("holons", "h"), tx.get("projects", "p"))
    assert "api_key" not in context["project"]["settings"]
    assert "authorization" not in context["project"]["settings"]["nested"]
    assert len(str(context["human_guidance"])) < 8500


async def test_cancelled_model_settles_reservation_and_propagates_cancellation(store):
    started = asyncio.Event()

    async def model(*args):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_turn(store, "h", model, None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get(store, "projects", "p")["budget_reserved"] == 0
    assert get(store, "projects", "p")["budget_spent"] == 1
    assert get(store, "holons", "h")["status"] == "blocked"
    assert get(store, "research_nodes", "n")["budget_spent"] == 1


async def test_concurrent_holons_cannot_reserve_the_same_project_dollar(store):
    add_child(store, "a", "na")
    add_child(store, "b", "nb")
    with store.transaction() as tx:
        tx.update("projects", "p", {"budget_total": 1})
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def model(context, schema, settings):
        calls.append(context["holon"]["id"])
        started.set()
        await finish.wait()
        return {"decision": decision().model_dump(), "cost_usd": 0.4}

    first = asyncio.create_task(run_turn(store, "a", model, None))
    await started.wait()
    second = await run_turn(store, "b", model, None)
    assert second["status"] == "blocked_or_stale"
    finish.set()
    await first
    assert calls == ["a"]
    assert get(store, "projects", "p")["budget_reserved"] == 0
    assert get(store, "projects", "p")["budget_spent"] == 0.4


async def test_allocation_investment_expands_ancestral_widening(store):
    add_branch(store)

    async def dispatch(*args):
        return {"summary": "Bounded work finished", "cost_usd": 0}

    for _ in range(3):
        await execute_work_order(store, "h", work("b"), dispatch)
    root = get(store, "research_nodes", "n")
    assert root["visits"] == 3
    assert widening_limit(root["visits"]) == 4
    assert get(store, "holons", "h")["allocation_count"] == 3
    proposals = [
        {
            "key": f"extra-{i}",
            "parent_node_id": "n",
            "title": str(i),
            "direction": "Explore",
            "rationale": "Investment justifies expansion",
        }
        for i in range(3)
    ]
    apply(store, decision(branch_proposals=proposals))
    assert len([n for n in rows(store, "research_nodes") if n.get("parent_id") == "n"]) == 4


def test_parent_can_reassess_delegate_but_cannot_execute_its_work(store):
    add_child(store, "a", "na")
    with store.transaction() as tx:
        context = HolonContextBuilder().build(tx, tx.get("holons", "h"), tx.get("projects", "p"))
        assert "na" in {n["id"] for n in context["frontier"]}
    apply(
        store,
        decision(
            node_assessments=[
                {"node_id": "na", "value": 0.9, "confidence": 0.8, "reasoning": "High value group"}
            ],
            budget_transfers=[{"child_holon_id": "a", "amount": 1, "reason": "Allocate next bounded unit"}],
        ),
    )
    assert get(store, "holons", "a")["budget_remaining"] == 3
    assert get(store, "research_nodes", "na")["value_estimate"] == 0.9
    with pytest.raises(RuntimeRejected, match="own research"):
        apply(store, decision(work_orders=[work("na")]))


async def test_nonempirical_reasoning_is_not_routed_as_shared_observation(store):
    add_child(store, "a", "na", group="h:g")
    add_child(store, "b", "nb", group="h:g")
    output = apply(
        store,
        decision(
            evidence_proposals=[{"type": "proof_fragment", "summary": "Optimization conjecture sketch"}]
        ),
        "a",
    )
    evidence_id = output["published_evidence_ids"][0]
    with store.transaction() as tx:
        context = HolonContextBuilder().build(tx, tx.get("holons", "b"), tx.get("projects", "p"))
        assert not context["retrieved_evidence"]
    result = await route_evidence(store, evidence_id, None)
    assert result["status"] == "local_interpretation"


async def test_tool_reported_failure_stops_rescheduling(store):
    async def model(*args):
        return {"decision": decision(work_orders=[work()]).model_dump(), "cost_usd": 0.1}

    async def dispatch(*args):
        return {"status": "error", "summary": "Unavailable service", "cost_usd": 0}

    result = await run_turn(store, "h", model, dispatch)
    assert result["status"] == "blocked"
    assert get(store, "research_nodes", "n")["visits"] == 0


def test_attention_carries_snapshot_for_real_human_preference_records(store):
    apply(
        store,
        decision(
            attention_assessments=[
                {"importance": 0.9, "decision_value": 0.8, "summary": "Choose a direction"}
            ]
        ),
    )
    item = rows(store, "attention_items")[0]
    snapshot = rows(store, "decision_snapshots")[0]
    assert item["decision_snapshot_id"] == snapshot["id"]


async def test_stale_routing_accounts_cost_without_delivering_old_assessment(store):
    add_child(store, "a", "na")
    with store.transaction() as tx:
        tx.create(
            "evidence",
            {
                "id": "e",
                "project_id": "p",
                "summary": "Optimization result",
                "producer_holon_id": "h",
                "producer_node_id": "n",
            },
        )

    async def model(*args):
        with store.transaction() as tx:
            tx.update("projects", "p", {"control_epoch": 1})
        return {
            "decision": {"assessments": [{"holon_id": "a", "impact": 3, "reason": "Old assessment"}]},
            "cost_usd": 0.05,
        }

    result = await route_evidence(store, "e", model)
    assert result["status"] == "stale"
    assert get(store, "projects", "p")["budget_spent"] == 0.05
    assert not rows(store, "holon_messages")
    with store.transaction() as tx:
        jobs = tx.jobs("p")
    assert any(j["kind"] == "route_evidence" and j["payload"]["evidence_id"] == "e" for j in jobs)


async def test_approved_work_helper_wakes_next_coordinator_turn(store):
    async def dispatch(*args):
        return {"summary": "Approved work completed", "cost_usd": 0}

    await execute_work_order(store, "h", work(), dispatch)
    with store.transaction() as tx:
        queued = [j for j in tx.jobs("p") if j["kind"] == "turn" and j["state"] == "queued"]
    assert len(queued) == 1


async def test_message_arriving_during_parent_decision_invalidates_old_actions(store):
    add_child(store, "a", "na")

    async def model(*args):
        apply(
            store,
            decision(parent_messages=[{"recipient_holon_id": "h", "summary": "A crucial new constraint"}]),
            "a",
        )
        return {"decision": decision(response="Old answer").model_dump(), "cost_usd": 0.1}

    result = await run_turn(store, "h", model, None)
    assert result["status"] == "stale"
    assert not rows(store, "messages")
    assert get(store, "projects", "p")["budget_spent"] == 0.1


async def test_invalid_model_response_preserves_sanitized_known_usage(store):
    from sapling.integrations.model import ModelResponseError

    async def model(*args):
        raise ModelResponseError(
            "Invalid structured response",
            usage={
                "input_tokens": 220,
                "output_tokens": 140,
                "cached_input_tokens": 100,
                "api_key": "must-not-persist",
                "details": {"reasoning_tokens": 90, "authorization": "must-not-persist"},
            },
            cost_usd=0.125,
            response_id="response-invalid",
        )

    result = await run_turn(store, "h", model, None)
    assert result["status"] == "blocked_or_stale"
    with store.transaction() as tx:
        recorded = tx.conn.execute(
            select(events.c.payload).where(events.c.type == "USAGE_RECORDED")
        ).scalar_one()
    assert recorded["usage"] == {
        "input_tokens": 220,
        "output_tokens": 140,
        "cached_input_tokens": 100,
        "details": {"reasoning_tokens": 90},
    }
    assert recorded["cost_usd"] == 0.125
    assert recorded["estimated"] is False
    assert get(store, "projects", "p")["budget_spent"] == 0.125
    assert get(store, "projects", "p")["budget_reserved"] == 0
    assert not rows(store, "decision_snapshots")
