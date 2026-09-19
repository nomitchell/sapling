"""Real store/worker/API integration, with no provider credentials or paid calls."""

import asyncio
import copy
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sapling.api import create_app
from sapling.runtime import HolonCompletion, HolonDecision
from sapling.store import Store
from sapling.worker import Worker, save_artifact


class EmptyVault:
    def get(self, provider):
        return None


class ConfiguredVault:
    def get(self, provider):
        return "bt-test-key" if provider == "baseten" else None


@pytest.fixture
def worker_app(tmp_path):
    store = Store(f"sqlite:///{(tmp_path / 'worker.sqlite3').as_posix()}")
    vault = EmptyVault()
    app = create_app(store, data_dir=tmp_path, workers=False, vault=vault)
    with TestClient(app) as client:
        yield client, store, Worker(store, tmp_path, vault), tmp_path
    store.engine.dispose()


def create_project(fixture, mode="ask", **settings):
    client, store, _, _ = fixture
    response = client.post("/projects", json={"title": "Worker integration", "settings": {"permission_mode": mode, "execution_backend": "process", **settings}})
    assert response.status_code == 201, response.text
    project = response.json()
    # These worker tests begin after consent; lifecycle tests cover the actual
    # invitation/conversation handshake separately.
    with store.transaction() as tx:
        human = tx.create("human_inputs", {"project_id": project["id"], "text": "Start pair research"})
        tx.update("projects", project["id"], {"research_invitation": {"id": "worker-test-invitation", "accepted_human_input_id": human["id"]}})
    assert client.post(f"/projects/{project['id']}/resume").status_code == 200
    with store.transaction() as tx:
        return tx.get("projects", project["id"]), tx.get("holons", project["root_holon_id"])


def work_order(holon, *, script="print('bounded result')", timeout=10):
    return {
        "node_id": holon["assigned_node_id"],
        "kind": "run_experiment",
        "arguments": {"command": [sys.executable, "research.py"], "files": {"research.py": script}, "timeout_seconds": timeout, "seed": 17},
        "rationale": "Verify local execution and immutable evidence provenance.",
        "estimated_cost": 0,
    }


def job(project, holon, order=None, attention_id=None):
    payload = {} if order is None else {"work_order": order}
    if attention_id:
        payload["attention_id"] = attention_id
    return {"project_id": project["id"], "holon_id": holon["id"], "kind": "turn" if order is None else "work_order", "payload": payload}


@pytest.mark.asyncio
async def test_missing_key_preserves_human_input_and_creates_one_configuration_attention(worker_app):
    client, store, worker, _ = worker_app
    project, holon = create_project(worker_app)
    text = "Investigate whether this claimed relation holds under alternative assumptions."
    assert client.post(f"/projects/{project['id']}/messages", json={"text": text}).status_code == 201
    await worker.perform(job(project, holon))
    await worker.perform(job(project, holon))
    with store.transaction() as tx:
        inputs = tx.list("human_inputs", project["id"])
        attention = tx.list("attention_items", project["id"], type="configuration")
        matching = [item for item in inputs if item["text"] == text]
        assert len(matching) == 1
        assert len(attention) == 1 and "API key" in attention[0]["summary"]
        assert tx.get("holons", holon["id"])["status"] == "blocked"
        assert tx.get("projects", project["id"])["budget_spent"] == 0
        assert not tx.list("experiments", project["id"])
        assert any(event["type"] == "ATTENTION_CREATED" for event in tx.history(project["id"]))


