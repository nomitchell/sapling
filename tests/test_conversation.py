import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from sapling.api import create_app
from sapling.integrations.model import decode_wire, strict_wire_schema
from sapling.runtime import (
    CURRENT_SCOPE,
    AttentionResolution,
    ConversationSynthesis,
    HolonContextBuilder,
    HolonDecision,
    InvitationIntent,
    NodeControl,
    ResearchControl,
    _runnable,
    apply_decision,
    apply_invitation_intent,
    run_turn,
    terminate_conversation_scope,
)
from sapling.store import Store
from sapling.worker import Worker


@pytest.fixture
def workspace(tmp_path):
    store = Store("sqlite:///:memory:")
    app = create_app(store, data_dir=tmp_path, workers=False)
    with TestClient(app) as client:
        p = client.post("/projects", json={"title": "This is only a label"}).json()
        yield client, store, p, app


def test_title_does_not_initialize_research(workspace):
    client, store, p, _ = workspace
    assert p["goal"] == "" and p["status"] == "active" and p["research_state"] == "planning"
    with store.transaction() as tx:
        assert tx.get("holons", p["root_holon_id"])["goal"] == ""
        assert tx.list("research_nodes", p["id"])[0]["direction"] == ""
        assert tx.jobs(p["id"]) == []
        context = HolonContextBuilder().build(tx, tx.get("holons", p["root_holon_id"]), p)
        assert p["title"] not in json.dumps(context)
    assert (
        client.post("/projects", json={"title": "No description", "goal": "Should not be used"}).status_code
        == 422
    )


def test_chat_references_but_does_not_implicitly_resolve_attention(workspace):
    client, store, p, _ = workspace
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])
        tx.update("holons", h["id"], {"status": "paused"})
        tx.update("research_nodes", h["assigned_node_id"], {"status": "abandoned"})
        question = tx.create(
            "attention_items",
            {
                "project_id": p["id"],
                "holon_id": h["id"],
                "type": "research_decision",
                "status": "pending",
                "pauses_subtree": True,
            },
        )
        permission = tx.create(
            "attention_items",
            {"project_id": p["id"], "holon_id": h["id"], "type": "permission", "status": "pending"},
        )
    assert (
        client.post(f"/projects/{p['id']}/messages", json={"text": "Let us discuss this.", "attention_ids": [question["id"]]}).status_code
        == 201
    )
    with store.transaction() as tx:
        assert tx.get("attention_items", question["id"])["status"] == "pending"
        assert tx.get("attention_items", permission["id"])["status"] == "pending"
        assert tx.get("holons", h["id"])["status"] == "paused"
        assert tx.get("research_nodes", h["assigned_node_id"])["status"] == "abandoned"
    assert store.claim("test") is not None


