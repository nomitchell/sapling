"""Transactional local persistence, durable jobs, and immutable event history.

Domain records use versioned JSON payloads in separate tables. Core identity and
project membership are indexed columns; model-produced data never becomes SQL.
PostgreSQL is the normal store; SQLite supports isolated automated tests.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
    text,
    update,
)
from sqlalchemy.pool import StaticPool

_clock_lock = threading.Lock()
_last_timestamp = datetime.min.replace(tzinfo=timezone.utc)


def now() -> str:
    # Windows clocks can return the same instant for consecutive writes. Keep
    # records ordered by insertion instead of random UUID order on those ties.
    global _last_timestamp
    with _clock_lock:
        _last_timestamp = max(datetime.now(timezone.utc), _last_timestamp + timedelta(microseconds=1))
        return _last_timestamp.isoformat()


KINDS = "projects holons research_nodes research_references claims evidence claim_evidence artifacts experiments holon_messages peer_channels human_inputs attention_items decision_snapshots messages settings provider_credentials permission_grants".split()
IMMUTABLE = {"evidence", "artifacts", "human_inputs", "messages", "claim_evidence", "research_references"}
metadata = MetaData()
tables = {}
for kind in KINDS:
    tables[kind] = Table(
        kind,
        metadata,
        Column("id", String(64), primary_key=True),
        Column("project_id", String(64), nullable=True, index=True),
        Column("created_at", String(40), nullable=False),
        Column("data", JSON, nullable=False),
    )

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("type", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False),
    Column("holon_id", String(64), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("state", String(32), nullable=False),
    Column("priority", Float, nullable=False, default=0),
    Column("created_at", String(40), nullable=False),
    Column("lease_owner", String(64)),
    Column("lease_until", String(40)),
    Column("attempts", Integer, nullable=False, default=0),
    Column("error", Text),
)
Index("jobs_claim_idx", jobs.c.state, jobs.c.priority, jobs.c.created_at)
versions = Table("schema_versions", metadata, Column("version", Integer, primary_key=True))


class Store:
    def __init__(self, url: str):
        options: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self._lock = threading.RLock()

    def initialize(self):
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            metadata.create_all(conn)
            if self.engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS retrieval_vectors (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, embedding vector(256) NOT NULL)"
                    )
                )
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS retrieval_project_idx ON retrieval_vectors(project_id)")
                )
            version = conn.execute(select(versions.c.version)).scalar()
            if version is None:
                conn.execute(versions.insert().values(version=1))
            elif version != 1:
                raise RuntimeError(f"Unsupported database schema {version}; application expects 1")

    @contextmanager
    def transaction(self):
        with self._lock, self.engine.begin() as conn:
            # Local single-user write serialization also covers multiple workers.
            # No model or experiment operation may hold this transaction open.
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(1935764588)"))
            yield Tx(conn)

    def claim(self, owner: str, lease_seconds=90) -> dict | None:
        with self.transaction() as tx:
            stamp = now()
            expired = (
                tx.conn.execute(select(jobs).where(jobs.c.state == "running", jobs.c.lease_until < stamp))
                .mappings()
                .all()
            )
            for row in expired:
                # Model calls or host side effects may have happened. Never silently
                # replay an interrupted run and double-charge or duplicate effects.
                tx.conn.execute(
                    update(jobs)
                    .where(jobs.c.id == row["id"])
                    .values(state="interrupted", error="Worker lease expired; inspect before retry")
                )
                holon = tx.get("holons", row["holon_id"])
                if holon:
                    tx.update(
                        "holons",
                        holon["id"],
                        {"status": "blocked", "control_epoch": holon.get("control_epoch", 0) + 1},
                    )
                tx.create(
                    "attention_items",
                    {
                        "project_id": row["project_id"],
                        "holon_id": row["holon_id"],
                        "type": "interrupted_job",
                        "status": "pending",
                        "summary": "A worker stopped during a job. Review before retrying.",
                        "job_id": row["id"],
                    },
                )
                tx.event(row["project_id"], "JOB_INTERRUPTED", {"job_id": row["id"]})
            query = (
                select(jobs)
                .where(jobs.c.state == "queued")
                .order_by(jobs.c.priority.desc(), jobs.c.created_at)
                .with_for_update(skip_locked=True)
            )
            candidates = tx.conn.execute(query).mappings().all()
            running = set(tx.conn.execute(select(jobs.c.holon_id).where(jobs.c.state == "running")).scalars())
            for row in candidates:
                project = tx.get("projects", row["project_id"])
                holon = tx.get("holons", row["holon_id"])
                if (
                    not project
                    or project["status"] != "active"
                    or not holon
                    or holon["status"] in {"paused", "completed", "error", "blocked"}
                    or holon.get("chat_stopped")
                ):
                    continue
                if row["holon_id"] in running:
                    continue
                count = tx.conn.execute(
                    select(jobs.c.id).where(jobs.c.project_id == project["id"], jobs.c.state == "running")
                ).all()
                if len(count) >= project["settings"].get("max_concurrent_holons", 4):
                    continue
                values = {
                    "state": "running",
                    "lease_owner": owner,
                    "lease_until": (
                        datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                    ).isoformat(),
                    "attempts": row["attempts"] + 1,
                }
                tx.conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(**values))
                return dict(row) | values
        return None

    def heartbeat(self, job_id: str, owner: str):
        with self.transaction() as tx:
            tx.conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.state == "running")
                .values(lease_until=(datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat())
            )

    def finish(self, job_id: str, owner: str, error: str | None = None, *, cancelled: bool = False):
        with self.transaction() as tx:
            tx.conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.state == "running")
                .values(
                    state="cancelled" if cancelled else "failed" if error else "completed",
                    error=error,
                    lease_until=None,
                )
            )


class Tx:
    def __init__(self, conn):
        self.conn = conn

    def get(self, kind: str, id: str) -> dict | None:
        row = self.conn.execute(
            select(tables[kind].c.data).where(tables[kind].c.id == str(id))
        ).scalar_one_or_none()
        return dict(row) if row is not None else None

    def list(self, kind: str, project_id: str | None = None, **filters) -> list[dict]:
        table = tables[kind]
        query = select(table.c.data).order_by(table.c.created_at, table.c.id)
        if project_id is not None:
            query = query.where(table.c.project_id == str(project_id))
        rows = self.conn.execute(query).scalars().all()
        return [dict(r) for r in rows if all(r.get(k) == v for k, v in filters.items())]

    def create(self, kind: str, data: dict) -> dict:
        record = json.loads(
            json.dumps({"id": str(uuid4()), "created_at": now(), **data}, default=str, allow_nan=False)
        )
        self.conn.execute(
            tables[kind]
            .insert()
            .values(
                id=record["id"],
                project_id=record.get("project_id"),
                created_at=record["created_at"],
                data=record,
            )
        )
        if kind == "holons":
            self._index_holon(record)
        return record

    def update(self, kind: str, id: str, patch: dict) -> dict:
        if kind in IMMUTABLE:
            raise ValueError(f"{kind} records are append-only")
        current = self.get(kind, id)
        if current is None:
            raise KeyError(id)
        if any(k in patch and patch[k] != current.get(k) for k in ("id", "project_id", "created_at")):
            raise ValueError("Record identity and project membership cannot change")
        record = json.loads(json.dumps(current | patch, default=str, allow_nan=False))
        self.conn.execute(update(tables[kind]).where(tables[kind].c.id == id).values(data=record))
        if kind == "holons":
            self._index_holon(record)
        return record

    def event(self, project_id: str, type: str, payload: dict) -> dict:
        record = {
            "project_id": project_id,
            "type": type,
            "payload": json.loads(json.dumps(payload, default=str)),
            "created_at": now(),
        }
        result = self.conn.execute(events.insert().values(**record))
        return {"id": result.inserted_primary_key[0], **record}

    def enqueue(self, project_id: str, holon_id: str, kind: str, payload: dict, priority=0) -> dict:
        # Coalesce queued wakes; events and human inputs remain in canonical state.
        if kind == "turn":
            existing = (
                self.conn.execute(
                    select(jobs).where(
                        jobs.c.holon_id == holon_id, jobs.c.kind == kind, jobs.c.state == "queued"
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return dict(existing)
        record = {
            "id": str(uuid4()),
            "project_id": project_id,
            "holon_id": holon_id,
            "kind": kind,
            "payload": payload,
            "priority": priority,
            "created_at": now(),
            "state": "queued",
            "attempts": 0,
        }
        self.conn.execute(jobs.insert().values(**record))
        return record

    def cancel_queued(self, holon_id: str):
        self.conn.execute(
            update(jobs)
            .where(jobs.c.holon_id == holon_id, jobs.c.state == "queued")
            .values(state="cancelled")
        )

    def _index_holon(self, record):
        if self.conn.dialect.name != "postgresql":
            return
        from .retrieval import document, embedding

        self.conn.execute(
            text(
                "INSERT INTO retrieval_vectors(id, project_id, embedding) VALUES (:id, :project_id, CAST(:vector AS vector)) ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding"
            ),
            {
                "id": record["id"],
                "project_id": record["project_id"],
                "vector": json.dumps(embedding(document(record))),
            },
        )

    def routing_candidates(self, project_id, evidence_text, limit=20):
        from .retrieval import document, embedding, similarity

        vector = embedding(evidence_text)
        if self.conn.dialect.name == "postgresql":
            query = text(
                "SELECT v.id AS holon_id, 1 - (v.embedding <=> CAST(:vector AS vector)) AS score FROM retrieval_vectors v JOIN holons h ON h.id = v.id WHERE v.project_id = :pid AND h.data->>'status' = 'active' ORDER BY v.embedding <=> CAST(:vector AS vector) LIMIT :limit"
            )
            return [
                dict(r)
                for r in self.conn.execute(
                    query, {"pid": project_id, "vector": json.dumps(vector), "limit": limit}
                ).mappings()
                if r["score"] > 0
            ]
        rows = [
            {"holon_id": h["id"], "score": similarity(vector, embedding(document(h)))}
            for h in self.list("holons", project_id, status="active")
        ]
        return sorted((r for r in rows if r["score"] > 0), key=lambda r: (-r["score"], r["holon_id"]))[:limit]

    def history(self, project_id: str, after=0, limit=250):
        return [
            dict(r)
            for r in self.conn.execute(
                select(events)
                .where(events.c.project_id == project_id, events.c.id > after)
                .order_by(events.c.id)
                .limit(limit)
            ).mappings()
        ]

    def jobs(self, project_id: str):
        return [
            dict(r)
            for r in self.conn.execute(
                select(jobs)
                .where(jobs.c.project_id == project_id)
                .order_by(jobs.c.created_at.desc())
                .limit(200)
            ).mappings()
        ]
