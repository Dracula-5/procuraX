"""End-to-end OCR benchmark on rendered synthetic invoices (Tesseract + labeled-line parser).

Each generated invoice is rendered to an image with distractor text (addresses, a line-item
table, notes) and passed through the production adapter (`extract_ocr_text`) and parser
(`extract_invoice_text`) under three capture conditions: a clean render, a degraded scan
(rotation, blur, speckle noise, heavy JPEG compression) and a low-resolution capture. A subset of
clean renders is also wrapped in a PDF to exercise the pdftoppm path.

The documents are synthetic and English-labelled; results say nothing about real supplier
invoices, Japanese layouts, handwriting or tables. Requires Pillow (dev dependency), a
Tesseract binary and English traineddata:

    uv run python -m app.scripts.benchmark_invoice_ocr --tesseract <path> --tessdata <dir>
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from random import Random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.domain.qualified_invoice import check_digit
from app.ml.invoice_ocr import extract_ocr_text
from app.ml.invoice_text import extract_invoice_text

DEV_SEED = 53  # errors on this seed were inspected when designing the OCR-tolerant clean-up
HELD_OUT_SEED = 54  # never inspected; the unbiased (still synthetic) comparison
FIELDS = [
    "vendor",
    "invoice_number",
    "invoice_date",
    "registration_number",
    "purchase_order_number",
    "currency",
    "quantity",
    "subtotal",
    "tax",
    "total",
]
CONDITIONS = ("clean", "degraded_scan", "low_resolution")
PAGE = (1240, 1754)  # A4 at 150 dpi


def _registration_number(rng: Random) -> str:
    base = f"{rng.randint(0, 10**12 - 1):012d}"
    return f"T{check_digit(base)}{base}"


def _invoice(rng: Random, index: int) -> tuple[dict[str, str], list[str], list[str]]:
    quantity = rng.randint(1, 60)
    unit_price = rng.randrange(500, 250_000, 50)
    subtotal = quantity * unit_price
    tax = subtotal // 10
    expected = {
        "vendor": f"{rng.choice(['Kanto', 'Sakura', 'Minato', 'Hikari', 'Aoba'])} Supply {index:03d} K.K.",
        "invoice_number": f"INV-2026-{rng.randint(0, 999_999):06d}",
        "invoice_date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "registration_number": _registration_number(rng),
        "purchase_order_number": f"PO-2026-{rng.randint(0, 999_999):06d}",
        "currency": "JPY",
        "quantity": str(quantity),
        "subtotal": str(subtotal),
        "tax": str(tax),
        "total": str(subtotal + tax),
    }
    header = [
        "INVOICE",
        f"{rng.randint(1, 9)}-{rng.randint(1, 30)}-{rng.randint(1, 20)} Shiba, Minato-ku, Tokyo",
        f"Phone 03-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
        "Bill to: ProcuraX Demo Manufacturing K.K. (fictional)",
    ]
    labelled = [
        f"Vendor: {expected['vendor']}",
        f"Invoice Number: {expected['invoice_number']}",
        f"Invoice Date: {expected['invoice_date']}",
        f"Registration Number: {expected['registration_number']}",
        f"PO Number: {expected['purchase_order_number']}",
        "Currency: JPY",
    ]
    table = [
        "Item                      Qty     Unit price      Amount",
        f"Industrial parts lot      {quantity:<7} {unit_price:>10,}  {subtotal:>12,}",
    ]
    totals = [
        f"Quantity: {quantity}",
        f"Subtotal: {subtotal:,}",
        f"Tax: {tax:,}",
        f"Total: {subtotal + tax:,}",
        "Payment terms: 30 days end of month. Thank you for your business.",
    ]
    return expected, header + labelled, table + totals


def _render(lines_top: list[str], lines_bottom: list[str], font_size: int) -> Image.Image:
    image = Image.new("L", PAGE, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_size)
    y = 90
    for line in lines_top:
        draw.text((100, y), line, fill=0, font=font)
        y += int(font_size * 1.6)
    y += font_size
    for line in lines_bottom:
        draw.text((100, y), line, fill=0, font=font)
        y += int(font_size * 1.6)
    return image


def _degrade(image: Image.Image, rng: Random) -> Image.Image:
    rotated = image.rotate(rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor=255)
    blurred = rotated.filter(ImageFilter.GaussianBlur(radius=0.9))
    pixels = blurred.load()
    assert pixels is not None
    width, height = blurred.size
    for _ in range(width * height // 400):  # ~0.25% speckle
        x, y = rng.randrange(width), rng.randrange(height)
        pixels[x, y] = 0 if rng.random() < 0.5 else 255
    return blurred


def _encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--tesseract", default=None)
    parser.add_argument("--tessdata", default=None)
    parser.add_argument("--pdftoppm", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    modes = {"strict": False, "ocr_tolerant": True}
    runs: list[dict] = []
    for split, seed in (("dev", DEV_SEED), ("held_out", HELD_OUT_SEED)):
        rng = Random(seed)  # noqa: S311 - deterministic synthetic fixture generation
        for index in range(args.samples):
            expected, top, bottom = _invoice(rng, index)
            clean = _render(top, bottom, font_size=rng.choice([24, 26, 28]))
            documents = {
                "clean": (_encode(clean, "PNG"), "invoice.png"),
                "degraded_scan": (
                    _encode(_degrade(clean, rng).convert("RGB"), "JPEG", quality=30),
                    "invoice.jpg",
                ),
                "low_resolution": (
                    _encode(clean.resize((PAGE[0] // 2, PAGE[1] // 2), Image.Resampling.BILINEAR), "PNG"),
                    "invoice.png",
                ),
            }
            if index % 4 == 0:
                documents["clean_pdf"] = (
                    _encode(clean.convert("RGB"), "PDF", resolution=150.0),
                    "invoice.pdf",
                )
            for condition, (content, filename) in documents.items():
                start = time.perf_counter()
                try:
                    text: str | None = extract_ocr_text(
                        content,
                        filename,
                        tesseract_path=args.tesseract,
                        pdftoppm_path=args.pdftoppm,
                        tessdata_path=args.tessdata,
                    )
                    error = None
                except (ValueError, subprocess.CalledProcessError) as exc:
                    text, error = None, type(exc).__name__
                ocr_ms = (time.perf_counter() - start) * 1000
                for mode, tolerant in modes.items():
                    predicted = (
                        extract_invoice_text(text, ocr_tolerant=tolerant) if text else dict.fromkeys(FIELDS)
                    )
                    runs.append(
                        {
                            "split": split,
                            "mode": mode,
                            "sample": index,
                            "condition": condition,
                            "latency_ms": ocr_ms,
                            "error": error,
                            "correct": {field: predicted.get(field) == expected[field] for field in FIELDS},
                            "wrong_value": {
                                field: predicted.get(field) is not None
                                and predicted.get(field) != expected[field]
                                for field in FIELDS
                            },
                            "mistakes": {
                                field: {"expected": expected[field], "predicted": predicted.get(field)}
                                for field in FIELDS
                                if predicted.get(field) != expected[field]
                            },
                        }
                    )

    def summarise(rows: list[dict]) -> dict:
        latencies = sorted(r["latency_ms"] for r in rows)
        cells = len(rows) * len(FIELDS)
        return {
            "documents": len(rows),
            "field_exact_match_accuracy": {
                field: round(sum(r["correct"][field] for r in rows) / len(rows), 4) for field in FIELDS
            },
            "average_field_accuracy": round(sum(sum(r["correct"].values()) for r in rows) / cells, 4),
            # A wrong non-empty value is worse than an empty one: it can be accepted unnoticed.
            "wrong_value_rate": round(sum(sum(r["wrong_value"].values()) for r in rows) / cells, 4),
            "document_exact_match": round(sum(all(r["correct"].values()) for r in rows) / len(rows), 4),
            "processing_errors": sum(r["error"] is not None for r in rows),
            "ocr_latency_ms": {
                "p50": round(statistics.median(latencies), 1),
                "p95": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 1),
            },
        }

    results: dict = {}
    for split in ("dev", "held_out"):
        for mode in modes:
            results.setdefault(split, {})[mode] = {
                condition: summarise(rows)
                for condition in (*CONDITIONS, "clean_pdf")
                if (
                    rows := [
                        r
                        for r in runs
                        if r["split"] == split and r["mode"] == mode and r["condition"] == condition
                    ]
                )
            }
    version = subprocess.run(  # noqa: S603 - local benchmark tool, fixed arguments
        [args.tesseract or "tesseract", "--version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    result = {
        "dataset": "synthetic_rendered_invoices_v1",
        "synthetic": True,
        "seeds": {"dev": DEV_SEED, "held_out": HELD_OUT_SEED},
        "samples_per_split": args.samples,
        "generated_on": datetime.now(UTC).date().isoformat(),
        "engine": version[0] if version else "unknown",
        "languages": "eng",
        "pipeline": "production adapter extract_ocr_text (--psm 6) + labeled-line parser extract_invoice_text",
        "parser_modes": {
            "strict": "Exact 'Label: value' extraction with amount normalisation (the earlier baseline)",
            "ocr_tolerant": (
                "Adds punctuation clean-up for code fields, digit-confusion mapping in amounts and a "
                "registration-number 'T' repair accepted only when the check digit validates"
            ),
        },
        "methodology": (
            "Clean-up rules were written after inspecting dev-split errors; the held-out split was "
            "generated with a different seed and not inspected, so it is the comparison to quote."
        ),
        "conditions": {
            "clean": "Rendered A4 page at 150 dpi, PNG",
            "degraded_scan": "Rotation ±1.5°, Gaussian blur 0.9 px, 0.25% speckle, JPEG quality 30",
            "low_resolution": "Half-scale render (~75 dpi), PNG",
            "clean_pdf": "Every fourth clean render wrapped in an image PDF (pdftoppm path)",
        },
        "results": results,
        "held_out_error_examples_ocr_tolerant": [
            {"sample": r["sample"], "condition": r["condition"], "mistakes": r["mistakes"]}
            for r in runs
            if r["split"] == "held_out" and r["mode"] == "ocr_tolerant" and r["mistakes"]
        ][:15],
        "limitations": [
            "Synthetic, English-labelled, single-column layouts rendered with one bundled font.",
            "Fields are read only from 'Label: value' lines; the line-item table is a distractor, not parsed.",
            "No real supplier invoices, Japanese text, handwriting, stamps (hanko), multi-page or skewed photos.",
            "Accuracy here is not an estimate of production extraction accuracy.",
        ],
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