def test_autoresearch_requires_invitation_then_model_assessed_agreement(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Let us investigate robust generalization."})
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set(message_scope := "conversation:" + project["active_conversation_id"])
        apply_decision(tx, project, root, HolonDecision(
            updated_summary="We have a concrete question about robust generalization.",
            research_goal="Determine which mechanisms improve robust generalization efficiently.",
            response="Should I start autoresearch mode?",
            research_control=ResearchControl(action="invite"),
        ), HolonContextBuilder().build(tx, root, project))
        CURRENT_SCOPE.reset(token)
        invited = tx.get("projects", p["id"])
        invitation = invited["research_invitation"]
        assert invited["research_state"] == "planning" and invitation["status"] == "pending"
        assert tx.get("projects", p["id"])["research_state"] == "planning"
    assert client.post(f"/projects/{p['id']}/resume").status_code == 409
    message = client.post(f"/projects/{p['id']}/messages", json={"text": "Yes, start autoresearch mode."}).json()
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set(message_scope)
        assert apply_invitation_intent(tx, project, root, InvitationIntent(
            decision="accept", reason="The user explicitly agreed to start now."))
        CURRENT_SCOPE.reset(token)
        started = tx.get("projects", p["id"])
        assert started["research_state"] == "running"
        assert started["research_invitation"]["accepted_human_input_id"] == message["human_input_id"]
        assert started["last_autoresearch_transition"]["human_input_id"] == message["human_input_id"]
        assert any(job["payload"].get("work_scope") == "research" for job in tx.jobs(p["id"]))


@pytest.mark.asyncio
async def test_invitation_gate_starts_before_the_normal_research_turn(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate robust generalization."})
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set("conversation:" + project["active_conversation_id"])
        apply_decision(tx, project, root, HolonDecision(
            updated_summary="A concrete campaign is ready.", research_goal="Test robust generalization mechanisms.",
            response="Should I start autoresearch mode?", research_control=ResearchControl(action="invite"),
        ), HolonContextBuilder().build(tx, root, project))
        CURRENT_SCOPE.reset(token)
    client.post(f"/projects/{p['id']}/messages", json={"text": "I trust your judgment. Let's start."})
    seen = []

    async def model(context, schema, settings):
        seen.append((context, schema))
        if schema is InvitationIntent:
            return {"decision": {"decision": "accept", "reason": "The user explicitly authorizes starting now."}, "cost_usd": 0.001}
        return {"decision": HolonDecision(
            updated_summary="Autoresearch has started from the accepted plan.",
            response="Autoresearch is underway.",
        ).model_dump(), "cost_usd": 0.001}

    with store.transaction() as tx:
        scope = "conversation:" + tx.get("projects", p["id"])["active_conversation_id"]
        project, root = tx.get("projects", p["id"]), tx.get("holons", p["root_holon_id"])
        assert _runnable(tx, project, root, scope), (project, root, scope)
    result = await run_turn(store, p["root_holon_id"], model, None, scope=scope)
    assert result["status"] == "autoresearch_started", (result, seen)
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        assert project["research_state"] == "running"
        assert project["research_invitation"]["status"] == "accepted"
        assert any(event["type"] == "AUTORESEARCH_STARTED" for event in tx.history(p["id"]))
    await run_turn(store, p["root_holon_id"], model, None, scope="research")
    assert seen[0][1] is InvitationIntent
    assert seen[1][0]["project"]["research_state"] == "running"
    assert seen[1][0]["project"]["last_autoresearch_transition"]["decision"] == "accept"


@pytest.mark.parametrize("decision", ["decline", "continue_planning", "unclear"])
def test_nonaccepting_invitation_intent_keeps_project_in_planning(workspace, decision):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate robust generalization."})
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set("conversation:" + project["active_conversation_id"])
        apply_decision(tx, project, root, HolonDecision(
            updated_summary="A concrete campaign is ready.", research_goal="Test robust generalization mechanisms.",
            response="Should I start autoresearch mode?", research_control=ResearchControl(action="invite"),
        ), HolonContextBuilder().build(tx, root, project))
        CURRENT_SCOPE.reset(token)
    client.post(f"/projects/{p['id']}/messages", json={"text": "Let's think about this a little longer."})
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        scope = "conversation:" + project["active_conversation_id"]
        token = CURRENT_SCOPE.set(scope)
        assert not apply_invitation_intent(tx, project, root, InvitationIntent(decision=decision, reason="Not a present-tense authorization."))
        CURRENT_SCOPE.reset(token)
        updated = tx.get("projects", p["id"])
        assert updated["research_state"] == "planning"
        assert updated["research_invitation"]["status"] == ("declined" if decision == "decline" else "pending")


def test_research_lifecycle_control_must_match_human_words(workspace):
    client, store, p, _ = workspace
    with store.transaction() as tx:
        accepted = tx.create("human_inputs", {"project_id": p["id"], "text": "Yes, start pair research."})
        tx.update("projects", p["id"], {
            "research_state": "running",
            "research_invitation": {
                "id": "accepted",
                "status": "accepted",
                "accepted_human_input_id": accepted["id"],
            },
        })
    message = client.post(f"/projects/{p['id']}/messages", json={
        "text": "Resume continuous research and continue the parallel work.",
    }).json()
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set("conversation:" + project["active_conversation_id"])
        try:
            apply_decision(tx, project, root, HolonDecision(
                updated_summary="Continuous work should continue.",
                response="Continuing the research.",
                research_control=ResearchControl(
                    action="pause", human_input_id=message["human_input_id"]
                ),
            ), HolonContextBuilder().build(tx, root, project))
        finally:
            CURRENT_SCOPE.reset(token)
        assert tx.get("projects", p["id"])["research_state"] == "running"
        ignored = [
            event for event in tx.history(p["id"])
            if event["type"] == "DECISION_ACTIONS_IGNORED"
        ]
        assert "unauthorized_research_control" in ignored[-1]["payload"]["reasons"]


def test_unselected_node_controls_do_not_reject_other_conversation_actions(workspace):
    client, store, p, _ = workspace
    message = client.post(f"/projects/{p['id']}/messages", json={
        "text": "Continue the current discussion.",
    }).json()
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        unselected = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"], "status": "active",
            "title": "Unselected branch", "direction": "Do not control this branch",
            "owning_holon_id": root["id"],
        })
        token = CURRENT_SCOPE.set("conversation:" + project["active_conversation_id"])
        try:
            result = apply_decision(tx, project, root, HolonDecision(
                updated_summary="The discussion continues.",
                response="I will keep working with you here.",
                node_controls=[NodeControl(
                    node_id=unselected["id"], action="pause",
                    human_input_id=message["human_input_id"], guidance="Pause it",
                )],
            ), HolonContextBuilder().build(tx, root, project))
        finally:
            CURRENT_SCOPE.reset(token)
        assert result["work_order"] is None
        assert tx.get("research_nodes", unselected["id"])["status"] == "active"
        ignored = [
            event for event in tx.history(p["id"])
            if event["type"] == "DECISION_ACTIONS_IGNORED"
        ]
        assert "unselected_node_control" in ignored[-1]["payload"]["reasons"]


