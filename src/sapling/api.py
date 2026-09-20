from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import DATA_DIR, DATABASE_URL, ProjectSettings
from .credentials import CredentialVault
from .model_catalog import merge_settings
from .store import Store, now


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CreateProject(Input):
    title: str = Field(min_length=1, max_length=200)
    settings: dict | None = None


class PatchProject(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=20000)
    settings: dict | None = None


class MessageInput(Input):
    text: str = Field(min_length=1, max_length=40000)
    node_ids: list[str] = Field(default_factory=list, max_length=16)
    attention_ids: list[str] = Field(default_factory=list, max_length=16)


class CredentialInput(Input):
    provider: Literal["openai", "openalex", "tavily", "baseten"]
    key: str = Field(min_length=5, max_length=1000)


class AttentionResponse(Input):
    response: str = Field(default="", max_length=40000)
    approve: bool = False
    remember: bool = False


def require(tx, kind, id):
    record = tx.get(kind, id)
    if record is None:
        raise HTTPException(404, f"{kind.replace('_', ' ').rstrip('s').title()} not found")
    return record


def global_settings(tx):
    record = tx.get("settings", "global")
    return record["values"] if record else ProjectSettings().model_dump()


def wake(tx, project, holon_id=None):
    hid = holon_id or project["root_holon_id"]
    return tx.enqueue(project["id"], hid, "turn", {})


