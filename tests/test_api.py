import hashlib

import pytest
from fastapi.testclient import TestClient

from sapling.api import _coalesce_live_events, create_app
from sapling.store import Store


class Vault:
    def __init__(self):
        self.data = {}

    def get(self, provider):
        return self.data.get(provider)

    def set(self, provider, key):
        self.data[provider] = key

    def delete(self, provider):
        self.data.pop(provider, None)


@pytest.fixture
def app_client(tmp_path):
    store = Store("sqlite:///:memory:")
    app = create_app(store, data_dir=tmp_path, workers=False, vault=Vault())
    with TestClient(app) as client:
        yield client, store


def project(client, **kwargs):
    response = client.post("/projects", json={"title": "Research", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def test_live_event_batch_keeps_latest_stream_snapshot_before_completion():
    events = [
        {"id": 10, "type": "MODEL_STREAM", "payload": {"stream_id": "9", "output_tokens": 2}},
        {"id": 11, "type": "MODEL_STREAM", "payload": {"stream_id": "9", "output_tokens": 18}},
        {"id": 12, "type": "MODEL_TURN", "payload": {"stream_id": "9"}},
        {"id": 13, "type": "JOB_FINISHED", "payload": {}},
    ]

    visible = _coalesce_live_events(events)

    assert [(event["id"], event["type"]) for event in visible] == [
        (11, "MODEL_STREAM"),
        (12, "MODEL_TURN"),
        (13, "JOB_FINISHED"),
    ]


def test_project_bootstrap_chat_and_isolation(app_client):
    client, store = app_client
    p, other = project(client), project(client, title="Independent")
    h = client.get(f"/projects/{p['id']}/holarchy").json()
    tree = client.get(f"/projects/{p['id']}/tree").json()
    assert len(h) == len(tree) == 1
    assert h[0]["assigned_node_id"] == tree[0]["id"]
    assert tree[0]["title"] == "Converse"
    assert tree[0]["value_estimate"] == 0.5
    assert tree[0]["value_confidence"] == 0.0
    r = client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate this open question"})
    assert r.status_code == 201, r.text
    assert client.get(f"/projects/{other['id']}/messages").json() == []
    with store.transaction() as tx:
        project_state = tx.get("projects", p["id"])
        request = project_state["conversation_requests"][project_state["active_conversation_id"]]
        assert request["context_epoch"] == 0
        assert request["max_model_calls"] == 24
        assert request["max_tool_calls"] == 16
        assert request["budget_total"] == 3
        assert tx.get("projects", p["id"])["control_epoch"] == 0
        assert len(tx.list("human_inputs", p["id"])) == 1
        assert len(tx.jobs(p["id"])) == 1


def test_local_origin_and_settings_validation(app_client):
    client, _ = app_client
    assert (
        client.post(
            "/projects", json={"title": "Injected"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/settings", headers={"Host": "attacker.example"}).status_code == 400
    assert client.patch("/settings", json={"api_key": "secret"}).status_code == 422
    assert client.patch("/settings", json={"cadence": 1.5}).status_code == 422
    assert client.patch("/settings", json={"permission_mode": "yolo"}).json()["permission_mode"] == "yolo"


def test_credentials_never_return_secrets(app_client):
    client, _ = app_client
    assert (
        client.post("/credentials", json={"provider": "openai", "key": "not-a-real-key"}).status_code == 200
    )
    assert "not-a-real-key" not in client.get("/credentials").text
    assert "not-a-real-key" not in client.get("/settings").text


def test_artifact_upload_hash_and_append_only(app_client):
    client, store = app_client
    p = project(client)
    r = client.post(
        f"/projects/{p['id']}/artifacts", files={"file": ("notes.md", b"Observed result", "text/markdown")}
    )
    assert r.status_code == 201, r.text
    artifact = r.json()
    assert artifact["sha256"] == hashlib.sha256(b"Observed result").hexdigest()
    assert client.get(f"/artifacts/{artifact['id']}/download").content == b"Observed result"
    with store.transaction() as tx:
        e = tx.list("evidence", p["id"])[0]
        with pytest.raises(ValueError, match="append-only"):
            tx.update("evidence", e["id"], {"summary": "Tampered"})


def test_pause_resume_and_archive(app_client):
    client, store = app_client
    p = project(client)
    assert client.post(f"/projects/{p['id']}/pause").json()["research_state"] == "paused"
    assert client.post(f"/projects/{p['id']}/resume").status_code == 409
    with store.transaction() as tx:
        human = tx.create("human_inputs", {"project_id": p["id"], "text": "Yes, start pair research"})
        tx.update("projects", p["id"], {"research_invitation": {"id": "api-test", "accepted_human_input_id": human["id"]}})
    assert client.post(f"/projects/{p['id']}/resume").json()["research_state"] == "running"
    assert client.delete(f"/projects/{p['id']}").json()["archived"]
    assert client.get("/projects").json() == []
    with store.transaction() as tx:
        assert tx.get("projects", p["id"])["status"] == "archived"


def test_retry_recovers_internal_decision_blocker(app_client):
    client, store = app_client
    p = project(client)
    client.post(f"/projects/{p['id']}/messages", json={"text": "Scope the literature"})
    with store.transaction() as tx:
        current = tx.get("projects", p["id"])
        scope = "conversation:" + current["active_conversation_id"]
        root = tx.get("holons", p["root_holon_id"])
        tx.update("research_nodes", root["assigned_node_id"], {"value_estimate": None})
        tx.update("holons", root["id"], {"status": "blocked", "blocked_reason": "decision_rejected"})
        item = tx.create("attention_items", {
            "project_id": p["id"], "holon_id": root["id"], "node_id": root["assigned_node_id"],
            "type": "decision_rejected", "status": "pending", "pauses_subtree": True,
            "work_scope": scope, "summary": "Internal decision failure",
        })

    assert client.post(f"/projects/{p['id']}/conversation/retry").status_code == 200
    with store.transaction() as tx:
        assert tx.get("attention_items", item["id"])["status"] == "resolved"
        assert tx.get("holons", p["root_holon_id"])["status"] == "active"
        assert tx.get("research_nodes", root["assigned_node_id"])["value_estimate"] == 0.5
        assert any(event["type"] == "ATTENTION_SUPERSEDED" for event in tx.history(p["id"]))


def test_permission_approval_cannot_be_replayed(app_client):
    client, store = app_client
    p = project(client)
    with store.transaction() as tx:
        item = tx.create(
            "attention_items",
            {
                "project_id": p["id"],
                "holon_id": p["root_holon_id"],
                "type": "permission",
                "status": "pending",
                "category": "execute_host",
                "work_order": {"kind": "run_experiment"},
            },
        )
    endpoint = f"/attention/{item['id']}/respond"
    assert client.post(endpoint, json={"approve": True, "remember": True}).status_code == 200
    assert client.post(endpoint, json={"approve": True}).status_code == 409
    with store.transaction() as tx:
        assert len(tx.list("permission_grants", p["id"])) == 1
        assert len(tx.jobs(p["id"])) == 1


def test_lease_expiry_does_not_replay_side_effect(app_client):
    client, store = app_client
    p = project(client)
    with store.transaction() as tx:
        request_id = "lease-test"
        tx.update("projects", p["id"], {"active_conversation_id": request_id,
            "budget_reserved": 0.04,
            "conversation_requests": {request_id: {"id": request_id, "state": "active",
                "budget_total": 0.2, "budget_reserved": 0.04, "budget_spent": 0}}})
        root = tx.get("holons", p["root_holon_id"])
        tx.update("holons", root["id"], {"budget_reserved": 0.04})
        tx.enqueue(p["id"], p["root_holon_id"], "turn", {"work_scope": f"conversation:{request_id}"})
    first = store.claim("worker1", lease_seconds=-1)
    assert first
    assert store.claim("worker2") is None
    with store.transaction() as tx:
        assert tx.jobs(p["id"])[0]["state"] == "interrupted"
        assert tx.list("attention_items", p["id"])[0]["type"] == "interrupted_job"
        current = tx.get("projects", p["id"])
        request = current["conversation_requests"][request_id]
        assert current["budget_reserved"] == 0 and current["budget_spent"] == 0.04
        assert tx.get("holons", p["root_holon_id"])["budget_reserved"] == 0
        assert request["budget_reserved"] == 0 and request["budget_spent"] == 0.04
        assert any(e["type"] == "RESERVATION_RECONCILED" for e in tx.history(p["id"]))