def test_read_and_targeted_resolution_are_independent(workspace):
    client, store, p, _ = workspace
    with store.transaction() as tx:
        root = tx.get("holons", p["root_holon_id"])
        check = tx.create("attention_items", {"project_id": p["id"], "holon_id": root["id"],
            "node_id": root["assigned_node_id"], "type": "research_decision", "status": "pending",
            "summary": "A useful result is ready to inspect.", "pauses_subtree": False, "read_at": None})
        first = tx.create("attention_items", {"project_id": p["id"], "holon_id": root["id"],
            "node_id": root["assigned_node_id"], "type": "research_decision", "status": "pending",
            "summary": "Choose a validation target.", "pauses_subtree": True, "read_at": None})
        second = tx.create("attention_items", {"project_id": p["id"], "holon_id": root["id"],
            "node_id": root["assigned_node_id"], "type": "research_decision", "status": "pending",
            "summary": "Choose a compute tradeoff.", "pauses_subtree": True, "read_at": None})
        tx.update("holons", root["id"], {"status": "paused"})
    assert client.post(f"/attention/{check['id']}/read").status_code == 200
    assert client.post(f"/attention/{first['id']}/read").status_code == 200
    with store.transaction() as tx:
        assert tx.get("attention_items", check["id"])["status"] == "pending"
        assert tx.get("attention_items", first["id"])["status"] == "pending"
        assert tx.get("attention_items", check["id"])["read_at"]
    message = client.post(f"/projects/{p['id']}/messages", json={
        "text": "Use the held-out corruption suite for validation.", "attention_ids": [first["id"]]}).json()
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        token = CURRENT_SCOPE.set("conversation:" + project["active_conversation_id"])
        apply_decision(tx, project, root, HolonDecision(
            updated_summary="The validation target is now specified.",
            response="I will use the held-out corruption suite.",
            attention_resolutions=[AttentionResolution(attention_id=first["id"],
                human_input_id=message["human_input_id"], resolution="Use the held-out corruption suite")],
        ), HolonContextBuilder().build(tx, root, project))
        CURRENT_SCOPE.reset(token)
        assert tx.get("attention_items", first["id"])["status"] == "resolved"
        assert tx.get("attention_items", second["id"])["status"] == "pending"
        assert tx.get("holons", root["id"])["status"] == "paused"


def test_stop_is_scoped_to_root_and_new_message_resumes_it(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Think with me"})
    with store.transaction() as tx:
        human = tx.create("human_inputs", {"project_id": p["id"], "text": "Start pair research"})
        tx.update("projects", p["id"], {"research_state": "running",
            "research_invitation": {"id": "conversation-test", "accepted_human_input_id": human["id"]}})
        child = tx.create(
            "holons",
            {
                "project_id": p["id"],
                "parent_id": p["root_holon_id"],
                "status": "active",
                "goal": "Independent work",
            },
        )
    r = client.post(f"/projects/{p['id']}/conversation/stop")
    assert r.status_code == 200
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        assert project["status"] == "active"
        stopped_scope = "conversation:" + project["active_conversation_id"]
        assert not _runnable(tx, project, tx.get("holons", p["root_holon_id"]), stopped_scope)
        assert _runnable(tx, project, tx.get("holons", p["root_holon_id"]), "research")
        assert _runnable(tx, project, tx.get("holons", child["id"]))
        assert all(job["state"] == "cancelled" for job in tx.jobs(p["id"]))
    client.post(f"/projects/{p['id']}/messages", json={"text": "Continue with this angle instead"})
    assert store.claim("test") is not None


def test_explicit_conversational_delegation_creates_persistent_campaign_researcher(workspace):
    client, store, p, _ = workspace
    with store.transaction() as tx:
        root = tx.get("holons", p["root_holon_id"])
        invitation_input = tx.create("human_inputs", {"project_id": p["id"], "text": "Yes"})
        tx.update("projects", p["id"], {
            "research_state": "paused",
            "research_invitation": {"id": "accepted", "status": "accepted",
                                    "accepted_human_input_id": invitation_input["id"]},
        })
        node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"], "status": "active",
            "title": "Independent control", "direction": "Test an independent control",
            "owning_holon_id": root["id"],
        })
    message = client.post(f"/projects/{p['id']}/messages", json={
        "text": "Delegate this branch with a small budget.", "node_ids": [node["id"]],
    }).json()
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        root = tx.get("holons", p["root_holon_id"])
        scope = "conversation:" + project["active_conversation_id"]
        token = CURRENT_SCOPE.set(scope)
        try:
            apply_decision(tx, project, root, HolonDecision(
                updated_summary="The user delegated one persistent campaign branch.",
                response="I delegated that branch with a small budget.",
                node_controls=[NodeControl(
                    node_id=node["id"], action="delegate", human_input_id=message["human_input_id"],
                    guidance="Run the independent control and report evidence.", requested_budget=0.08,
                )],
            ), HolonContextBuilder().build(tx, root, project))
        finally:
            CURRENT_SCOPE.reset(token)
        delegated = tx.get("research_nodes", node["id"])
        child = tx.get("holons", delegated["delegated_holon_id"])
        assert child["parent_id"] == root["id"]
        assert child["work_scope"] == "research"
        assert child["budget_total"] == pytest.approx(0.08)
        assert delegated["owning_holon_id"] == child["id"]
        assert tx.get("projects", p["id"])["research_state"] == "paused"
        assert any(j["holon_id"] == child["id"] and j["payload"]["work_scope"] == "research"
                   for j in tx.jobs(p["id"]))


