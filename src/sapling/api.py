from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import DATA_DIR, DATABASE_URL, ProjectSettings
from .credentials import CredentialVault
from .model_catalog import DEFAULT_MODEL, MODELS, merge_settings
from .store import Store


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


class CredentialInput(Input):
    provider: Literal["openai", "openalex", "tavily"]
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
    holon = tx.get("holons", hid)
    if holon and holon["status"] in {"blocked", "error", "awaiting_permission", "completed"}:
        tx.update("holons", hid, {"status": "active"})
        assigned = tx.get("research_nodes", holon["assigned_node_id"])
        if assigned and assigned["status"] in {"completed", "abandoned"}:
            tx.update("research_nodes", assigned["id"], {"status": "active"})
    return tx.enqueue(project["id"], hid, "turn", {})


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
        available = None
        error = None
        if vault.get("openai"):
            from openai import AsyncOpenAI

            try:
                async with AsyncOpenAI(api_key=vault.get("openai"), timeout=15, max_retries=0) as provider:
                    result = await provider.models.list()
                    available = {m.id for m in result.data}
            except Exception:
                error = "Could not refresh account model availability. Presets remain available."
        result = {
            "models": [
                m | {"available": m["id"] in available if available is not None else None} for m in MODELS
            ],
            "default_model": DEFAULT_MODEL,
            "error": error,
        }
        app.state.model_catalog = (time.monotonic(), result)
        return result

    @app.get("/credentials")
    def credentials():
        return [
            {"id": provider, "provider": provider, "configured": bool(vault.get(provider))}
            for provider in ("openai", "openalex", "tavily")
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
    def delete_credential(provider: Literal["openai", "openalex", "tavily"]):
        vault.delete(provider)
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
                    "status": "paused",
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
                    "title": "Research direction",
                    "direction": goal,
                    "rationale": "Direction develops through conversation",
                    "interpretation": "",
                    "status": "active",
                    "visits": 0,
                    "budget_spent": 0,
                    "value_estimate": None,
                    "value_confidence": None,
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
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            p = tx.update(
                "projects",
                pid,
                {
                    "status": "paused" if action == "pause" else "active",
                    "control_epoch": p.get("control_epoch", 0) + 1,
                },
            )
            if action == "resume":
                for h in tx.list("holons", pid):
                    if h["status"] not in {"paused", "completed", "awaiting_permission"}:
                        wake(tx, p, h["id"])
            tx.event(pid, "PROJECT_" + action.upper() + "D", {})
            return p

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
            if not tx.list("human_inputs", pid):
                p = tx.update("projects", pid, {"status": "active"})
            human = tx.create("human_inputs", {"project_id": pid, "text": value})
            message = tx.create(
                "messages", {"project_id": pid, "role": "user", "text": value, "human_input_id": human["id"]}
            )
            # A reply in chat resolves scientific questions in the same channel.
            # Permission approvals are explicit and never inferred from prose.
            root = require(tx, "holons", p["root_holon_id"])
            for item in tx.list("attention_items", pid, status="pending"):
                if item.get("holon_id") != root["id"] or item.get("type") == "permission":
                    continue
                tx.update(
                    "attention_items",
                    item["id"],
                    {"status": "resolved", "response": value, "human_input_id": human["id"]},
                )
                if item.get("decision_snapshot_id"):
                    tx.update(
                        "decision_snapshots",
                        item["decision_snapshot_id"],
                        {"human_override": {"human_input_id": human["id"], "text": value}},
                    )
                tx.event(pid, "ATTENTION_RESOLVED", {"attention_id": item["id"], "via": "conversation"})
            tx.update(
                "holons",
                root["id"],
                {
                    "status": "active",
                    "chat_stopped": False,
                    "model_retry_count": 0,
                    "empty_turn_count": 0,
                    "decision_retry_count": 0,
                    "runtime_feedback": None,
                    "blocked_reason": None,
                    "context_epoch": root.get("context_epoch", 0) + 1,
                },
            )
            node = require(tx, "research_nodes", root["assigned_node_id"])
            if node["status"] != "active":
                tx.update("research_nodes", node["id"], {"status": "active"})
            tx.event(pid, "HUMAN_INPUT", {"message_id": message["id"], "human_input_id": human["id"]})
            wake(tx, p)
            return message

    @app.post("/projects/{pid}/conversation/stop")
    async def stop_conversation(pid: str):
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            h = require(tx, "holons", p["root_holon_id"])
            tx.update(
                "holons", h["id"], {"chat_stopped": True, "control_epoch": h.get("control_epoch", 0) + 1}
            )
            tx.cancel_queued(h["id"])
            tx.event(pid, "CONVERSATION_STOPPED", {"holon_id": h["id"]})
        worker = getattr(app.state, "worker", None)
        if worker:
            await worker.stop_holon(h["id"])
        return {"status": "stopped", "background_research": p["status"]}

    @app.post("/projects/{pid}/conversation/retry")
    def retry_conversation(pid: str):
        with store.transaction() as tx:
            p = require(tx, "projects", pid)
            if not tx.list("messages", pid):
                raise HTTPException(409, "Start with a message")
            h = require(tx, "holons", p["root_holon_id"])
            tx.update(
                "holons",
                h["id"],
                {
                    "status": "active",
                    "chat_stopped": False,
                    "model_retry_count": 0,
                    "empty_turn_count": 0,
                    "decision_retry_count": 0,
                    "runtime_feedback": None,
                    "blocked_reason": None,
                    "context_epoch": h.get("context_epoch", 0) + 1,
                },
            )
            tx.update("research_nodes", h["assigned_node_id"], {"status": "active"})
            for item in tx.list("attention_items", pid, status="pending"):
                if item.get("holon_id") == h["id"] and item.get("type") != "permission":
                    tx.update(
                        "attention_items",
                        item["id"],
                        {"status": "resolved", "resolution": "Retried from conversation"},
                    )
            wake(tx, p)
            tx.event(pid, "CONVERSATION_RETRIED", {"holon_id": h["id"]})
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
                            "status": "paused" if action == "pause" else "active",
                            "control_epoch": item.get("control_epoch", 0) + 1,
                        },
                    )
                    if action == "resume":
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
                "human_inputs", {"project_id": p["id"], "text": guidance, "preferred_node_id": id}
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
            tx.update(
                "projects",
                p["id"],
                {"control_epoch": p.get("control_epoch", 0) + 1, "evidence_epoch": p["evidence_epoch"] + 1},
            )
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
            idle = 0
            while not await request.is_disconnected():
                with store.transaction() as tx:
                    batch = tx.history(pid, cursor)
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: research\ndata: {json.dumps(event)}\n\n"
                if not batch:
                    idle += 1
                    if idle % 15 == 0:
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/attention/{id}/respond")
    def respond(id: str, body: AttentionResponse):
        with store.transaction() as tx:
            item = require(tx, "attention_items", id)
            if item.get("status") != "pending":
                raise HTTPException(409, "This attention item has already been resolved")
            p = require(tx, "projects", item["project_id"])
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
                tx.update("holons", h_id, {"status": "active"})
                tx.enqueue(
                    p["id"],
                    h_id,
                    "work_order",
                    {"attention_id": id, "work_order": item["work_order"]},
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
                target = tx.get("holons", h_id)
                if target and target["status"] == "paused" and item.get("pauses_subtree"):
                    tx.update(
                        "holons",
                        h_id,
                        {"status": "active", "control_epoch": target.get("control_epoch", 0) + 1},
                    )
                wake(tx, p, h_id)
            tx.update("projects", p["id"], {"control_epoch": p.get("control_epoch", 0) + 1})
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
