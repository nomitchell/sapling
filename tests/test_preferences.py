from fastapi.testclient import TestClient

from sapling.api import create_app
from sapling.store import Store


def test_human_preference_export_preserves_real_input(tmp_path):
    store = Store("sqlite:///:memory:")
    with TestClient(create_app(store, data_dir=tmp_path, workers=False)) as client:
        project = client.post("/projects", json={"title": "Unspecified research"}).json()
        node = client.get(f"/projects/{project['id']}/tree").json()[0]
        reply = client.post(f"/nodes/{node['id']}/prioritize", json={})
        assert reply.status_code == 200
        import json
        record = json.loads(client.get(f"/projects/{project['id']}/training-data/export").text)
        override = record["human_override"]
        assert override["preferred_node_id"] == node["id"]
        assert record["model_ranking"] == []  # Never invent a model comparison.
        with store.transaction() as tx:
            original = tx.get("human_inputs", override["human_input_id"])
            assert original["text"] == override["text"]
            assert len(tx.jobs(project["id"])) == 1