def test_orphaned_active_conversation_is_requeued_after_restart(worker_app):
    _, store, worker, _ = worker_app
    project, holon = create_project(worker_app)
    with store.transaction() as tx:
        tx.cancel_queued(holon["id"])
        tx.update(
            "projects",
            project["id"],
            {
                "active_conversation_id": "orphaned",
                "conversation_requests": {
                    "orphaned": {
                        "id": "orphaned",
                        "state": "active",
                        "budget_total": 1,
                        "budget_spent": 0,
                        "budget_reserved": 0,
                        "model_calls": 0,
                        "max_model_calls": 48,
                        "tool_calls": 0,
                        "max_tool_calls": 32,
                    }
                },
            },
        )
    worker._recover_orphaned_turns()
    with store.transaction() as tx:
        queued = [item for item in tx.jobs(project["id"]) if item["state"] == "queued"]
        events = tx.history(project["id"])
    assert any(item["holon_id"] == holon["id"] and item["payload"]["work_scope"] == "conversation:orphaned" for item in queued)
    assert any(item["type"] == "ORPHANED_TURN_RECOVERED" for item in events)


@pytest.mark.asyncio
async def test_baseten_rate_limit_retries_before_the_turn_fails(worker_app, monkeypatch):
    _, store, worker, _ = worker_app
    worker.vault = ConfiguredVault()
    project, holon = create_project(
        worker_app,
        provider="baseten",
        model="zai-org/GLM-5.3-Flash",
        reasoning_effort="high",
    )
    calls, sleeps = [], []

    class RetryModel:
        async def turn(self, *args, **kwargs):
            calls.append(kwargs["max_output_tokens"])
            if len(calls) == 1:
                request = httpx.Request("POST", "https://inference.baseten.co/v1/chat/completions")
                from openai import RateLimitError
                raise RateLimitError("rate limited", response=httpx.Response(429, request=request), body=None)
            return type("Turn", (), {
                "decision": HolonDecision(
                    updated_summary="Recovered after provider backoff.",
                    completion=HolonCompletion(summary="No further work needed."),
                ),
                "usage": {"input_tokens": 10, "output_tokens": 10, "cached_input_tokens": 0},
                "cost_usd": 0.00001,
                "response_id": "retry-test",
            })()

        async def close(self):
            pass

    monkeypatch.setattr("sapling.integrations.model.model_runtime", lambda *args, **kwargs: RetryModel())

    async def no_wait(delay):
        sleeps.append(delay)

    monkeypatch.setattr("sapling.worker.asyncio.sleep", no_wait)
    await worker.perform(job(project, holon))

    assert len(calls) == 2
    assert sleeps == [1]
    with store.transaction() as tx:
        events = tx.history(project["id"])
    assert any(event["type"] == "MODEL_RATE_LIMIT_RETRY" for event in events)


@pytest.mark.asyncio
async def test_ask_mode_records_exact_action_without_running_it(worker_app):
    _, store, worker, directory = worker_app
    project, holon = create_project(worker_app)
    order = work_order(holon)
    await worker.perform(job(project, holon, order))
    with store.transaction() as tx:
        attention = tx.list("attention_items", project["id"], type="permission")
        assert len(attention) == 1
        assert attention[0]["work_order"] == order
        assert attention[0]["categories"] == ["execute_host"]
        assert tx.get("holons", holon["id"])["status"] == "awaiting_permission"
        assert not tx.list("experiments", project["id"])
    assert not (directory / "workspaces").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required for real execution")