def test_stopping_conversation_releases_active_nodes_and_temporary_budget(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Check three ideas in parallel"})
    with store.transaction() as tx:
        project = tx.get("projects", p["id"])
        scope = "conversation:" + project["active_conversation_id"]
        root = tx.get("holons", p["root_holon_id"])
        node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"], "status": "active",
            "direction": "A persistent campaign branch", "owning_holon_id": root["id"],
        })
        child = tx.create("holons", {
            "project_id": p["id"], "parent_id": root["id"], "status": "active",
            "goal": "Temporary background check", "assigned_node_id": node["id"],
            "work_scope": scope, "budget_remaining": 0.07, "budget_reserved": 0,
        })
        tx.update("research_nodes", node["id"], {
            "owning_holon_id": child["id"], "delegated_holon_id": child["id"],
        })
        root_before = root["budget_remaining"]

    assert client.post(f"/projects/{p['id']}/conversation/stop").status_code == 200

    with store.transaction() as tx:
        child = tx.get("holons", child["id"])
        node = tx.get("research_nodes", node["id"])
        root = tx.get("holons", root["id"])
        assert child["status"] == "completed" and child["terminated"] is True
        assert child["budget_remaining"] == 0
        assert root["budget_remaining"] == pytest.approx(root_before + 0.07)
        assert node["status"] == "active"
        assert node["owning_holon_id"] == root["id"]
        assert node["delegated_holon_id"] is None
        assert any(e["type"] == "CONVERSATION_NODE_RELEASED" for e in tx.history(p["id"]))


def test_conversation_limit_cleanup_is_idempotent(workspace):
    _, store, p, _ = workspace
    with store.transaction() as tx:
        scope = "conversation:finished"
        root = tx.get("holons", p["root_holon_id"])
        child = tx.create("holons", {
            "project_id": p["id"], "parent_id": root["id"], "status": "completed",
            "terminated": True, "goal": "Finished check", "work_scope": scope,
            "budget_remaining": 0.03, "budget_reserved": 0,
        })
        alert = tx.create("attention_items", {
            "project_id": p["id"], "holon_id": root["id"], "status": "pending",
            "type": "tool_error", "pauses_subtree": True,
            "summary": "A scoped tool failed", "work_scope": scope,
        })
        unused_node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"],
            "owning_holon_id": root["id"], "status": "active",
            "title": "Unused bounded idea", "work_scope": scope,
        })
        evidenced_node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"],
            "owning_holon_id": root["id"], "status": "active",
            "title": "Finished bounded check", "work_scope": scope,
        })
        tx.create("evidence", {
            "project_id": p["id"], "producer_node_id": evidenced_node["id"],
            "producer_holon_id": root["id"], "summary": "A durable result",
        })
        legacy_node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"],
            "owning_holon_id": root["id"], "status": "active",
            "title": "Legacy scoped idea",
        })
        tx.event(p["id"], "NODE_CREATED", {
            "node_id": legacy_node["id"], "holon_id": root["id"], "work_scope": scope,
        })
        before = root["budget_remaining"]
        terminate_conversation_scope(tx, p["id"], scope)
        terminate_conversation_scope(tx, p["id"], scope)
        assert tx.get("holons", child["id"])["budget_remaining"] == 0
        assert tx.get("holons", root["id"])["budget_remaining"] == pytest.approx(before + 0.03)
        assert tx.get("attention_items", alert["id"])["status"] == "resolved"
        assert tx.get("research_nodes", unused_node["id"])["status"] == "abandoned"
        assert tx.get("research_nodes", evidenced_node["id"])["status"] == "completed"
        assert tx.get("research_nodes", legacy_node["id"])["status"] == "abandoned"
        superseded = [e for e in tx.history(p["id"]) if e["type"] == "ATTENTION_SUPERSEDED"]
        assert len(superseded) == 1


def test_conversation_contains_assistant_turns_and_latest_message(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Hello"})
    with store.transaction() as tx:
        tx.create(
            "messages",
            {"project_id": p["id"], "role": "assistant", "text": "What have you been thinking about?"},
        )
    client.post(f"/projects/{p['id']}/messages", json={"text": "Robustness"})
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])
        project = tx.get("projects", p["id"])
        context = HolonContextBuilder().build(tx, h, project)
        assert [m["role"] for m in context["conversation"]] == ["user", "assistant", "user"]
        assert context["conversation"][-1]["text"] == "Robustness"
        apply_decision(
            tx,
            project,
            h,
            HolonDecision(
                updated_summary="Exploring robustness",
                research_goal="Find efficient robust training methods",
                response="Let’s separate information from optimization.",
            ),
            context,
        )
    with store.transaction() as tx:
        assert tx.get("projects", p["id"])["goal"] == "Find efficient robust training methods"
        assert any(e["type"] == "RESEARCH_DIRECTION_UPDATED" for e in tx.history(p["id"]))


