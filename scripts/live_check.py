"""Explicit, bounded model integration check; never part of the offline suite.

Run manually with --confirm-live. Reads the OS vault, never prints credentials.
Uses an isolated test database and does not seed a user's research workspace.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from sapling.api import create_app
from sapling.credentials import CredentialVault
from sapling.store import Store
from sapling.worker import Worker


async def check(root):
    store = Store("sqlite:///" + (root / "validation.sqlite3").as_posix())
    app = create_app(store, data_dir=root, workers=False)
    with TestClient(app) as client:
        project = client.post("/projects", json={"title": "Runtime integration validation", "goal": "Validate the coordinator connection without conducting research.", "settings": {"budget_total": 0.20, "max_turn_cost_usd": 0.10, "max_output_tokens": 4096, "max_concurrent_holons": 1}}).json()
        assert "id" in project, project
        client.post(f"/projects/{project['id']}/messages", json={"text": "This is a connection integration test, not a research task. Reply briefly that you can receive this message. Return no branches, work orders, claims, evidence, child holons, or attention. Complete this one-off check with a concise completion summary."})
        worker = Worker(store, root, CredentialVault())
        job = store.claim("live-check")
        assert job
        await worker._perform_leased(job, "live-check")
        with store.transaction() as tx:
            p = tx.get("projects", project["id"])
            messages = tx.list("messages", p["id"])
            events = tx.history(p["id"], limit=1000)
            report = {"project_id": p["id"], "model": p["settings"]["model"], "reasoning_effort": p["settings"]["reasoning_effort"], "cost_usd": p["budget_spent"], "assistant_messages": [m["text"] for m in messages if m["role"] == "assistant"], "model_events": [e for e in events if e["type"] in {"MODEL_TURN", "MODEL_ERROR", "DECISION_REJECTED"}], "holon_status": tx.get("holons", p["root_holon_id"])["status"]}
            tx.update("projects", p["id"], {"status": "paused"})
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        assert report["assistant_messages"], "No live assistant response; inspect recorded events"
        assert report["cost_usd"] <= 0.20, "Live check exceeded its project budget"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("Use --confirm-live to authorize a model call capped by a $0.20 project budget")
    root = (Path(".sapling") / ("live-check-" + str(uuid4()))).resolve()
    root.mkdir(parents=True)
    asyncio.run(check(root))