def record_conversation(tx, project, text, node_ids=None, attention_ids=None):
    """A message authorizes one bounded request, never a continuous campaign."""
    from .runtime import terminate_conversation_scope, update_request
    pid = project["id"]
    node_ids, attention_ids = list(node_ids or []), list(attention_ids or [])
    for identifier in re.findall(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", text):
        for kind, refs in (("research_nodes", node_ids), ("attention_items", attention_ids)):
            item = tx.get(kind, identifier)
            if item and item["project_id"] == pid and identifier not in refs:
                refs.append(identifier)
    for kind, refs in (("research_nodes", node_ids), ("attention_items", attention_ids)):
        for identifier in refs:
            item = require(tx, kind, identifier)
            if item["project_id"] != pid:
                raise HTTPException(422, "References must belong to this project")
    for identifier in attention_ids:
        item = tx.get("attention_items", identifier)
        owner = tx.get("holons", item.get("holon_id"))
        nid = item.get("node_id") or (owner or {}).get("assigned_node_id")
        if nid and nid not in node_ids:
            node_ids.append(nid)
    human = tx.create("human_inputs", {"project_id": pid, "text": text,
        "node_ids": node_ids, "attention_ids": attention_ids})
    request_id = project.get("active_conversation_id")
    request = project.get("conversation_requests", {}).get(request_id)
    if not request or request.get("state") != "active":
        if request_id and request:
            terminate_conversation_scope(tx, pid, "conversation:" + request_id)
        request_id = str(uuid4())
        requests = dict(project.get("conversation_requests", {}))
        available = max(0, project["budget_total"] - project.get("budget_spent", 0) - project.get("budget_reserved", 0))
        requests[request_id] = {"id": request_id, "state": "active", "control_epoch": 0,
            "context_epoch": 0, "budget_total": min(3.0, available), "budget_spent": 0,
            "budget_reserved": 0, "model_calls": 0, "max_model_calls": 48,
            "tool_calls": 0, "max_tool_calls": 32, "tool_history": [],
            "latest_human_input_id": human["id"]}
        tx.update("projects", pid, {"active_conversation_id": request_id, "conversation_requests": requests})
    else:
        update_request(tx, pid, "conversation:" + request_id, {
            "latest_human_input_id": human["id"],
            "context_epoch": request.get("context_epoch", 0) + 1,
            "budget_total": max(request.get("budget_total", 0), min(3.0, project["budget_total"])),
            "max_model_calls": max(request.get("max_model_calls", 0), 48),
            "max_tool_calls": max(request.get("max_tool_calls", 0), 32),
        })
    message = tx.create("messages", {"project_id": pid, "role": "user", "text": text,
        "human_input_id": human["id"], "node_ids": node_ids, "attention_ids": attention_ids,
        "work_scope": "conversation:" + request_id})
    root = tx.get("holons", project["root_holon_id"])
    for item in tx.list("attention_items", pid, type="decision_rejected", status="pending"):
        if item.get("holon_id") == root["id"]:
            tx.update("attention_items", item["id"], {
                "status": "resolved",
                "resolution": "Superseded by the next conversational turn",
                "resolved_at": now(),
            })
            tx.event(pid, "ATTENTION_SUPERSEDED", {"attention_id": item["id"]})
    from .runtime import refresh_blockers
    root = refresh_blockers(tx, pid, root["id"])
    tx.update("holons", root["id"], {"chat_stopped": False, "model_retry_count": 0,
        "empty_turn_count": 0, "decision_retry_count": 0, "runtime_feedback": None})
    tx.event(pid, "HUMAN_INPUT", {"message_id": message["id"], "human_input_id": human["id"],
        "node_ids": node_ids, "attention_ids": attention_ids, "work_scope": "conversation:" + request_id})
    tx.enqueue(pid, root["id"], "turn", {"reason": "human_input", "work_scope": "conversation:" + request_id})
    return message


def _coalesce_live_events(events: list[dict]) -> list[dict]:
    """Keep one renderable model snapshot per stream in an SSE polling batch."""
    latest_stream: dict[str, dict] = {}
    visible: list[dict] = []
    for event in events:
        stream_id = str(event.get("payload", {}).get("stream_id", ""))
        if event.get("type") == "MODEL_STREAM" and stream_id:
            latest_stream[stream_id] = event
            continue
        if event.get("type") == "MODEL_TURN" and stream_id in latest_stream:
            visible.append(latest_stream.pop(stream_id))
        visible.append(event)
    visible.extend(latest_stream.values())
    return sorted(visible, key=lambda event: int(event["id"]))


def create_app(store: Store | None = None, *, data_dir: Path | None = None, workers=True, vault=None):
    store = store or Store(DATABASE_URL)
    data_dir = (data_dir or DATA_DIR).resolve()
    vault = vault or CredentialVault()

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        data_dir.mkdir(parents=True, exist_ok=True)
        tasks = []
        if workers:
            from .worker import Worker

            app.state.worker = Worker(store, data_dir, vault)
            tasks = [asyncio.create_task(app.state.worker.serve())]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Sapling", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.data_dir, app.state.vault = store, data_dir, vault
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET"],
        allow_headers=["Last-Event-ID", "Cache-Control"],
    )

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        }
        if origin and origin not in allowed:
            return JSONResponse(
                {"detail": "This local API accepts requests only from Sapling."}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health")
    def health():
        with store.transaction() as tx:
            tx.list("settings")
        return {
            "status": "ok",
            "model_configured": bool(vault.get("openai")),
            "database": store.engine.dialect.name,
        }

    @app.get("/settings")
    def get_settings():
        with store.transaction() as tx:
            return global_settings(tx)

    @app.patch("/settings")
    def patch_settings(patch: dict):
        with store.transaction() as tx:
            values = ProjectSettings.model_validate(merge_settings(global_settings(tx), patch)).model_dump()
            if tx.get("settings", "global"):
                tx.update("settings", "global", {"values": values})
            else:
                tx.create("settings", {"id": "global", "values": values})
            return values

    @app.get("/models")
    async def model_catalog():
        import time

        cached = getattr(app.state, "model_catalog", None)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        from .model_catalog import account_catalog
        result = await account_catalog(vault)
        app.state.model_catalog = (time.monotonic(), result)
        return result

    @app.get("/credentials")
    def credentials():
        return [
            {"id": provider, "provider": provider, "configured": bool(vault.get(provider))}
            for provider in ("openai", "openalex", "tavily", "baseten")
        ]

    @app.post("/credentials")
    def set_credential(body: CredentialInput):
        try:
            vault.set(body.provider, body.key)
        except Exception as exc:
            # Never echo a key or platform exception that may contain one.
            raise HTTPException(
                503, "Could not save this key in the operating-system credential vault."
            ) from exc
        app.state.model_catalog = None
        return {"id": body.provider, "provider": body.provider, "configured": True}

    @app.delete("/credentials/{provider}")
    def delete_credential(provider: Literal["openai", "openalex", "tavily", "baseten"]):
        vault.delete(provider)
        app.state.model_catalog = None
        return {"configured": bool(vault.get(provider)), "environment_fallback": bool(vault.get(provider))}

    @app.get("/projects")
    def projects():
        with store.transaction() as tx:
            return [p for p in tx.list("projects") if p["status"] != "archived"]

    @app.post("/projects", status_code=201)
    def create_project(body: CreateProject):
        with store.transaction() as tx:
            settings = ProjectSettings.model_validate(
                merge_settings(global_settings(tx), body.settings or {})
            ).model_dump()
            pid, hid, nid = (str(uuid4()) for _ in range(3))
            goal = ""
            project = tx.create(
                "projects",
                {
                    "id": pid,
                    "title": body.title.strip(),
                    "goal": goal,
                    "status": "active",
                    "research_state": "planning",
                    "research_epoch": 0,
                    "research_invitation": None,
                    "conversation_requests": {},
                    "active_conversation_id": None,
                    "settings": settings,
                    "budget_total": settings["budget_total"],
                    "budget_spent": 0.0,
                    "budget_reserved": 0.0,
                    "root_holon_id": hid,
                    "evidence_epoch": 0,
                    "control_epoch": 0,
                },
            )
            tx.create(
                "holons",
                {
                    "id": hid,
                    "project_id": pid,
                    "parent_id": None,
                    "role": "converse",
                    "work_scope": "converse",
                    "goal": goal,
                    "summary": "",
                    "assigned_node_id": nid,
                    "budget_total": settings["budget_total"],
                    "budget_remaining": settings["budget_total"],
                    "budget_reserved": 0.0,
                    "depth": 0,
                    "status": "active",
                    "coordinator_session_id": None,
                    "control_epoch": 0,
                },
            )
            tx.create(
                "research_nodes",
                {
                    "id": nid,
                    "project_id": pid,
                    "parent_id": None,
                    "owning_holon_id": hid,
                    "title": "Converse",
                    "direction": goal,
                    "rationale": "Direction develops through conversation",
                    "interpretation": "",
                    "status": "active",
                    "visits": 0,
                    "budget_spent": 0,
                    # A neutral prior lets the first conversational turn use the
                    # root direction without inventing a comparative branch score.
                    "value_estimate": 0.5,
                    "value_confidence": 0.0,
                    "evidence_epoch": 0,
                    "estimated_cost": 1,
                },
            )
            tx.event(pid, "PROJECT_CREATED", {"project_id": pid})
            tx.event(pid, "HOLON_CREATED", {"holon_id": hid})
            return project

    @app.get("/projects/{pid}")
    def get_project(pid: str):
        with store.transaction() as tx:
            return require(tx, "projects", pid)

    @app.patch("/projects/{pid}")
    def patch_project(pid: str, body: PatchProject):
        with store.transaction() as tx:
            project = require(tx, "projects", pid)
            patch = body.model_dump(exclude_none=True)
            if "settings" in patch:
                patch["settings"] = ProjectSettings.model_validate(
                    merge_settings(project["settings"], patch["settings"])
                ).model_dump()
                total = patch["settings"]["budget_total"]
                if total < project["budget_spent"] + project.get("budget_reserved", 0):
                    raise HTTPException(409, "Budget cannot be less than spent and reserved usage")
                root = require(tx, "holons", project["root_holon_id"])
                delta = total - project["budget_total"]
                if root["budget_remaining"] + delta < root.get("budget_reserved", 0):
                    raise HTTPException(409, "Return child allocations before reducing this budget")
                tx.update(
                    "holons",
                    root["id"],
                    {
                        "budget_total": root["budget_total"] + delta,
                        "budget_remaining": root["budget_remaining"] + delta,
                    },
                )
                patch["budget_total"] = total
            if "goal" in patch:
                tx.update("holons", project["root_holon_id"], {"goal": patch["goal"]})
            patch["control_epoch"] = project.get("control_epoch", 0) + 1
            project = tx.update("projects", pid, patch)
            tx.event(pid, "PROJECT_UPDATED", {"fields": list(patch)})
            return project

    @app.delete("/projects/{pid}")
    async def archive_project(pid: str, permanent: bool = False):
        with store.transaction() as tx:
            project = require(tx, "projects", pid)
            tx.update(
                "projects", pid, {"status": "archived", "control_epoch": project.get("control_epoch", 0) + 1}
            )
            tx.event(pid, "PROJECT_ARCHIVED", {})
            holon_ids = [h["id"] for h in tx.list("holons", pid)]
            for hid in holon_ids:
                tx.cancel_queued(hid)
        worker = getattr(app.state, "worker", None)
        if worker:
            await asyncio.gather(*(worker.stop_holon(hid) for hid in holon_ids))
        if permanent:
            with store.transaction() as tx:
                tx.delete_project(pid)
            return {"deleted": True}
        return {"archived": True}

    def project_control(pid: str, action: str):
        from .runtime import set_research_state
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            if p.get("status") == "archived":
                raise HTTPException(409, "This project is archived")
            if action == "resume" and not (p.get("research_invitation") or {}).get("accepted_human_input_id"):
                raise HTTPException(409, "Agree to Sapling's autoresearch invitation in Converse before starting")
            return set_research_state(tx, p, "paused" if action == "pause" else "running")

    @app.post("/projects/{pid}/pause")
    def pause_project(pid: str):
        return project_control(pid, "pause")

    @app.post("/projects/{pid}/resume")
    def resume_project(pid: str):
        return project_control(pid, "resume")

    @app.get("/projects/{pid}/messages")
    def messages(pid: str):
        with store.transaction() as tx:
            project = require(tx, "projects", pid)
            # Older runs stored tool-step narration as ordinary replies. Classify
            # it from the recorded decision, preserving the immutable transcript.
            legacy_progress = {
                item["decision"]["response"]
                for item in tx.list("decision_snapshots", pid)
                if item.get("holon_id") == project.get("root_holon_id")
                and item.get("decision", {}).get("response")
                and item["decision"].get("work_orders")
                and "progress_note" not in item["decision"]
                and "\n\n" not in item["decision"]["response"].strip()
                and not item["decision"]["response"].rstrip().endswith("?")
            }
            return [
                {**message, "channel": "progress"}
                if message.get("role") == "assistant"
                and not message.get("channel")
                and message.get("text") in legacy_progress
                else message
                for message in tx.list("messages", pid)
            ]

    @app.post("/projects/{pid}/messages", status_code=201)
    def send_message(pid: str, body: MessageInput):
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            if p["status"] == "archived":
                raise HTTPException(409, "This project is archived")
            value = body.text.strip()
            if not value:
                raise HTTPException(422, "Enter a message")
            return record_conversation(tx, p, value, body.node_ids, body.attention_ids)

    @app.post("/projects/{pid}/conversation/stop")
    async def stop_conversation(pid: str):
        from .runtime import terminate_conversation_scope, update_request
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            request_id = p.get("active_conversation_id")
            request = p.get("conversation_requests", {}).get(request_id)
            scope = "conversation:" + str(request_id)
            if request and request.get("state") == "active":
                update_request(tx, pid, scope, {"state": "cancelled", "control_epoch": request.get("control_epoch", 0) + 1})
                for h in tx.list("holons", pid):
                    tx.cancel_queued(h["id"], scope)
                terminate_conversation_scope(tx, pid, scope)
                tx.event(pid, "CONVERSATION_STOPPED", {"work_scope": scope})
        worker = getattr(app.state, "worker", None)
        if worker and request:
            await worker.stop_scope(pid, scope)
        return {"status": "stopped", "work_scope": scope, "background_research": p.get("research_state")}

    @app.post("/projects/{pid}/conversation/retry")
    def retry_conversation(pid: str):
        from .runtime import refresh_blockers, update_request
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            inputs = tx.list("human_inputs", pid)
            if not inputs:
                raise HTTPException(409, "Start with a message")
            request_id = p.get("active_conversation_id")
            request = p.get("conversation_requests", {}).get(request_id)
            if not request:
                human = inputs[-1]
                record_conversation(tx, p, human["text"], human.get("node_ids"), human.get("attention_ids"))
            else:
                scope = "conversation:" + request_id
                affected_holons: set[str] = set()
                recoverable_types = {
                    "decision_rejected",
                    "model_error",
                    "output_limit",
                    "tool_error",
                    "error",
                    "no_research_action",
                }
                for item in tx.list("attention_items", pid, status="pending"):
                    if item.get("type") in recoverable_types and item.get("work_scope") == scope:
                        tx.update("attention_items", item["id"], {
                            "status": "resolved",
                            "resolution": "Superseded by the user's retry",
                            "resolved_at": now(),
                        })
                        tx.event(pid, "ATTENTION_SUPERSEDED", {"attention_id": item["id"]})
                        if item.get("holon_id"):
                            affected_holons.add(item["holon_id"])
                update_request(tx, pid, scope, {"state": "active", "control_epoch": request.get("control_epoch", 0) + 1})
                root = require(tx, "holons", p["root_holon_id"])
                affected_holons.update(
                    holon["id"]
                    for holon in tx.list("holons", pid)
                    if holon.get("work_scope") == scope
                    and holon.get("parent_id")
                    and holon.get("status") in {"active", "blocked", "error"}
                    and not holon.get("manual_paused")
                    and not holon.get("terminated")
                )
                node = require(tx, "research_nodes", root["assigned_node_id"])
                if node.get("value_estimate") is None:
                    tx.update("research_nodes", node["id"], {
                        "value_estimate": 0.5,
                        "value_confidence": 0.0,
                        "evidence_epoch": p.get("evidence_epoch", 0),
                    })
                affected_holons.add(root["id"])
                for holon_id in affected_holons:
                    resumed = refresh_blockers(tx, pid, holon_id)
                    tx.update("holons", holon_id, {"model_retry_count": 0, "empty_turn_count": 0,
                        "decision_retry_count": 0, "runtime_feedback": None})
                    if holon_id != root["id"] and resumed.get("status") == "active":
                        tx.enqueue(pid, holon_id, "turn", {"reason": "conversation_retry", "work_scope": scope})
                tx.enqueue(pid, root["id"], "turn", {"reason": "conversation_retry", "work_scope": scope})
            tx.event(pid, "CONVERSATION_RETRIED", {})
        return {"status": "queued"}

    def listing(kind):
        def read(pid: str):
            with store.transaction() as tx:
                require(tx, "projects", pid)
                return tx.list(kind, pid)

        return read

    for endpoint, kind in {
        "tree": "research_nodes",
        "holarchy": "holons",
        "claims": "claims",
        "evidence": "evidence",
        "attention": "attention_items",
        "experiments": "experiments",
        "artifacts": "artifacts",
        "decisions": "decision_snapshots",
    }.items():
        app.add_api_route(
            f"/projects/{{pid}}/{endpoint}", listing(kind), methods=["GET"], name=f"list_{kind}"
        )

    def inspector(kind):
        def read(id: str):
            with store.transaction() as tx:
                return require(tx, kind, id)

        return read

    for endpoint, kind in {
        "holons": "holons",
        "nodes": "research_nodes",
        "claims": "claims",
        "evidence": "evidence",
        "experiments": "experiments",
        "artifacts": "artifacts",
    }.items():
        app.add_api_route(f"/{endpoint}/{{id}}", inspector(kind), methods=["GET"], name=f"inspect_{kind}")

    @app.post("/holons/{id}/{action}")
    def holon_control(id: str, action: Literal["pause", "resume"]):
        from .runtime import refresh_blockers
        with store.transaction() as tx:
            h = require(tx, "holons", id)
            p = require(tx, "projects", h["project_id"])
            descendants = {id}
            holons = tx.list("holons", p["id"])
            for _ in holons:
                descendants.update(c["id"] for c in holons if c.get("parent_id") in descendants)
            for item in holons:
                if item["id"] in descendants and item["status"] != "completed":
                    tx.update(
                        "holons",
                        item["id"],
                        {
                            "status": "paused" if action == "pause" else item["status"],
                            "manual_paused": action == "pause",
                            "control_epoch": item.get("control_epoch", 0) + 1,
                        },
                    )
                    if action == "resume":
                        refresh_blockers(tx, p["id"], item["id"])
                        wake(tx, p, item["id"])
            tx.event(p["id"], "HOLON_" + action.upper() + "D", {"holon_id": id})
            return tx.get("holons", id)

    @app.post("/nodes/{id}/prioritize")
    def prioritize_node(id: str):
        with store.transaction() as tx:
            node = require(tx, "research_nodes", id)
            p = require(tx, "projects", node["project_id"])
            guidance = f"Prioritize the research direction: {node['title']} (node {id})."
            human = tx.create(
                "human_inputs", {"project_id": p["id"], "text": guidance, "preferred_node_id": id, "node_ids": [id]}
            )
            tx.create(
                "messages",
                {"project_id": p["id"], "role": "user", "text": guidance, "human_input_id": human["id"]},
            )
            snapshots = tx.list("decision_snapshots", p["id"])
            candidates = [
                s
                for s in snapshots
                if id in s.get("allocation_ranking", []) or id in s.get("model_ranking", [])
            ]
            override = {
                "kind": "explicit_node_preference",
                "preferred_node_id": id,
                "human_input_id": human["id"],
                "text": guidance,
            }
            if candidates:
                tx.update("decision_snapshots", candidates[-1]["id"], {"human_override": override})
            else:
                tx.create(
                    "decision_snapshots",
                    {
                        "project_id": p["id"],
                        "holon_id": p["root_holon_id"],
                        "compressed_state": {"node": node},
                        "candidate_actions": [{"node_id": id}],
                        "model_ranking": [],
                        "allocation_ranking": [],
                        "human_override": override,
                        "eventual_outcome": None,
                    },
                )
            owner = require(tx, "holons", node["owning_holon_id"])
            tx.update("holons", owner["id"], {"control_epoch": owner.get("control_epoch", 0) + 1})
            tx.event(p["id"], "HUMAN_PRIORITY", {"node_id": id, "human_input_id": human["id"]})
            wake(tx, p)
            return {"status": "recorded", "human_input_id": human["id"], "node_id": id}

    @app.get("/projects/{pid}/stats")
    def stats(pid: str):
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            return {
                "budget_total": p["budget_total"],
                "budget_spent": p["budget_spent"],
                "budget_reserved": p.get("budget_reserved", 0),
                "evidence_epoch": p["evidence_epoch"],
                **{
                    kind: len(tx.list(kind, pid))
                    for kind in ("holons", "research_nodes", "claims", "evidence", "experiments")
                },
                "pending_attention": len(tx.list("attention_items", pid, status="pending")),
                "jobs": tx.jobs(pid),
            }

    @app.get("/projects/{pid}/events")
    def event_history(pid: str, after: int = 0):
        with store.transaction() as tx:
            require(tx, "projects", pid)
            return tx.history(pid, after)

    @app.get("/projects/{pid}/events/stream")
    async def event_stream(pid: str, request: Request, after: int = 0):
        with store.transaction() as tx:
            require(tx, "projects", pid)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            loop = asyncio.get_running_loop()
            last_heartbeat = loop.time()
            # Flush response headers immediately so a quiet project still
            # reports a healthy live connection in the interface.
            yield ": connected\n\n"
            while not await request.is_disconnected():
                with store.transaction() as tx:
                    batch = tx.history(pid, cursor)
                for event in _coalesce_live_events(batch):
                    yield f"id: {event['id']}\nevent: research\ndata: {json.dumps(event)}\n\n"
                if batch:
                    cursor = batch[-1]["id"]
                if not batch:
                    if loop.time() - last_heartbeat >= 15:
                        yield ": heartbeat\n\n"
                        last_heartbeat = loop.time()
                    await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/attention/{id}/read")
    def read_attention(id: str):
        from .store import now
        with store.transaction() as tx:
            item = require(tx, "attention_items", id)
            if item.get("read_at"):
                return item
            item = tx.update("attention_items", id, {"read_at": now()})
            tx.event(item["project_id"], "ATTENTION_READ", {"attention_id": id, "node_id": item.get("node_id")})
            return item

    @app.post("/attention/{id}/respond")
    def respond(id: str, body: AttentionResponse):
        with store.transaction() as tx:
            item = require(tx, "attention_items", id)
            if item.get("status") != "pending":
                raise HTTPException(409, "This attention item has already been resolved")
            p = require(tx, "projects", item["project_id"])
            if item.get("type") != "permission":
                if not body.response.strip():
                    raise HTTPException(422, "Discuss this item in Converse to resolve it")
                return record_conversation(tx, p, body.response.strip(), attention_ids=[id])
            result = tx.update(
                "attention_items",
                id,
                {
                    "status": "approved" if body.approve else "resolved",
                    "response": body.response,
                    "approved": body.approve,
                },
            )
            h_id = item.get("holon_id") or p["root_holon_id"]
            if item.get("type") == "permission" and body.approve:
                if body.remember:
                    for category in item.get("categories", [item["category"]]):
                        tx.create("permission_grants", {"project_id": p["id"], "category": category})
                from .runtime import refresh_blockers
                refresh_blockers(tx, p["id"], h_id)
                tx.enqueue(
                    p["id"],
                    h_id,
                    "work_order",
                    {"attention_id": id, "work_order": item["work_order"], "work_scope": item.get("work_scope", "research")},
                    priority=10,
                )
            else:
                if body.response:
                    human = tx.create(
                        "human_inputs", {"project_id": p["id"], "text": body.response, "attention_id": id}
                    )
                    tx.create(
                        "messages",
                        {
                            "project_id": p["id"],
                            "role": "user",
                            "text": body.response,
                            "human_input_id": human["id"],
                        },
                    )
                if item.get("decision_snapshot_id"):
                    snap = tx.get("decision_snapshots", item["decision_snapshot_id"])
                    if snap and snap["project_id"] == p["id"]:
                        tx.update("decision_snapshots", snap["id"], {"human_override": body.model_dump()})
                from .runtime import refresh_blockers
                refresh_blockers(tx, p["id"], h_id)
                tx.enqueue(p["id"], h_id, "turn", {"reason": "permission_declined", "work_scope": item.get("work_scope", "research")})
            tx.event(p["id"], "ATTENTION_RESOLVED", {"attention_id": id, "approved": body.approve})
            return result

    @app.post("/projects/{pid}/artifacts", status_code=201)
    async def upload(pid: str, file: UploadFile = File(...)):
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
        content = await file.read(25 * 1024 * 1024 + 1)
        await file.close()
        if len(content) > 25 * 1024 * 1024:
            raise HTTPException(413, "Maximum document size is 25 MB")
        filename = Path(file.filename or "document").name
        ext = Path(filename).suffix.lower()
        if ext not in {".pdf", ".txt", ".md", ".csv", ".json", ".html", ".htm"}:
            raise HTTPException(422, "Supported documents: PDF, text, Markdown, CSV, JSON, HTML")
        from .worker import save_artifact

        artifact = save_artifact(
            store, data_dir, pid, content, filename, "source", {"origin": "human_upload", "private": True}
        )
        if ext == ".pdf":
            from io import BytesIO

            from pypdf import PdfReader

            try:
                extracted = "\n".join(
                    (page.extract_text() or "") for page in PdfReader(BytesIO(content)).pages
                )[:200000]
            except Exception:
                extracted = "Text extraction failed. Original PDF is retained."
        elif ext in {".html", ".htm"}:
            from bs4 import BeautifulSoup

            extracted = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)[:200000]
        else:
            extracted = content.decode("utf-8", errors="replace")[:200000]
        text_artifact = save_artifact(
            store,
            data_dir,
            pid,
            extracted.encode(),
            filename + ".txt",
            "extracted_text",
            {"source_artifact_id": artifact["id"]},
        )
        with store.transaction() as tx:
            root = require(tx, "holons", p["root_holon_id"])
            evidence = tx.create(
                "evidence",
                {
                    "project_id": pid,
                    "type": "source",
                    "summary": f"Uploaded source: {filename}\n{extracted[:6000]}",
                    "scope": {"source": filename, "private": True},
                    "producer_holon_id": root["id"],
                    "producer_node_id": root["assigned_node_id"],
                    "artifact_ids": [artifact["id"], text_artifact["id"]],
                },
            )
            latest = require(tx, "projects", pid)
            tx.update("projects", pid, {"evidence_epoch": latest["evidence_epoch"] + 1})
            tx.event(pid, "EVIDENCE_PUBLISHED", {"evidence_id": evidence["id"]})
            wake(tx, latest)
            tx.enqueue(pid, latest["root_holon_id"], "route_evidence", {"evidence_id": evidence["id"]})
        return artifact

    @app.get("/artifacts/{id}/download")
    def download(id: str):
        with store.transaction() as tx:
            record = require(tx, "artifacts", id)
        path = (data_dir / record["uri"]).resolve()
        if not path.is_relative_to(data_dir / "artifacts") or not path.is_file():
            raise HTTPException(404, "Artifact content unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise HTTPException(409, "Artifact hash does not match the recorded evidence")
        return FileResponse(path, filename=record.get("filename", id), media_type="application/octet-stream")

    @app.get("/projects/{pid}/training-data/stats")
    def training_stats(pid: str):
        with store.transaction() as tx:
            require(tx, "projects", pid)
            records = tx.list("decision_snapshots", pid)
        return {
            "decisions": len(records),
            "human_overrides": sum(bool(r.get("human_override")) for r in records),
            "outcomes": sum(bool(r.get("eventual_outcome")) for r in records),
        }

    @app.get("/projects/{pid}/training-data/export")
    def export_training(pid: str):
        with store.transaction() as tx:
            require(tx, "projects", pid)
            records = tx.list("decision_snapshots", pid)
        return StreamingResponse(
            (json.dumps(r) + "\n" for r in records),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="sapling-{pid}-decisions.jsonl"'},
        )

    return app


app = create_app()