def test_full_decision_wire_schema_is_closed_and_maps_round_trip():
    schema = HolonDecision.model_json_schema()
    wire = strict_wire_schema(schema)

    def check(item):
        if isinstance(item, dict):
            if item.get("type") == "object":
                assert item["additionalProperties"] is False
                assert set(item["required"]) == set(item["properties"])
            for value in item.values():
                check(value)
        elif isinstance(item, list):
            for value in item:
                check(value)

    check(wire)
    raw = {
        "updated_summary": "A question",
        "response": "Let’s check the literature.",
        "work_orders": [
            {
                "node_id": "n",
                "kind": "search_literature",
                "rationale": "Find sources",
                "estimated_cost": 0,
                "arguments": json.dumps({"query": "robustness diffusion"}),
            }
        ],
    }
    decision = HolonDecision.model_validate(decode_wire(raw, schema, schema))
    assert decision.work_orders[0].arguments["query"] == "robustness diffusion"


def test_progress_is_distinct_from_substantive_answer_and_legacy_is_preserved(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Find evidence"})
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])
        project = tx.get("projects", p["id"])
        apply_decision(tx, project, h, HolonDecision(
            updated_summary="Checking evidence", progress_note="Checking the paper's ablation tables.",
            response="The evidence supports a narrower claim.",
        ), HolonContextBuilder().build(tx, h, project))
        legacy = tx.create("messages", {"project_id": p["id"], "role": "assistant", "text": "I will search now."})
        tx.create("decision_snapshots", {"project_id": p["id"], "holon_id": h["id"],
            "decision": {"response": legacy["text"], "work_orders": [{"kind": "search_web"}]}})
    messages = client.get(f"/projects/{p['id']}/messages").json()
    assert [m.get("channel") for m in messages[-3:]] == ["progress", "answer", "progress"]
    with store.transaction() as tx:
        assert "channel" not in tx.get("messages", legacy["id"])


def test_permanent_project_delete_preserves_other_project_and_settings(workspace):
    client, store, p, _ = workspace
    other = client.post("/projects", json={"title": "Keep me"}).json()
    client.post(f"/projects/{p['id']}/messages", json={"text": "Saved research"})
    assert client.delete(f"/projects/{p['id']}?permanent=true").json() == {"deleted": True}
    assert client.get(f"/projects/{p['id']}/messages").status_code == 404
    assert client.get(f"/projects/{other['id']}/messages").status_code == 200
    with store.transaction() as tx:
        assert tx.get("projects", p["id"]) is None
        assert tx.list("messages", p["id"]) == []
        assert tx.list("holons", p["id"]) == []
        assert tx.jobs(p["id"]) == []
        assert tx.history(p["id"]) == []


@pytest.mark.asyncio
async def test_progress_only_turn_recovers_once_then_reports_error(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Read a paper"})
    contexts = []
    async def model(context, schema, settings):
        contexts.append(context)
        return {"decision": HolonDecision(updated_summary="Reading", progress_note="A read is queued.").model_dump(), "cost_usd": 0.001}
    await run_turn(store, p["root_holon_id"], model, None)
    with store.transaction() as tx:
        assert tx.get("holons", p["root_holon_id"])["empty_turn_count"] == 1
        assert tx.get("holons", p["root_holon_id"])["status"] == "active"
    await run_turn(store, p["root_holon_id"], model, None)
    assert "Nothing is queued" in contexts[-1]["runtime_feedback"]
    with store.transaction() as tx:
        assert tx.get("holons", p["root_holon_id"])["status"] == "blocked"
        assert tx.list("attention_items", p["id"])[-1]["type"] == "error"
    client.post(f"/projects/{p['id']}/messages", json={"text": "Try again"})
    with store.transaction() as tx:
        assert tx.get("holons", p["root_holon_id"])["empty_turn_count"] == 0


@pytest.mark.asyncio
async def test_worker_stop_cancels_model_without_interrupt_attention(workspace, tmp_path):
    client, store, p, app = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate"})
    started = asyncio.Event()

    async def fake_model(*args):
        started.set()
        await asyncio.Event().wait()

    worker = Worker(store, tmp_path, None)

    async def perform(job):
        await run_turn(store, job["holon_id"], fake_model, None)

    worker.perform = perform
    job = store.claim("stop-test")
    task = asyncio.create_task(worker._perform_leased(job, "stop-test"))
    await started.wait()
    with store.transaction() as tx:
        tx.update("holons", p["root_holon_id"], {"chat_stopped": True})
    await worker.stop_holon(p["root_holon_id"])
    await task
    with store.transaction() as tx:
        assert tx.get("projects", p["id"])["budget_reserved"] == 0
        assert tx.list("attention_items", p["id"]) == []
        assert any(e["type"] == "JOB_CANCELLED" for e in tx.history(p["id"]))
        assert any(job["state"] == "cancelled" for job in tx.jobs(p["id"]))


@pytest.mark.asyncio
async def test_steering_survives_failure_of_obsolete_model_call(workspace):
    from sapling.integrations.model import ModelResponseError

    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "First direction"})

    async def model(context, schema, settings):
        client.post(f"/projects/{p['id']}/messages", json={"text": "New direction"})
        raise ModelResponseError("Invalid old decision", cost_usd=0.001)

    await run_turn(store, p["root_holon_id"], model, None)
    with store.transaction() as tx:
        assert tx.get("holons", p["root_holon_id"])["status"] == "active"
        assert tx.list("attention_items", p["id"]) == []
        assert tx.get("projects", p["id"])["budget_spent"] == pytest.approx(0.001)
        assert any(e["type"] == "STALE_TURN_DISCARDED" for e in tx.history(p["id"]))
        context = HolonContextBuilder().build(
            tx, tx.get("holons", p["root_holon_id"]), tx.get("projects", p["id"])
        )
        assert context["conversation"][-1]["text"] == "New direction"