async def test_approved_job_executes_and_publishes_evidence_without_model_key(worker_app):
    client, store, worker, directory = worker_app
    project, holon = create_project(worker_app)
    # Consume the original wake so a lingering fixture job cannot conceal an
    # approved-work continuation bug.
    initial = store.claim("bootstrap")
    assert initial and initial["kind"] == "turn"
    store.finish(initial["id"], "bootstrap")
    order = work_order(holon, script="from pathlib import Path\nPath('measurement.txt').write_text('42')\nprint('measurement completed')")
    await worker.perform(job(project, holon, order))
    with store.transaction() as tx:
        attention = tx.list("attention_items", project["id"], type="permission")[0]
    response = client.post(f"/attention/{attention['id']}/respond", json={"approve": True})
    assert response.status_code == 200, response.text
    queued = store.claim("approved-action")
    assert queued and queued["kind"] == "work_order"
    await worker._perform_leased(queued, "approved-action")
    with store.transaction() as tx:
        experiment = tx.list("experiments", project["id"])[0]
        assert experiment["status"] == "completed"
        assert experiment["result"]["exit_code"] == 0
        assert experiment["result"]["git_commit"]
        evidence = tx.list("evidence", project["id"])
        assert len(evidence) == 1 and evidence[0]["producer_holon_id"] == holon["id"]
        assert evidence[0]["scope"]["status"] == "completed"
        assert evidence[0]["scope"]["metrics_are_untrusted"]
        assert tx.get("attention_items", attention["id"])["consumed"]
        assert tx.get("projects", project["id"])["budget_spent"] == 0
        assert tx.get("projects", project["id"])["evidence_epoch"] > 0
        for artifact_id in evidence[0]["artifact_ids"]:
            artifact = tx.get("artifacts", artifact_id)
            assert artifact["project_id"] == project["id"]
            assert (directory / artifact["uri"]).exists()
        current_holon = tx.get("holons", holon["id"])
        assert "measurement completed" in str(current_holon["recent_tool_results"])
        pending = [item for item in tx.jobs(project["id"]) if item["state"] == "queued"]
        turns = [item for item in pending if item["kind"] == "turn"]
        assert len(turns) == 1 and turns[0]["holon_id"] == holon["id"]
        routes = [item for item in pending if item["kind"] == "route_evidence"]
        assert len(routes) == 1 and routes[0]["payload"]["evidence_id"] == evidence[0]["id"]
    with pytest.raises(ValueError, match="already been used"):
        await worker.dispatch(order, current_holon, project, approval_id=attention["id"])


@pytest.mark.asyncio
async def test_permission_cannot_authorize_different_arguments_or_project(worker_app):
    client, store, worker, directory = worker_app
    project, holon = create_project(worker_app)
    artifact = save_artifact(store, directory, project["id"], b"private research notes", "notes.txt", "document")
    order = {"node_id": holon["assigned_node_id"], "kind": "read_artifact", "arguments": {"artifact_id": artifact["id"], "offset": 0}, "rationale": "Read the original observation.", "estimated_cost": 0}
    result = await worker.dispatch(order, holon, project)
    attention_id = result["attention_id"]
    assert client.post(f"/attention/{attention_id}/respond", json={"approve": True}).status_code == 200
    tampered = copy.deepcopy(order)
    tampered["arguments"]["offset"] = 1
    with pytest.raises(ValueError, match="Permission does not match"):
        await worker.dispatch(tampered, holon, project, approval_id=attention_id)
    other_project, other_holon = create_project(worker_app)
    other_order = {**order, "node_id": other_holon["assigned_node_id"]}
    with pytest.raises(ValueError, match="Permission does not match"):
        await worker.dispatch(other_order, other_holon, other_project, approval_id=attention_id)
    with store.transaction() as tx:
        assert not tx.get("attention_items", attention_id).get("consumed")
    result = await worker.dispatch(order, holon, project, approval_id=attention_id)
    assert result["summary"] == "private research notes"
    with pytest.raises(ValueError, match="already been used"):
        await worker.dispatch(order, holon, project, approval_id=attention_id)


@pytest.mark.asyncio
async def test_artifact_cross_project_access_denied_even_with_full_autonomy(worker_app):
    _, store, worker, directory = worker_app
    first, _ = create_project(worker_app, mode="yolo")
    second, holon = create_project(worker_app, mode="yolo")
    artifact = save_artifact(store, directory, first["id"], b"first project only", "notes.txt", "document")
    order = {"node_id": holon["assigned_node_id"], "kind": "read_artifact", "arguments": {"artifact_id": artifact["id"]}, "rationale": "Inspect artifact.", "estimated_cost": 0}
    with pytest.raises(ValueError, match="does not belong to this project"):
        await worker.dispatch(order, holon, second)


