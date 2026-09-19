import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from sapling.api import create_app
from sapling.integrations.model import decode_wire, strict_wire_schema
from sapling.runtime import HolonContextBuilder, HolonDecision, _runnable, apply_decision, run_turn
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
    assert p["goal"] == "" and p["status"] == "paused"
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


def test_chat_resolves_research_pause_without_granting_permission(workspace):
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
        client.post(f"/projects/{p['id']}/messages", json={"text": "Let us explore robustness."}).status_code
        == 201
    )
    with store.transaction() as tx:
        assert tx.get("attention_items", question["id"])["status"] == "resolved"
        assert tx.get("attention_items", permission["id"])["status"] == "pending"
        assert tx.get("holons", h["id"])["status"] == "active"
        assert tx.get("research_nodes", h["assigned_node_id"])["status"] == "active"
    assert store.claim("test") is not None


def test_stop_is_scoped_to_root_and_new_message_resumes_it(workspace):
    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Think with me"})
    with store.transaction() as tx:
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
        assert not _runnable(tx, project, tx.get("holons", p["root_holon_id"]))
        assert _runnable(tx, project, tx.get("holons", child["id"]))
        assert all(job["state"] == "cancelled" for job in tx.jobs(p["id"]))
    client.post(f"/projects/{p['id']}/messages", json={"text": "Continue with this angle instead"})
    assert store.claim("test") is not None


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
    from sapling.runtime import execute_work_order

    client, store, p, _ = workspace
    client.post(f"/projects/{p['id']}/messages", json={"text": "Read papers"})
    with store.transaction() as tx:
        h = tx.get("holons", p["root_holon_id"])

    async def dispatch(order, *args):
        return {"summary": order["rationale"] + "x" * 3500, "cost_usd": 0}

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
    with store.transaction() as tx:
        h = tx.get("holons", h["id"])
        p = tx.get("projects", p["id"])
        context = HolonContextBuilder().build(tx, h, p)
        assert h["recent_tool_results"][-1]["summary"].startswith("RESULT-6")
        assert context["recent_tool_results"][-1]["summary"].startswith("RESULT-6")


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
    from sapling.integrations.model import OpenAIModelRuntime, ModelResponseError

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
