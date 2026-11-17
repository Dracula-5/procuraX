"""Local text embeddings for hybrid retrieval: feature-hashed word, CJK-bigram and character-trigram counts.

A deterministic stand-in that needs no model download, no network and no key (model "hashing-ngram-256",
version 1). It captures surface overlap, not meaning; its value is measured, not assumed
(`app.scripts.benchmark_rag_nta_qa --hybrid`). A neural embedding model can replace it behind the same
function by changing EMBEDDING_MODEL and re-indexing. Pure, no I/O.
"""

import hashlib
import math
import unicodedata

from app.ai.models import EMBEDDING_DIMENSIONS
from app.ai.text import tokenize

EMBEDDING_MODEL = "hashing-ngram-256@1"


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % EMBEDDING_DIMENSIONS, 1.0 if (value >> 63) & 1 else -1.0


def embed(text: str) -> list[float]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    features = [f"t:{token}" for token in tokenize(normalized)]
    compact = "".join(normalized.split())
    features += [f"c:{compact[i : i + 3]}" for i in range(max(0, len(compact) - 2))]
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for feature in features:
        index, sign = _bucket(feature)
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector
