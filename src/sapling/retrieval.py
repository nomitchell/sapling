"""Local candidate retrieval vectors, followed by model impact assessment.

Token hashing needs no paid embedding endpoint and never downloads research data
to a third party. It is lexical, not a semantic embedding model. PostgreSQL uses
pgvector cosine distance; SQLite uses the identical vectors for local operation.
The model performs the separate scientific relevance judgment.
"""

import hashlib
import math
import re
from collections import Counter

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
