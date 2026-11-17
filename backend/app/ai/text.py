"""Text helpers shared by retrieval and the assistant: tokenizer (ASCII words + CJK bigrams), BM25, chunking.

Pure functions, no I/O (moved here from the assistant router so the AI modules and benchmarks can use
them without importing a web module).
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

_STOPWORDS = {
    "a", "an", "and", "are", "can", "do", "for", "how", "i", "in", "is", "me", "of", "our", "the",
    "this", "to", "what", "when", "which", "who", "why", "with",
}  # fmt: skip
_WORD = re.compile(r"[a-z0-9]+")
# Hiragana, katakana (incl. prolonged sound mark), CJK unified ideographs and compatibility forms.
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")


def ascii_tokens(text: str) -> list[str]:
    """Original tokenizer: lower-case ASCII words only. Japanese text yields no terms."""
    return [term for term in _WORD.findall(text.casefold()) if term not in _STOPWORDS]


def tokenize(text: str) -> list[str]:
    """ASCII words plus overlapping character bigrams for Japanese/CJK runs.

    Japanese is written without spaces, so word-splitting finds nothing. Character bigrams are
    the standard dictionary-free approach for CJK retrieval. NFKC first folds full-width forms
    (ＱＡ, １２) to their ASCII equivalents so mixed-width text matches.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = ascii_tokens(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend([run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)])
    return tokens


@dataclass(frozen=True)
class Passage:
    id: str
    title: str
    text: str
    score: float


def bm25_rank(
    question: str,
    passages: list[Passage],
    limit: int = 5,
    tokenizer: Callable[[str], list[str]] = tokenize,
) -> list[Passage]:
    """Okapi BM25 (k1 = 1.2, b = 0.75) over title + text; ties broken by id."""
    query_terms = set(tokenizer(question))
    if not query_terms or not passages:
        return []
    tokenized = [tokenizer(f"{p.title} {p.text}") for p in passages]
    count = len(passages)
    average_length = sum(map(len, tokenized)) / max(count, 1)
    frequencies = [Counter(tokens) for tokens in tokenized]
    idf = {
        term: math.log(
            1
            + (count - sum(term in f for f in frequencies) + 0.5)
            / (sum(term in f for f in frequencies) + 0.5)
        )
        for term in query_terms
    }
    hits: list[Passage] = []
    for passage, tokens, freq in zip(passages, tokenized, frequencies, strict=True):
        score = 0.0
        for term in query_terms:
            tf = freq[term]
            if tf:
                score += (
                    idf[term] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / max(average_length, 1)))
                )
        if score > 0:
            hits.append(Passage(passage.id, passage.title, passage.text, round(score, 4)))
    return sorted(hits, key=lambda hit: (-hit.score, hit.id))[:limit]


def chunks(content: str, *, size: int = 900, overlap: int = 120) -> list[str]:
    normalized = " ".join(content.split())
    pieces: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + size)
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        pieces.append(normalized[start:end])
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return pieces