@pytest.mark.asyncio
async def test_new_tool_results_survive_context_limits(workspace):
    from sapling.runtime import CURRENT_SCOPE, execute_work_order

    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Read papers"})
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])

    async def dispatch(order, *args):
        return {"summary": order["rationale"] + "x" * 3500, "cost_usd": 0}

    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        request = current["conversation_requests"][current["active_conversation_id"]]
        from sapling.runtime import update_request

        update_request(tx, p["id"], scope, {**request, "max_tool_calls": 8})
    token = CURRENT_SCOPE.set(scope)
    try:
        for index in range(7):
            await execute_work_order(
                store,
                h["id"],
                {
                    "node_id": h["assigned_node_id"],
                    "kind": "search_web",
                    "arguments": {"query": str(index)},
                    "rationale": f"RESULT-{index}",
                    "estimated_cost": 0,
                },
                dispatch,
            )
    finally:
        CURRENT_SCOPE.reset(token)
    with store.transaction() as tx:
        h = tx.get("holons", h["id"])
        p = tx.get("projects", p["id"])
        context = HolonContextBuilder().build(tx, h, p)
        assert h["recent_tool_results"][-1]["summary"].startswith("RESULT-6")
        assert context["recent_tool_results"][-1]["summary"].startswith("RESULT-6")


@pytest.mark.asyncio
async def test_bounded_conversation_forces_synthesis_after_tool_limit(workspace):
    from sapling.runtime import execute_work_order

    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Do a bounded literature check"})
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        request = current["conversation_requests"][current["active_conversation_id"]]
        from sapling.runtime import update_request

        update_request(tx, p["id"], scope, {**request, "max_tool_calls": 16})

    async def dispatch(order, *args):
        return {"summary": f"result for {order['arguments']['query']}", "cost_usd": 0}

    token = CURRENT_SCOPE.set(scope)
    try:
        for index in range(16):
            await execute_work_order(
                store,
                h["id"],
                {
                    "node_id": h["assigned_node_id"],
                    "kind": "search_web",
                    "arguments": {"query": str(index)},
                    "rationale": "Gather one bounded result",
                    "estimated_cost": 0,
                },
                dispatch,
            )

        observed = {}

        async def model(context, schema, settings):
            observed.update(
                schema=schema,
                feedback=context["runtime_feedback"],
                history=len(context["conversation_request"]["tool_history"]),
            )
            return {
                "decision": schema(
                    response="Here is the bounded synthesis.",
                    updated_summary="The bounded source set was checked.",
                ),
                "usage": {},
                "cost_usd": 0,
            }

        result = await run_turn(store, h["id"], model, None, scope=scope)
    finally:
        CURRENT_SCOPE.reset(token)

    assert observed == {
        "schema": ConversationSynthesis,
        "feedback": (
            "The bounded conversation has used all of its tool actions. Return a substantive "
            "answer now from the available results, with source links and limitations. Do not "
            "request more tools or delegation."
        ),
        "history": 16,
    }
    assert result["status"] == "active"
    with store.transaction() as tx:
        messages = tx.list("messages", project_id=p["id"])
        assert messages[-1]["text"] == "Here is the bounded synthesis."
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][current["active_conversation_id"]]
        assert request["state"] == "completed"


@pytest.mark.asyncio
async def test_bounded_conversation_reserves_its_last_model_call_for_an_answer(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate this and report back"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        request = current["conversation_requests"][current["active_conversation_id"]]
        from sapling.runtime import update_request

        update_request(tx, p["id"], scope, {**request, "model_calls": 9, "max_model_calls": 10})

    observed = {}

    async def model(context, schema, settings):
        observed["schema"] = schema
        return {
            "decision": schema(
                response="Here is the answer from the evidence gathered so far.",
                updated_summary="Returned the bounded synthesis.",
            ),
            "usage": {},
            "cost_usd": 0,
        }

    await run_turn(store, p["root_holon_id"], model, None, scope=scope)
    assert observed["schema"] is ConversationSynthesis
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][current["active_conversation_id"]]
        assert request["state"] == "completed"
        assert tx.list("messages", project_id=p["id"])[-1]["text"].startswith("Here is the answer")


@pytest.mark.asyncio
async def test_root_blocked_completion_without_attention_recovers_instead_of_false_completion(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Search first, then report back"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]

    async def model(context, schema, settings):
        return {
            "decision": HolonDecision(
                response="I found one lead and will now search for the remaining sources.",
                updated_summary="One lead found; the requested synthesis is incomplete.",
                completion={"summary": "Not complete; more sources are needed.", "outcome": "blocked"},
            ),
            "usage": {},
            "cost_usd": 0,
        }

    await run_turn(store, p["root_holon_id"], model, None, scope=scope)
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][current["active_conversation_id"]]
        root = tx.get("holons", p["root_holon_id"])
        messages = tx.list("messages", project_id=p["id"])
        jobs = tx.jobs(p["id"])
        assert request["state"] == "active"
        assert root["empty_turn_count"] == 1
        assert "Do not promise future work" in root["runtime_feedback"]
        assert messages[-1]["channel"] == "progress"
        assert any(job["state"] == "queued" and job["holon_id"] == p["root_holon_id"] for job in jobs)
        assert any(event["type"] == "CONVERSATION_STALL_RECOVERING" for event in tx.history(p["id"]))


