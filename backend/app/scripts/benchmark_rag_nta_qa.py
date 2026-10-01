"""Japanese retrieval benchmark on the National Tax Agency's qualified-invoice Q&A.

Source: 国税庁「消費税の仕入税額控除制度における適格請求書等保存方式に関するＱ＆Ａ」 (the official
Q&A on the Qualified Invoice System), used under the Public Data License 1.0 (CC BY 4.0
compatible). This script downloads it, extracts the question/answer pairs and edits them:
whitespace is normalised and revision tags are removed.

Task: each official question is a query; the corpus is every official answer, chunked exactly
as tenant documents are chunked in production. A query succeeds when a chunk of its own answer
is retrieved. Questions and relevance labels are the NTA's, not ours, so the test is not
written to suit the retriever. Compared: the original ASCII-word tokenizer vs the CJK
character-bigram tokenizer, both through the production BM25 ranker.

    uv run python -m app.scripts.benchmark_rag_nta_qa --output ../reports/rag_nta_invoice_qa.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import time
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.modules.procurement.assistant import _chunks, _rank_texts, ascii_tokens, tokenize

SOURCE_URL = "https://www.nta.go.jp/taxes/shiraberu/zeimokubetsu/shohi/keigenzeiritsu/pdf/qa/01-01.pdf"
SOURCE_TITLE = "消費税の仕入税額控除制度における適格請求書等保存方式に関するＱ＆Ａ"
CACHE = Path(".cache/eval/nta_invoice_qa.pdf")
CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿　-〿＀-￯"
QUESTION_START = re.compile(r"^[ \t]*問[ \t]*(\d(?:[ \t]?\d)*(?:-\d+)?)[ \t]+", re.MULTILINE)
PAGE_FOOTER = re.compile(r"^\s*(?:-\s*\d+\s*-|\d+|目次-\d+)\s*$", re.MULTILINE)
REVISION_TAG = re.compile(r"【(?:平成|令和)[^】]*】")


def _normalise(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Layout extraction inserts spaces/line breaks inside Japanese sentences; remove them.
    return re.sub(rf"(?<=[{CJK}]) (?=[{CJK}])", "", text)


def extract_pairs(raw: str) -> list[dict[str, str]]:
    text = PAGE_FOOTER.sub("", unicodedata.normalize("NFKC", raw))
    starts = list(QUESTION_START.finditer(text))
    pairs: dict[str, dict[str, str]] = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.end() : end]
        if "【答】" not in block:
            continue  # table-of-contents entry
        question, answer = block.split("【答】", 1)
        # The next question's topic heading, e.g. "(登録の手続)", trails the answer.
        answer = re.sub(r"\([^()]{1,60}\)\s*$", "", answer.rstrip())
        number = re.sub(r"\s", "", match.group(1))
        pairs.setdefault(
            number,
            {
                "id": f"問{number}",
                "question": _normalise(REVISION_TAG.sub("", question)),
                "answer": _normalise(answer),
            },
        )
    return [pair for pair in pairs.values() if pair["question"] and len(pair["answer"]) > 20]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, default=CACHE)
    parser.add_argument("--pdftotext", default="pdftotext")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if not args.pdf.exists():
        args.pdf.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ProcuraX-benchmark"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https URL
            args.pdf.write_bytes(response.read())
    digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    converter = shutil.which(args.pdftotext)
    if converter is None:
        raise SystemExit("pdftotext (Poppler) is required")
    raw = subprocess.run(  # noqa: S603 - resolved local executable, fixed arguments
        [converter, "-enc", "UTF-8", "-layout", str(args.pdf), "-"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    revision = re.search(
        r"((?:令和|平成)\s*\d+\s*年\s*\d+\s*月改訂)", unicodedata.normalize("NFKC", raw[:3000])
    )
    pairs = extract_pairs(raw)

    corpus: list[tuple[str, str, str]] = []
    for pair in pairs:
        for index, chunk in enumerate(_chunks(pair["answer"])):
            corpus.append((f"{pair['id']}#{index + 1}", pair["id"], chunk))

    results = {}
    for name, tokenizer in (("ascii_words_baseline", ascii_tokens), ("cjk_bigram", tokenize)):
        reciprocal, hit1, hit5, latencies, no_terms = [], 0, 0, [], 0
        for pair in pairs:
            if not tokenizer(pair["question"]):
                no_terms += 1
            began = time.perf_counter()
            # The title slot is empty so a chunk is matched on answer text only.
            hits = _rank_texts(pair["question"], [(cid, "", text) for cid, _, text in corpus], 10, tokenizer)
            latencies.append((time.perf_counter() - began) * 1000)
            answers = [hit.id.split("#", 1)[0] for hit in hits]
            ranked = list(dict.fromkeys(answers))  # answer-level ranking, first chunk wins
            rank = ranked.index(pair["id"]) + 1 if pair["id"] in ranked else None
            hit1 += rank == 1
            hit5 += rank is not None and rank <= 5
            reciprocal.append(1 / rank if rank else 0.0)
        count = len(pairs)
        latencies.sort()
        results[name] = {
            "queries_with_no_terms": no_terms,
            "recall_at_1": round(hit1 / count, 4),
            "recall_at_5": round(hit5 / count, 4),
            "mrr_at_10": round(sum(reciprocal) / count, 4),
            "latency_ms_per_query": {
                "p50": round(statistics.median(latencies), 1),
                "p95": round(latencies[int(0.95 * (count - 1))], 1),
            },
        }

    output = {
        "dataset": "nta_qualified_invoice_qa",
        "synthetic": False,
        "source": {
            "title": SOURCE_TITLE,
            "publisher": "国税庁 (National Tax Agency, Japan)",
            "url": SOURCE_URL,
            "revision": revision.group(1).replace(" ", "") if revision else "unknown",
            "sha256": digest,
            "license": "公共データ利用規約 第1.0版 (PDL1.0), CC BY 4.0 compatible",
            "attribution": f"出典：国税庁「{SOURCE_TITLE}」を加工して作成",
            "edits": "Extracted Q&A pairs from the PDF text layer, normalised whitespace, removed revision tags",
        },
        "generated_on": datetime.now(UTC).date().isoformat(),
        "queries": len(pairs),
        "answer_chunks": len(corpus),
        "task": "Official question → retrieve a chunk of its official answer among all answers (answer-level ranks)",
        "ranker": "production BM25 (_rank_texts, k1=1.2, b=0.75) over production 900-character chunks",
        "results": results,
        "limitations": [
            "Questions come from the same document as the answers and often share its vocabulary, so this "
            "measures in-domain lexical retrieval, not paraphrased user questions.",
            "One relevant answer per question; related answers that also help are counted as misses.",
            "Retrieval only: no generated answer, and citation usefulness is not human-judged.",
        ],
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
