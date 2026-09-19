from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from uuid import uuid4

from .credentials import is_placeholder
from .store import now

log = logging.getLogger(__name__)


def serialize(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def save_artifact(
    store, root: Path, project_id: str, content: bytes, filename: str, type: str, metadata=None
):
    digest = hashlib.sha256(content).hexdigest()
    path = root / "artifacts" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_name(f".{digest}-{uuid4()}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
    with store.transaction() as tx:
        record = tx.create(
            "artifacts",
            {
                "project_id": project_id,
                "type": type,
                "filename": Path(filename).name,
                "uri": path.relative_to(root).as_posix(),
                "sha256": digest,
                "byte_size": len(content),
                "metadata": metadata or {},
            },
        )
        tx.event(project_id, "ARTIFACT_CREATED", {"artifact_id": record["id"], "sha256": digest})
        return record


class Worker:
    def __init__(self, store, data_dir, vault):
        self.store, self.data_dir, self.vault = store, data_dir, vault
        self.running = {}
        self.running_scopes = {}
        self.user_stops = set()

    async def stop_holon(self, holon_id):
        task = self.running.get(holon_id)
        if task and not task.done():
            self.user_stops.add(holon_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop_scope(self, project_id, scope):
        for hid, details in list(self.running_scopes.items()):
            if details == (project_id, scope):
                await self.stop_holon(hid)

    def preflight(self, project):
        settings = project["settings"]
        provider = settings.get("provider", "openai")
        if is_placeholder(self.vault.get(provider)):
            return f"Add a {provider.title()} API key in Settings to start research. Your messages have been saved."
        if not settings.get("model"):
            return "Choose a model identifier in project Settings before starting research."
        if settings.get("input_cost_per_million", 0) <= 0 or settings.get("output_cost_per_million", 0) <= 0:
            return "Set this model's input and output prices in project Settings for dollar accounting."
        return None

    async def serve(self):
        from .retrieval import semantic_retriever
        preparation = asyncio.create_task(asyncio.to_thread(semantic_retriever(str(self.data_dir / "retrieval")).prepare, download=True))
        active = set()
        try:
            while True:
                active = {task for task in active if not task.done()}
                if len(active) < 32:
                    owner = str(uuid4())
                    job = self.store.claim(owner)
                    if job:
                        active.add(asyncio.create_task(self._perform_leased(job, owner)))
                        continue
                if active:
                    await asyncio.wait(active, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await asyncio.sleep(0.5)
        finally:
            preparation.cancel()
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)

    async def _perform_leased(self, job, owner):
        with self.store.transaction() as tx:
            if not tx.get("projects", job["project_id"]):
                return
            tx.event(
                job["project_id"],
                "JOB_STARTED",
                {"job_id": job["id"], "holon_id": job["holon_id"], "kind": job["kind"]},
            )
        self.running[job["holon_id"]] = asyncio.current_task()
        self.running_scopes[job["holon_id"]] = (job["project_id"], job["payload"].get("work_scope", "research"))
        heartbeat = asyncio.create_task(self._heartbeat(job["id"], owner))
        watcher = asyncio.create_task(self._watch_controls(job, asyncio.current_task()))
        try:
            await self.perform(job)
        except asyncio.CancelledError:
            if job["holon_id"] in self.user_stops:
                self.store.finish(job["id"], owner, cancelled=True)
                with self.store.transaction() as tx:
                    tx.event(
                        job["project_id"], "JOB_CANCELLED", {"job_id": job["id"], "holon_id": job["holon_id"]}
                    )
                return
            self.store.finish(job["id"], owner, "Application stopped during this job; inspect before retry")
            with self.store.transaction() as tx:
                tx.create(
                    "attention_items",
                    {
                        "project_id": job["project_id"],
                        "holon_id": job["holon_id"],
                        "node_id": (tx.get("holons", job["holon_id"]) or {}).get("assigned_node_id"),
                        "work_scope": job["payload"].get("work_scope", "research"),
                        "type": "interrupted_job",
                        "status": "pending",
                        "read_at": None,
                        "pauses_subtree": True,
                        "summary": "Application stopped during a research action. Review the latest logs before resuming.",
                        "job_id": job["id"],
                    },
                )
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: research job failed; inspect its action and retry after correcting configuration."
            log.warning("Job %s failed (%s)", job["id"], type(exc).__name__)
            self.store.finish(job["id"], owner, message)
            with self.store.transaction() as tx:
                tx.update("holons", job["holon_id"], {"status": "error"})
                tx.create(
                    "attention_items",
                    {
                        "project_id": job["project_id"],
                        "holon_id": job["holon_id"],
                        "node_id": (tx.get("holons", job["holon_id"]) or {}).get("assigned_node_id"),
                        "work_scope": job["payload"].get("work_scope", "research"),
                        "type": "error",
                        "status": "pending",
                        "read_at": None,
                        "pauses_subtree": True,
                        "summary": message,
                        "job_id": job["id"],
                    },
                )
                tx.event(job["project_id"], "JOB_ERROR", {"job_id": job["id"], "error": message})
        else:
            self.store.finish(job["id"], owner)
        finally:
            self.running.pop(job["holon_id"], None)
            self.running_scopes.pop(job["holon_id"], None)
            self.user_stops.discard(job["holon_id"])
            heartbeat.cancel()
            watcher.cancel()
            await asyncio.gather(heartbeat, watcher, return_exceptions=True)
            with self.store.transaction() as tx:
                tx.event(
                    job["project_id"], "JOB_FINISHED", {"job_id": job["id"], "holon_id": job["holon_id"]}
                )

    async def _heartbeat(self, job_id, owner):
        while True:
            await asyncio.sleep(20)
            self.store.heartbeat(job_id, owner)

    async def _watch_controls(self, job, task):
        from .runtime import conversation_request
        while not task.done():
            await asyncio.sleep(0.25)
            with self.store.transaction() as tx:
                project = tx.get("projects", job["project_id"])
                holon = tx.get("holons", job["holon_id"])
            request = conversation_request(project or {}, job["payload"].get("work_scope", "research"))
            if not project or not holon or holon.get("terminated") or (request and request.get("state") == "cancelled"):
                self.user_stops.add(job["holon_id"])
                task.cancel()
                return

    async def perform(self, job):
        from .runtime import CURRENT_SCOPE
        token = CURRENT_SCOPE.set(job["payload"].get("work_scope", "research"))
        try:
            return await self._perform_scoped(job)
        finally:
            CURRENT_SCOPE.reset(token)

    async def _perform_scoped(self, job):
        from .runtime import _runnable
        with self.store.transaction() as tx:
            project = tx.get("projects", job["project_id"])
            holon = tx.get("holons", job["holon_id"])
            runnable = _runnable(tx, project, holon)
        if not runnable:
            return
        if job["kind"] == "work_order":
            from .runtime import execute_work_order

            order = job["payload"]["work_order"]

            async def approved_dispatch(work_order, researcher, campaign):
                return await self.dispatch(
                    work_order, researcher, campaign, approval_id=job["payload"].get("attention_id")
                )

            result = await execute_work_order(self.store, holon["id"], order, approved_dispatch)
            with self.store.transaction() as tx:
                if result.get("status") == "completed":
                    tx.enqueue(project["id"], holon["id"], "turn", {"reason": "approved_work_completed"})
                for evidence_id in result.get("published_evidence_ids", []):
                    tx.enqueue(
                        project["id"],
                        project["root_holon_id"],
                        "route_evidence",
                        {"evidence_id": evidence_id},
                    )
            return

        problem = self.preflight(project)
        if problem:
            with self.store.transaction() as tx:
                tx.update("holons", holon["id"], {"status": "blocked"})
                pending = tx.list("attention_items", project["id"], type="configuration", status="pending")
                if not pending:
                    item = tx.create(
                        "attention_items",
                        {
                            "project_id": project["id"],
                            "holon_id": holon["id"],
                            "type": "configuration",
                            "status": "pending",
                            "summary": problem,
                        },
                    )
                    tx.event(
                        project["id"], "ATTENTION_CREATED", {"attention_id": item["id"], "summary": problem}
                    )
            return
        from .integrations.model import input_token_bound, model_runtime
        from .runtime import INSTRUCTIONS, run_turn

        settings = project["settings"]
        model = model_runtime(
            settings.get("provider", "openai"), self.vault.get(settings.get("provider", "openai")),
            settings["model"],
            settings["reasoning_effort"],
            settings["input_cost_per_million"],
            settings["output_cost_per_million"],
            settings.get("cached_input_cost_per_million"),
        )

        async def call_model(context, schema, config):
            # UTF-8 bytes upper-bound ordinary text tokenization conservatively;
            # reserve schema/instruction overhead before allowing a paid request.
            input_bound = input_token_bound(context, schema, INSTRUCTIONS)
            input_cost = input_bound * settings["input_cost_per_million"] / 1_000_000
            cap = config.get("max_cost_usd", settings["max_turn_cost_usd"])
            affordable = int(max(0, cap - input_cost) * 1_000_000 / settings["output_cost_per_million"])
            if affordable < 256:
                error = ValueError(
                    "This turn's context exceeds its dollar reservation; increase max_turn_cost_usd"
                )
                error.cost_usd = 0
                raise error
            result = await model.turn(
                context,
                schema,
                INSTRUCTIONS,
                max_output_tokens=min(settings["max_output_tokens"], affordable),
                progress=config.get("_progress_callback"),
            )
            return {
                "decision": result.decision.model_dump(mode="json"),
                "usage": result.usage,
                "cost_usd": result.cost_usd,
                "response_id": result.response_id,
            }

        try:
            if job["kind"] == "route_evidence":
                from .runtime import route_evidence

                await route_evidence(self.store, job["payload"]["evidence_id"], call_model, cache_dir=self.data_dir / "retrieval")
            else:
                await run_turn(self.store, holon["id"], call_model, self.dispatch, cache_dir=self.data_dir / "retrieval")
        finally:
            await model.close()

    async def dispatch(self, order, holon, project, *, approval_id=None):
        from .integrations.permissions import PermissionPolicy
        from .integrations.search import SearchClient, extract_text

        order = serialize(order)
        kind, args = order["kind"], order.get("arguments", {})
        pid = project["id"]
        with self.store.transaction() as tx:
            from .runtime import _runnable, work_scope
            latest = tx.get("projects", pid)
            current = tx.get("holons", holon["id"])
            node = tx.get("research_nodes", order["node_id"])
            if not _runnable(tx, latest, current):
                return {
                    "summary": "Work stopped because the project or researcher is paused.",
                    "status": "paused",
                }
            if not node or node["project_id"] != pid or node["owning_holon_id"] != holon["id"]:
                raise ValueError("Work order does not belong to this researcher")
            project = latest
            category = {
                "search_literature": "search_web",
                "search_web": "search_web",
                "read_paper": "fetch_source",
                "open_source": "fetch_source",
                "read_artifact": "read_workspace",
                "retrieve_evidence": "read_workspace",
                "run_experiment": "execute_host"
                if project["settings"]["execution_backend"] == "process"
                else "execute_container",
            }[kind]
            grants = {g["category"] for g in tx.list("permission_grants", pid)}
            categories = [category]
            if kind == "run_experiment" and args.get("source_dir"):
                categories.append("access_external_files")
            if (
                kind == "run_experiment"
                and args.get("allow_network")
                and project["settings"]["execution_backend"] == "docker"
            ):
                categories.append("network_container")
            policy = PermissionPolicy(project["settings"]["permission_mode"], grants)
            allowed = all(policy.evaluate(c).allowed for c in categories)
            if approval_id:
                approval = tx.get("attention_items", approval_id)
                if (
                    not approval
                    or approval["project_id"] != pid
                    or approval["status"] != "approved"
                    or approval["work_order"] != order
                    or approval.get("consumed")
                ):
                    raise ValueError("Permission does not match this action or has already been used")
                if set(approval.get("categories", [approval.get("category")])) == set(categories):
                    allowed = True
                    tx.update("attention_items", approval_id, {"consumed": True})
            if not allowed:
                pending = tx.list("attention_items", pid, type="permission", status="pending")
                item = next(
                    (i for i in pending if i.get("holon_id") == holon["id"] and i.get("work_order") == order),
                    None,
                )
                if item is None:
                    item = tx.create(
                        "attention_items",
                        {
                            "project_id": pid,
                            "holon_id": holon["id"],
                            "type": "permission",
                            "category": category,
                            "categories": categories,
                            "status": "pending",
                            "summary": f"Permission requested: {category.replace('_', ' ')}. {order.get('rationale', '')}",
                            "work_order": order,
                            "work_scope": work_scope(current),
                        },
                    )
                    tx.event(pid, "ATTENTION_CREATED", {"attention_id": item["id"], "category": category})
                tx.update("holons", holon["id"], {"status": "awaiting_permission"})
                return {
                    "summary": item["summary"],
                    "status": "awaiting_permission",
                    "attention_id": item["id"],
                    "cost_usd": 0,
                }
            tx.event(
                pid,
                "TOOL_STARTED",
                {
                    "holon_id": holon["id"],
                    "kind": kind,
                    "summary": order.get("rationale", ""),
                    "query": args.get("query"),
                    "url": args.get("url"),
                },
            )

        if kind in {"search_literature", "search_web", "read_paper", "open_source"}:
            async with SearchClient(
                os.environ.get("SAPLING_SEARXNG_URL", "http://127.0.0.1:8088"), self.vault.get("openalex"),
                tavily_api_key=self.vault.get("tavily"),
            ) as search:
                if kind in {"read_paper", "open_source"}:
                    source = (
                        await search.fetch_paper(args["query"], self.data_dir / "downloads")
                        if kind == "read_paper"
                        else await search.fetch_source(args["url"], self.data_dir / "downloads")
                    )
                    provenance = serialize(source)
                    provenance.pop("text", None)
                    raw = save_artifact(
                        self.store,
                        self.data_dir,
                        pid,
                        Path(source.raw_path).read_bytes(),
                        source.title or "source",
                        "source",
                        provenance,
                    )
                    txt = save_artifact(
                        self.store,
                        self.data_dir,
                        pid,
                        source.text.encode(),
                        "source.txt",
                        "extracted_text",
                        {"source_artifact_id": raw["id"]},
                    )
                    return {
                        "summary": f"Opened {source.final_url}\n{source.text[:2600]}",
                        "artifact_id": txt["id"],
                        "raw_artifact_id": raw["id"],
                        "links": source.links,
                        "total_characters": len(source.text),
                        "next_offset": 2600 if len(source.text) > 2600 else None,
                        "reading_hint": "This is an excerpt. Read artifact_id with query for a section or next_offset for the next passage. Paper-reading results are full-text artifacts when an open copy is available.",
                        "cost_usd": 0,
                        "evidence": [
                            {
                                "type": "source",
                                "summary": f"Source {source.title}: {source.text[:6000]}",
                                "scope": {"url": source.final_url},
                                "artifact_ids": [raw["id"], txt["id"]],
                            }
                        ],
                    }
                results = await getattr(search, kind)(args["query"], limit=args.get("limit", 8))
                payload = json.dumps([serialize(r) for r in results], ensure_ascii=False, indent=2)
                artifact = save_artifact(
                    self.store,
                    self.data_dir,
                    pid,
                    payload.encode(),
                    "search-results.json",
                    "search_results",
                    {"query": args["query"], "provider": kind},
                )
                # Discovery metadata is retained; it is not empirical support for a claim.
                return {
                    "summary": f"Search for {args['query']}: {len(results)} results. These are discovery metadata; open relevant sources before treating them as evidence.",
                    "results": [
                        {
                            "title": r.title,
                            "url": r.url,
                            "abstract": r.summary[:300],
                            "year": r.year,
                            "open_access_url": r.open_access_url,
                            "provider": r.provider,
                        }
                        for r in results
                    ],
                    "cost_usd": 0,
                    "artifacts": [artifact["id"]],
                    "evidence": [
                        {
                            "type": "source",
                            "summary": f"Discovery results for {args['query']} (metadata only; open and evaluate original sources):\n{payload[:8000]}",
                            "scope": {"query": args["query"], "discovery_only": True},
                            "artifact_ids": [artifact["id"]],
                        }
                    ],
                }

        if kind == "retrieve_evidence":
            with self.store.transaction() as tx:
                item = tx.get("evidence", args["evidence_id"])
                if not item or item["project_id"] != pid:
                    raise ValueError("Evidence does not belong to this project")
            return {"summary": json.dumps(item), "cost_usd": 0}
        if kind == "read_artifact":
            with self.store.transaction() as tx:
                item = tx.get("artifacts", args["artifact_id"])
                if not item or item["project_id"] != pid:
                    raise ValueError("Artifact does not belong to this project")
            path = (self.data_dir / item["uri"]).resolve()
            if not path.is_relative_to(self.data_dir / "artifacts"):
                raise ValueError("Invalid artifact path")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Artifact integrity check failed")
            content_type = item.get("metadata", {}).get("content_type", "")
            if content.startswith(b"%PDF"):
                content_type = "application/pdf"
            elif content.lstrip()[:40].lower().startswith((b"<!doctype html", b"<html")):
                content_type = "text/html"
            if content_type in {"text/html", "application/xhtml+xml", "application/pdf"}:
                _, text = await asyncio.to_thread(extract_text, content, content_type)
            else:
                text = content.decode(errors="replace")
            offset = max(0, int(args.get("offset", 0)))
            query = str(args.get("query") or "").strip()
            if query:
                folded = text.lower()
                match = folded.find(query.lower(), offset)
                if match < 0:
                    return {"summary": f"Phrase {query!r} was not found at or after offset {offset}. Try a shorter phrase or page through with offset.",
                            "artifact_id": item["id"], "total_characters": len(text), "cost_usd": 0}
                passages = []
                first_offset = max(offset, match - 100)
                end = offset
                while match >= 0 and len(passages) < 6:
                    start = max(offset, match - 100)
                    end = min(len(text), match + 300)
                    passages.append(f"[Characters {start}-{end}]\n{text[start:end]}")
                    match = folded.find(query.lower(), max(end, match + len(query)))
                return {"summary": "\n\n".join(passages), "artifact_id": item["id"],
                        "offset": first_offset, "next_offset": end if end < len(text) else None,
                        "total_characters": len(text), "cost_usd": 0}
            end = min(len(text), offset + 2600)
            return {"summary": text[offset:end], "artifact_id": item["id"],
                    "offset": offset, "next_offset": end if end < len(text) else None,
                    "total_characters": len(text), "cost_usd": 0}
        return await self.experiment(order, holon, project)

    async def experiment(self, order, holon, project):
        import time

        from .integrations.execution import LocalDockerBackend, LocalProcessBackend

        args, pid = order["arguments"], project["id"]
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("Experiment command must be a nonempty argument list")
        with self.store.transaction() as tx:
            parent_id = args.get("parent_experiment_id")
            parent_commit = args.get("parent_commit")
            if parent_id:
                parent = tx.get("experiments", parent_id)
                if not parent or parent.get("project_id") != pid:
                    raise ValueError("Parent experiment must belong to this project")
                if args.get("source_dir"):
                    raise ValueError("Choose a recorded parent snapshot or an imported directory")
                parent_commit = parent_commit or parent.get("result", {}).get("git_commit")
                if not parent_commit:
                    raise ValueError("Parent experiment has no recorded input snapshot")
            experiment = tx.create(
                "experiments",
                {
                    "project_id": pid,
                    "holon_id": holon["id"],
                    "research_node_id": order["node_id"],
                    "status": "preparing",
                    "manifest": {
                        "proposed_change": order.get("rationale", ""),
                        "prediction": args.get("prediction", ""),
                        "seed": args.get("seed", 0),
                        "command": command,
                        "parent_experiment_id": parent_id,
                        "parent_commit": parent_commit,
                        "dataset_version": args.get("dataset_version", "unspecified"),
                        "evaluator_version": args.get("evaluator_version", "unspecified"),
                        "environment_version": "python:3.12-slim"
                        if project["settings"]["execution_backend"] == "docker"
                        else "host",
                    },
                },
            )
        try:
            backend_type = (
                LocalDockerBackend
                if project["settings"]["execution_backend"] == "docker"
                else LocalProcessBackend
            )
            options = (
                {"allow_network": bool(args.get("allow_network")), "gpus": args.get("gpus")}
                if backend_type is LocalDockerBackend
                else {}
            )
            backend = backend_type(self.data_dir / "workspaces", **options)
            workspace = (
                await backend.fork_workspace(pid, experiment["id"], parent_id, parent_commit)
                if parent_id else await backend.create_workspace(
                    pid, experiment["id"], source_dir=args.get("source_dir"))
            )
            base = Path(workspace.path).resolve()
            files = args.get("files", {})
            if not isinstance(files, dict) or sum(len(str(v)) for v in files.values()) > 2_000_000:
                raise ValueError("Experiment source exceeds the size limit")
            for name, content in files.items():
                workspace.write_file(name, str(content))
            # Capture proposed source before execution, including the exact manifest.
            source = save_artifact(
                self.store,
                self.data_dir,
                pid,
                json.dumps(files, ensure_ascii=False).encode(),
                "source.json",
                "code",
                {"experiment_id": experiment["id"]},
            )
            timeout = min(
                int(args.get("timeout_seconds", project["settings"]["experiment_timeout"])),
                project["settings"]["experiment_timeout"],
            )
            deadline = time.monotonic() + max(1, timeout)
            with self.store.transaction() as tx:
                tx.update(
                    "experiments",
                    experiment["id"],
                    {"status": "running", "started_at": now(), "workspace": str(base)},
                )
                tx.event(pid, "EXPERIMENT_STARTED", {"experiment_id": experiment["id"]})
            task = asyncio.create_task(
                backend.run(workspace, command, timeout_seconds=max(1, timeout), run_id=experiment["id"])
            )
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.5)
                    with self.store.transaction() as tx:
                        p, h = tx.get("projects", pid), tx.get("holons", holon["id"])
                    from .runtime import conversation_request
                    request = conversation_request(p or {})
                    if not p or not h or p["status"] == "archived" or h.get("terminated") or (request and request.get("state") == "cancelled"):
                        await backend.cancel(experiment["id"])
                result = await task
            except BaseException:
                await backend.cancel(experiment["id"])
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            payload = serialize(result)
            payload.update(parent_experiment_id=parent_id, parent_commit=parent_commit)
            manifest_path = Path(payload.get("manifest_path", ""))
            manifest_artifact = None
            if manifest_path.is_file() and manifest_path.resolve().is_relative_to(workspace.metadata_path.resolve()):
                manifest_artifact = save_artifact(
                    self.store, self.data_dir, pid, manifest_path.read_bytes(), "run-manifest.json",
                    "experiment_manifest", {"experiment_id": experiment["id"]})
            evaluation = args.get("evaluation")
            if evaluation and payload.get("exit_code") == 0 and not payload.get("cancelled") and not payload.get("timed_out"):
                if not isinstance(evaluation, dict):
                    raise ValueError("Evaluation must provide files and an executable command")
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    # Evaluation belongs to the same approved bounded work order.
                    # It shares that order's deadline and cancellation task.
                    payload["evaluation"] = await backend.evaluate(
                        workspace, evaluation.get("files", {}), evaluation.get("command", []),
                        timeout_seconds=remaining, config=evaluation.get("config"),
                        expected_version=evaluation.get("version"))
                else:
                    payload["evaluation"] = {"verification": "not_run", "reason": "work_order_time_budget_exhausted"}
            log_artifact = save_artifact(
                self.store,
                self.data_dir,
                pid,
                json.dumps(payload, default=str).encode(),
                "execution.json",
                "execution_result",
                {"experiment_id": experiment["id"]},
            )
            artifacts = [source["id"], log_artifact["id"]]
            if manifest_artifact:
                artifacts.append(manifest_artifact["id"])
            if payload.get("evaluation"):
                evaluation_artifact = save_artifact(
                    self.store, self.data_dir, pid,
                    json.dumps(payload["evaluation"], ensure_ascii=False, default=str).encode(),
                    "evaluation.json", "evaluation_result", {"experiment_id": experiment["id"]})
                artifacts.append(evaluation_artifact["id"])
            collected = await backend.collect_artifacts(workspace)
            for entry in collected[:50]:
                info = serialize(entry)
                path = Path(info.get("path", info.get("uri", "")))
                if (
                    path.is_file()
                    and path.resolve().is_relative_to(base)
                    and path.stat().st_size <= 20_000_000
                ):
                    artifact = save_artifact(
                        self.store,
                        self.data_dir,
                        pid,
                        path.read_bytes(),
                        path.name,
                        "experiment_output",
                        {"experiment_id": experiment["id"]},
                    )
                    artifacts.append(artifact["id"])
            exit_code = payload.get("exit_code", payload.get("returncode"))
            status = (
                "cancelled"
                if payload.get("cancelled")
                else "timed_out"
                if payload.get("timed_out")
                else "completed"
                if exit_code == 0
                else "failed"
            )
            with self.store.transaction() as tx:
                tx.update(
                    "experiments",
                    experiment["id"],
                    {"status": status, "finished_at": now(), "result": payload, "artifact_ids": artifacts},
                )
                tx.event(
                    pid, "EXPERIMENT_FINISHED", {"experiment_id": experiment["id"], "exit_code": exit_code}
                )
            return {
                "summary": json.dumps(payload, default=str)[:16000],
                "cost_usd": 0,
                "evidence": [
                    {
                        "type": "experiment",
                        "summary": f"Experiment {experiment['id']} exited with {exit_code}. "
                        + str(payload.get("stdout", ""))[:6000],
                        "scope": {
                            "experiment_id": experiment["id"],
                            "exit_code": exit_code,
                            "seed": args.get("seed", 0),
                            "metrics_are_untrusted": True,
                            "evaluation_version": payload.get("evaluation", {}).get("evaluator_version"),
                            "verification": payload.get("evaluation", {}).get("verification", "reported_output"),
                            "parent_experiment_id": parent_id,
                            "parent_commit": parent_commit,
                            "status": status,
                            "timed_out": payload.get("timed_out", False),
                            "cancelled": payload.get("cancelled", False),
                        },
                        "artifact_ids": artifacts,
                    }
                ],
            }
        except BaseException as exc:
            # Preparation, execution and artifact collection all need terminal
            # state. Store only the exception type: messages can contain private
            # paths, source content or provider credentials.
            interrupted = isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
            status = "interrupted" if interrupted else "failed"
            with self.store.transaction() as tx:
                tx.update(
                    "experiments",
                    experiment["id"],
                    {"status": status, "finished_at": now(), "error_type": type(exc).__name__},
                )
                tx.event(
                    pid,
                    "EXPERIMENT_INTERRUPTED" if interrupted else "EXPERIMENT_FAILED",
                    {"experiment_id": experiment["id"], "error_type": type(exc).__name__},
                )
            raise