@pytest.mark.asyncio
async def test_repeated_root_stall_forces_a_final_synthesis(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Search first, then report back"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        tx.update("holons", p["root_holon_id"], {"empty_turn_count": 2})
    observed = {}

    async def model(context, schema, settings):
        observed["schema"] = schema
        return {
            "decision": schema(
                response="Here is the best synthesis from the sources available so far.",
                updated_summary="Returned a bounded synthesis after stalled actions.",
            ),
            "usage": {},
            "cost_usd": 0,
        }

    await run_turn(store, p["root_holon_id"], model, None, scope=scope)
    assert observed["schema"] is ConversationSynthesis
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][current["active_conversation_id"]]
        assert request["state"] == "completed"
        assert tx.list("messages", project_id=p["id"])[-1]["channel"] == "answer"


@pytest.mark.asyncio
async def test_temporary_researchers_cannot_consume_the_final_synthesis_call(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate in parallel"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        request = current["conversation_requests"][current["active_conversation_id"]]
        from sapling.runtime import update_request

        update_request(tx, p["id"], scope, {**request, "model_calls": 9, "max_model_calls": 10})
        root = tx.get("holons", p["root_holon_id"])
        node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"],
            "owning_holon_id": root["id"], "title": "Parallel check",
            "direction": "Check one source", "status": "active",
        })
        child = tx.create("holons", {
            "project_id": p["id"], "parent_id": root["id"], "work_scope": scope,
            "goal": "Check one source", "summary": "One useful result was found.",
            "assigned_node_id": node["id"], "budget_total": 0.5,
            "budget_remaining": 0.5, "budget_reserved": 0, "depth": 1,
            "status": "active", "control_epoch": 0, "turn_count": 1,
        })
        tx.update("research_nodes", node["id"], {
            "owning_holon_id": child["id"], "delegated_holon_id": child["id"],
        })

    calls = 0

    async def model(context, schema, settings):
        nonlocal calls
        calls += 1
        raise AssertionError("The temporary researcher must not consume the reserved call")

    result = await run_turn(store, child["id"], model, None, scope=scope)
    assert result["status"] == "blocked_or_stale"
    assert calls == 0
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][current["active_conversation_id"]]
        assert request["state"] == "active"
        assert request["model_calls"] == 9
        assert tx.get("holons", child["id"])["status"] == "completed"
        assert any(
            job["holon_id"] == p["root_holon_id"]
            and job["state"] == "queued"
            for job in tx.jobs(p["id"])
        )
        assert any(e["type"] == "CONVERSATION_SYNTHESIS_RESERVED" for e in tx.history(p["id"]))


