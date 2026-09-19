"""Local candidate retrieval vectors, followed by model impact assessment.

Token hashing needs no paid embedding endpoint and never downloads research data
to a third party. It is lexical, not a semantic embedding model. PostgreSQL uses
pgvector cosine distance; SQLite uses the identical vectors for local operation.
The model performs the separate scientific relevance judgment.
"""

import hashlib
import json
import logging
import math
import re
import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

DIMENSIONS = 256
STOP = set(
    "a an and are as at be by for from has have in is it of on or that the their this to was were will with we you".split()
)


def embedding(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    terms = Counter(t for t in re.findall(r"[\w-]+", text.lower()) if len(t) > 2 and t not in STOP)
    for token, count in terms.items():
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % DIMENSIONS
        vector[index] += (1 + math.log(count)) * (1 if digest[4] & 1 else -1)
    norm = math.sqrt(sum(x * x for x in vector)) or 1
    return [round(x / norm, 8) for x in vector]


def document(holon: dict) -> str:
    return " ".join(str(holon.get(k, "")) for k in ("goal", "summary", "open_questions"))


def similarity(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


class SemanticRetriever:
    """Local semantic ranking with content-addressed caches and lexical fallback.

    Call outside database transactions, normally through asyncio.to_thread.
    Only model weights are downloaded; research text is embedded on this host.
    The existing database index remains a usable, independent lexical fallback.
    """

    model_name = "BAAI/bge-small-en-v1.5"

    def __init__(self, cache_dir: str | Path, *, model_factory=None):
        self.cache_dir = Path(cache_dir)
        self._factory = model_factory
        self._model = None
        self._unavailable = False
        self._lock = threading.RLock()

    def prepare(self, *, download=False) -> bool:
        with self._lock:
            if self._model is not None:
                return True
            if self._unavailable and not download:
                return False
            try:
                if self._factory is None:
                    from fastembed import TextEmbedding

                    factory = TextEmbedding
                else:
                    factory = self._factory
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = factory(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir / "models"),
                    threads=2,
                    local_files_only=not download,
                )
                self._unavailable = False
                return True
            except Exception as exc:
                self._unavailable = True
                logging.getLogger(__name__).info(
                    "Local semantic retrieval unavailable (%s); using lexical ranking", type(exc).__name__
                )
                return False

    def _key(self, text: str, query: bool) -> str:
        return hashlib.sha256(f"{self.model_name}:v1:{query}:{text}".encode()).hexdigest()

    def _vectors(self, texts: list[str], *, query=False) -> list[list[float]]:
        directory = self.cache_dir / "vectors"
        directory.mkdir(parents=True, exist_ok=True)
        vectors, missing = {}, {}
        for text in texts:
            key = self._key(text, query)
            try:
                value = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
                if not isinstance(value, list) or len(value) != 384 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in value
                ):
                    raise ValueError("Invalid cached vector")
                vectors[key] = value
            except (OSError, ValueError):
                missing[key] = text
        if missing:
            method = self._model.query_embed if query else self._model.passage_embed
            output = list(method(list(missing.values())))
            if len(output) != len(missing):
                raise ValueError("Incomplete embedding batch")
            for key, raw in zip(missing, output, strict=True):
                value = [float(v) for v in raw]
                if len(value) != 384 or not all(math.isfinite(v) for v in value):
                    raise ValueError("Invalid embedding output")
                norm = math.sqrt(sum(v * v for v in value)) or 1
                value = [v / norm for v in value]
                temporary = directory / f".{key}-{uuid4()}.tmp"
                temporary.write_text(json.dumps(value), encoding="utf-8")
                temporary.replace(directory / f"{key}.json")
                vectors[key] = value
        return [vectors[self._key(text, query)] for text in texts]

    def rank(self, query: str, rows: list[dict], *, text=document, limit=20) -> list[dict]:
        if not rows or limit <= 0:
            return []
        # Bound both inference input and cache growth per record; raw artifacts
        # remain separately retrievable when a researcher needs their full text.
        query = query[:8000]
        passages = [text(row)[:12000] for row in rows]
        lexical_query = embedding(query)
        scores = [similarity(lexical_query, embedding(p)) for p in passages]
        method = "lexical"
        with self._lock:
            if self.prepare():
                try:
                    query_vector = self._vectors([query], query=True)[0]
                    passage_vectors = self._vectors(passages)
                    scores = [0.85 * similarity(query_vector, vector) + 0.15 * lexical
                              for vector, lexical in zip(passage_vectors, scores, strict=True)]
                    method = "semantic-local"
                except Exception as exc:
                    logging.getLogger(__name__).info("Embedding failed (%s); using lexical ranking", type(exc).__name__)
        ranked = [{"id": row["id"], "holon_id": row["id"], "score": score, "retrieval": method}
                  for row, score in zip(rows, scores, strict=True)]
        return sorted(ranked, key=lambda row: (-row["score"], row["id"]))[:limit]


@lru_cache(maxsize=4)
def semantic_retriever(cache_dir: str) -> SemanticRetriever:
    return SemanticRetriever(cache_dir)


def rank_candidates(query: str, rows: list[dict], cache_dir: str | Path, limit=20) -> list[dict]:
    return semantic_retriever(str(cache_dir)).rank(query, rows, limit=limit)