@pytest.mark.asyncio
async def test_raw_html_artifact_reads_text_and_can_find_later_passages(worker_app):
    _, store, worker, directory = worker_app
    project, holon = create_project(worker_app, mode="yolo")
    html = ("<html><script>invisible script</script><body><p>" + "Introduction. " * 600
            + "</p><h2>Ablation results</h2><p>The matched control scored 42.</p></body></html>")
    artifact = save_artifact(store, directory, project["id"], html.encode(), "paper.html", "source",
                             {"content_type": "text/html"})
    order = {"node_id": holon["assigned_node_id"], "kind": "read_artifact",
             "arguments": {"artifact_id": artifact["id"]}, "rationale": "Read paper", "estimated_cost": 0}
    first = await worker.dispatch(order, holon, project)
    assert "<html>" not in first["summary"] and "invisible script" not in first["summary"]
    assert first["next_offset"] == 2600 and first["total_characters"] > 7000
    order["arguments"]["query"] = "Ablation results"
    found = await worker.dispatch(order, holon, project)
    assert "matched control scored 42" in found["summary"]
    assert found["offset"] > 6000 and found["next_offset"] is None


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required for real execution")
async def test_experiment_timeout_is_recorded_as_timeout_evidence(worker_app):
    _, store, worker, _ = worker_app
    project, holon = create_project(worker_app, mode="yolo", experiment_timeout=1)
    order = work_order(holon, script="import time\nprint('started', flush=True)\ntime.sleep(30)", timeout=30)
    await worker.perform(job(project, holon, order))
    with store.transaction() as tx:
        experiment = tx.list("experiments", project["id"])[0]
        assert experiment["status"] == "timed_out"
        assert experiment["result"]["timed_out"] and not experiment["result"]["cancelled"]
        evidence = tx.list("evidence", project["id"])[0]
        assert evidence["scope"]["status"] == "timed_out"
        assert evidence["scope"]["timed_out"]


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required for real execution")
async def test_project_pause_finishes_bounded_experiment_and_keeps_provenance(worker_app):
    client, store, worker, _ = worker_app
    project, holon = create_project(worker_app, mode="yolo", experiment_timeout=30)
    order = work_order(holon, script="from pathlib import Path\nimport time\nPath('started.txt').write_text('started')\ntime.sleep(1)\nprint('finished bounded action')", timeout=10)
    task = asyncio.create_task(worker.perform(job(project, holon, order)))
    try:
        for _ in range(200):
            with store.transaction() as tx:
                experiments = tx.list("experiments", project["id"])
            if experiments and experiments[0].get("workspace") and (Path(experiments[0]["workspace"]) / "started.txt").exists():
                break
            if task.done():
                await task
                pytest.fail("Experiment finished before pause could be tested")
            await asyncio.sleep(0.025)
        else:
            pytest.fail("Experiment did not start in time")
        assert client.post(f"/projects/{project['id']}/pause").status_code == 200
        await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    with store.transaction() as tx:
        experiment = tx.list("experiments", project["id"])[0]
        assert experiment["status"] == "completed"
        assert not experiment["result"]["cancelled"] and not experiment["result"]["timed_out"]
        assert "finished bounded action" in experiment["result"]["stdout"]
        evidence = tx.list("evidence", project["id"])[0]
        assert not evidence["scope"]["cancelled"]
        assert evidence["artifact_ids"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "network", "expected"),
    [
        ("process", False, {"execute_host", "access_external_files"}),
        ("docker", True, {"execute_container", "access_external_files", "network_container"}),
    ],
)
async def test_capability_approval_remembers_all_categories_and_survives_store_reopen(
    worker_app, monkeypatch, backend, network, expected
):
    client, store, worker, directory = worker_app
    project, holon = create_project(worker_app, execution_backend=backend)
    order = work_order(holon)
    order["arguments"].update(source_dir=str(directory / "external-repository"), allow_network=network)
    result = await worker.dispatch(order, holon, project)
    assert result["status"] == "awaiting_permission"
    with store.transaction() as tx:
        attention = tx.get("attention_items", result["attention_id"])
        assert set(attention["categories"]) == expected
        assert attention["work_order"] == order
    response = client.post(f"/attention/{attention['id']}/respond", json={"approve": True, "remember": True})
    assert response.status_code == 200, response.text
    reopened = Store(str(store.engine.url))
    reopened.initialize()
    calls = []
    async def record_dispatch(self, action, researcher, campaign):
        calls.append((action, researcher["id"], campaign["id"]))
        return {"summary": "Test dispatcher reached after capability evaluation", "cost_usd": 0}
    monkeypatch.setattr(Worker, "experiment", record_dispatch)
    try:
        with reopened.transaction() as tx:
            assert {grant["category"] for grant in tx.list("permission_grants", project["id"])} == expected
        restarted = Worker(reopened, directory, EmptyVault())
        changed_order = copy.deepcopy(order)
        changed_order["arguments"]["command"] = ["not-executed-by-test", "different arguments"]
        result = await restarted.dispatch(changed_order, holon, project)
        assert "Test dispatcher" in result["summary"] and len(calls) == 1
        # Remembering a category in one project never authorizes another project.
        other, other_holon = create_project(worker_app, execution_backend=backend)
        other_order = {**changed_order, "node_id": other_holon["assigned_node_id"]}
        result = await restarted.dispatch(other_order, other_holon, other)
        assert result["status"] == "awaiting_permission" and len(calls) == 1
    finally:
        reopened.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required for preparation")