@pytest.mark.asyncio
async def test_temporary_researcher_response_completes_and_wakes_parent(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Run a short parallel check"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        root = tx.get("holons", p["root_holon_id"])
        node = tx.create("research_nodes", {
            "project_id": p["id"], "parent_id": root["assigned_node_id"],
            "owning_holon_id": root["id"], "title": "Short check",
            "direction": "Find one result", "status": "active",
        })
        child = tx.create("holons", {
            "project_id": p["id"], "parent_id": root["id"], "work_scope": scope,
            "goal": "Find one result", "summary": "", "assigned_node_id": node["id"],
            "budget_total": 0.5, "budget_remaining": 0.5, "budget_reserved": 0,
            "depth": 1, "status": "active", "control_epoch": 0, "turn_count": 0,
        })
        tx.update("research_nodes", node["id"], {
            "owning_holon_id": child["id"], "delegated_holon_id": child["id"],
        })

    async def model(context, schema, settings):
        return {
            "decision": HolonDecision(
                response="The strongest result is the primary study at https://example.test/paper.",
                updated_summary="Found and returned one primary result.",
            ),
            "usage": {}, "cost_usd": 0,
        }

    await run_turn(store, child["id"], model, None, scope=scope)
    with store.transaction() as tx:
        completed = tx.get("holons", child["id"])
        assert completed["status"] == "completed"
        messages = tx.list("holon_messages", project_id=p["id"])
        assert messages[-1]["recipient_holon_id"] == p["root_holon_id"]
        assert "strongest result" in messages[-1]["summary"]
        assert any(
            job["holon_id"] == p["root_holon_id"] and job["state"] == "queued"
            for job in tx.jobs(p["id"])
        )


@pytest.mark.asyncio
async def test_conversational_root_keeps_follow_up_questions_in_chat(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "What should we do next?"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]

    async def model(context, schema, settings):
        return {
            "decision": HolonDecision(
                response="I would test the narrower hypothesis first. Does that match your intuition?",
                updated_summary="Proposed a focused next test in conversation.",
                attention_assessments=[{
                    "importance": 0.8, "decision_value": 0.9,
                    "summary": "Choose the next hypothesis", "possible_responses": ["Narrow", "Broad"],
                }],
            ),
            "usage": {}, "cost_usd": 0,
        }

    await run_turn(store, p["root_holon_id"], model, None, scope=scope)
    with store.transaction() as tx:
        assert tx.list("attention_items", p["id"]) == []
        assert tx.get("holons", p["root_holon_id"])["status"] == "active"
        messages = tx.list("messages", project_id=p["id"])
        assert messages[-1]["channel"] == "answer"
        assert "intuition" in messages[-1]["text"]


def test_source_catalog_keeps_exact_citations_after_tool_history_rolls_off(workspace):
    _, store, p, _ = workspace
    with store.transaction() as tx:
        source = tx.create("artifacts", {"project_id": p["id"], "type": "source", "metadata": {
            "title": "Better Diffusion Models Further Improve Adversarial Training",
            "final_url": "https://proceedings.mlr.press/v202/wang23ad/wang23ad.pdf"}})
        extracted = tx.create("artifacts", {"project_id": p["id"], "type": "extracted_text",
            "metadata": {"source_artifact_id": source["id"]}})
        h = tx.get("holons", p["root_holon_id"])
        context = HolonContextBuilder().build(tx, h, p)
    assert context["sources"] == [{"title": source["metadata"]["title"],
                                  "url": source["metadata"]["final_url"], "artifact_id": extracted["id"]}]


@pytest.mark.asyncio
async def test_structured_response_retry_is_bounded_and_billed(workspace):
    from sapling.integrations.model import ModelResponseError

    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Search the literature"})
    calls = 0

    async def model(context, schema, settings):
        nonlocal calls
        calls += 1
        assert bool(context["output_repair"]) == (calls == 2)
        raise ModelResponseError("Bad map", cost_usd=0.001, diagnostics=[{"type": "invalid_json_map"}])

    await run_turn(store, p["root_holon_id"], model, None)
    with store.transaction() as tx:
        assert tx.list("attention_items", p["id"]) == []
    await run_turn(store, p["root_holon_id"], model, None)
    with store.transaction() as tx:
        assert len(tx.list("attention_items", p["id"])) == 1
        assert tx.get("projects", p["id"])["budget_spent"] == pytest.approx(0.002)
        assert not [m for m in tx.list("messages", p["id"]) if m["role"] == "assistant"]


def test_structured_maps_preserve_nested_tool_arguments():
    schema = HolonDecision.model_json_schema()
    raw = {
        "updated_summary": "Run a bounded experiment",
        "work_orders": [
            {
                "node_id": "n",
                "kind": "run_experiment",
                "rationale": "Check a hypothesis",
                "arguments": {
                    "entries": [
                        {"key": "command", "value": ["python", "experiment.py"]},
                        {
                            "key": "files",
                            "value": {"entries": [{"key": "experiment.py", "value": 'print("ok")\n'}]},
                        },
                        {"key": "allow_network", "value": False},
                        {"key": "timeout", "value": 3},
                    ]
                },
            }
        ],
    }
    result = HolonDecision.model_validate(decode_wire(raw, schema, schema))
    assert result.work_orders[0].arguments == {
        "command": ["python", "experiment.py"],
        "files": {"experiment.py": 'print("ok")\n'},
        "allow_network": False,
        "timeout": 3,
    }


@pytest.mark.asyncio
async def test_output_limit_is_distinguished_from_invalid_decision():
    from types import SimpleNamespace

    from sapling.integrations.model import ModelResponseError, OpenAIModelRuntime

    async def create(**kwargs):
        return SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text='{"response":',
            id="bounded",
            usage=SimpleNamespace(input_tokens=30, output_tokens=256, input_tokens_details=None),
        )

    model = OpenAIModelRuntime(
        "test-credential",
        "test-model",
        input_cost_per_million=1,
        output_cost_per_million=2,
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    with pytest.raises(ModelResponseError) as error:
        await model.turn({}, HolonDecision, "Research", 256)
    assert error.value.diagnostics == [{"type": "incomplete_response", "reason": "max_output_tokens"}]
    assert error.value.cost_usd == pytest.approx(0.000542)


@pytest.mark.asyncio
async def test_only_final_structured_message_is_applied():
    from types import SimpleNamespace as NS

    from sapling.integrations.model import OpenAIModelRuntime

    preview = json.dumps({"updated_summary": "Draft", "response": "Draft response"})
    final = json.dumps({"updated_summary": "Final", "response": "Final response"})

    async def create(**kwargs):
        return NS(
            status="completed",
            output_text=preview + final,
            output=[
                NS(type="message", phase="commentary", content=[NS(type="output_text", text=preview)]),
                NS(type="message", phase="final_answer", content=[NS(type="output_text", text=final)]),
            ],
            id="multi-message",
            usage=NS(input_tokens=30, output_tokens=256, input_tokens_details=None),
        )

    model = OpenAIModelRuntime(
        "test-credential",
        "test-model",
        input_cost_per_million=1,
        output_cost_per_million=2,
        client=NS(responses=NS(create=create)),
    )
    result = await model.turn({}, HolonDecision, "Research")
    assert result.decision.response == "Final response"
    assert result.usage["output_tokens"] == 256
