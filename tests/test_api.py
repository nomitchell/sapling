import hashlib

import pytest
from fastapi.testclient import TestClient

from sapling.api import create_app
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


def test_project_bootstrap_chat_and_isolation(app_client):
    client, store = app_client
    p, other = project(client), project(client, title="Independent")
    h = client.get(f"/projects/{p['id']}/holarchy").json()
    tree = client.get(f"/projects/{p['id']}/tree").json()
    assert len(h) == len(tree) == 1
    assert h[0]["assigned_node_id"] == tree[0]["id"]
    r = client.post(f"/projects/{p['id']}/messages", json={"text": "Investigate this open question"})
    assert r.status_code == 201, r.text
    assert client.get(f"/projects/{other['id']}/messages").json() == []
    with store.transaction() as tx:
        assert tx.get("projects", p["id"])["control_epoch"] == 1
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
    assert client.post(f"/projects/{p['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/projects/{p['id']}/resume").json()["status"] == "active"
    assert client.delete(f"/projects/{p['id']}").json()["archived"]
    assert client.get("/projects").json() == []
    with store.transaction() as tx:
        assert tx.get("projects", p["id"])["status"] == "archived"


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
    client.post(f"/projects/{p['id']}/resume")
    with store.transaction() as tx:
        tx.enqueue(p["id"], p["root_holon_id"], "turn", {})
    first = store.claim("worker1", lease_seconds=-1)
    assert first
    assert store.claim("worker2") is None
    with store.transaction() as tx:
        assert tx.jobs(p["id"])[0]["state"] == "interrupted"
        assert tx.list("attention_items", p["id"])[0]["type"] == "interrupted_job"