@pytest.mark.parametrize(
    ("patch", "expected_error"),
    [
        ({"files": {"../escape.py": "print('must not run')"}}, "WorkspaceViolation"),
        ({"timeout_seconds": "private-invalid-timeout-value"}, "ValueError"),
    ],
)
async def test_preparation_failure_leaves_terminal_experiment_record(worker_app, patch, expected_error):
    _, store, worker, directory = worker_app
    project, holon = create_project(worker_app, mode="yolo")
    order = work_order(holon)
    order["arguments"].update(patch)
    await worker.perform(job(project, holon, order))
    with store.transaction() as tx:
        experiments = tx.list("experiments", project["id"])
        assert len(experiments) == 1
        experiment = experiments[0]
        assert experiment["status"] == "failed"
        assert experiment["finished_at"] and experiment["error_type"] == expected_error
        assert "private-invalid-timeout-value" not in str(experiment)
        events = tx.history(project["id"])
        failures = [event for event in events if event["type"] == "EXPERIMENT_FAILED"]
        assert len(failures) == 1 and failures[0]["payload"]["error_type"] == expected_error
        assert not any(event["type"] == "EXPERIMENT_STARTED" for event in events)
        assert not tx.list("evidence", project["id"])
    assert not (directory / "escape.py").exists()


@pytest.mark.asyncio
async def test_cancellation_during_preparation_records_interrupted(worker_app, monkeypatch):
    from sapling.integrations.execution import LocalProcessBackend
    _, store, worker, _ = worker_app
    project, holon = create_project(worker_app, mode="yolo")
    preparing = asyncio.Event()
    async def wait_in_preparation(self, *args, **kwargs):
        preparing.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(LocalProcessBackend, "create_workspace", wait_in_preparation)
    task = asyncio.create_task(worker.experiment(work_order(holon), holon, project))
    await preparing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with store.transaction() as tx:
        experiment = tx.list("experiments", project["id"])[0]
        assert experiment["status"] == "interrupted"
        assert experiment["finished_at"] and experiment["error_type"] == "CancelledError"
        assert any(event["type"] == "EXPERIMENT_INTERRUPTED" for event in tx.history(project["id"]))
