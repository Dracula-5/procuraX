"""Japanese invoice OCR benchmark on synthetic rendered 適格請求書 (qualified invoices).

    uv run python -m app.scripts.benchmark_invoice_ocr_ja --font C:/Windows/Fonts/YuGothR.ttc \\
        --ocr-image procurax-api:local --output ../reports/invoice_ocr_ja_synthetic.json

1. Renders seeded synthetic invoices with Japanese labels (取引先, 請求書番号, 登録番号, 請求日, 小計, 消費税,
   合計, …), full-width colons, 円 amounts and 2026年10月31日 dates, with a Japanese font from the host.
   Conditions: clean (300 dpi-like) and degraded (rotation, blur, noise, JPEG) renders.
2. Runs tesseract inside the production API image (Debian `tesseract-ocr` + `tesseract-ocr-jpn`, the same
   binaries the deployed OCR endpoint uses) with `--psm 6`, once with `eng` and once with `jpn+eng`, and
   records the per-image wall time measured inside the container.
3. Parses the text with the production parser (`app.ml.invoice_text`, strict and OCR-tolerant modes) and
   scores field accuracy against the generated labels.

Everything is synthetic and the numbers describe these renders only, not real scanned invoices.
"""

import argparse
import io
import json
import shlex
import statistics
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from random import Random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.domain.qualified_invoice import check_digit
from app.ml.invoice_text import extract_invoice_text

FIELDS = (
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
)
VENDORS = (
    "株式会社みなと文具",
    "東都オフィス株式会社",
    "さくらテック合同会社",
    "北斗物流株式会社",
    "青空サービス株式会社",
)


def _registration(rng: Random) -> str:
    body = "".join(str(rng.randint(0, 9)) for _ in range(12))
    return f"T{check_digit(body)}{body}"


def _invoice(rng: Random, index: int) -> tuple[dict[str, str], list[str]]:
    quantity = rng.randint(1, 40)
    unit = rng.choice((1200, 3500, 8800, 15000, 42000))
    subtotal = quantity * unit
    tax = subtotal // 10
    issued = date(2026, 4, 1) + timedelta(days=rng.randint(0, 200))
    labels = {
        "vendor": rng.choice(VENDORS),
        "invoice_number": f"INV-{rng.randint(2025, 2026)}-{index:05d}",
        "invoice_date": issued.isoformat(),
        "registration_number": _registration(rng),
        "purchase_order_number": f"PO-2026-{rng.randint(1, 999999):06d}",
        "currency": "JPY",
        "quantity": str(quantity),
        "subtotal": str(subtotal),
        "tax": str(tax),
        "total": str(subtotal + tax),
    }
    lines = [
        "適格請求書",
        f"取引先：{labels['vendor']}",
        f"登録番号：{labels['registration_number']}",
        f"請求書番号：{labels['invoice_number']}",
        f"請求日：{issued.year}年{issued.month}月{issued.day}日",
        f"注文番号：{labels['purchase_order_number']}",
        "通貨：円",
        f"数量：{quantity}",
        f"小計：{subtotal:,}円",
        f"消費税：{tax:,}円",
        f"合計：{subtotal + tax:,}円",
        "（10%対象）",
    ]
    return labels, lines


def _render(lines: list[str], font_path: str, size: int) -> Image.Image:
    font = ImageFont.truetype(font_path, size)
    width, line_height = 1400, int(size * 1.9)
    image = Image.new("L", (width, line_height * (len(lines) + 2)), 255)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines, start=1):
        draw.text((80, i * line_height), line, font=font, fill=0)
    return image


def _degrade(image: Image.Image, rng: Random) -> Image.Image:
    rotated = image.rotate(rng.uniform(-1.5, 1.5), expand=True, fillcolor=255)
    blurred = rotated.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.6, 1.1)))
    noisy = blurred.point(lambda v: max(0, min(255, v + rng.randint(-25, 25))))
    small = noisy.resize((int(noisy.width * 0.7), int(noisy.height * 0.7)))
    buffer = io.BytesIO()
    small.save(buffer, format="JPEG", quality=45)
    buffer.seek(0)
    return Image.open(buffer).convert("L")


def _ocr(work: Path, image: str, languages: str) -> None:
    script = (
        "for f in /work/*.png; do b=${f%.png}; s=$(date +%s%N); "
        f'tesseract "$f" "$b.{languages.replace("+", "_")}" -l {languages} --psm 6 >/dev/null 2>&1; '
        f'e=$(date +%s%N); echo $(( (e - s) / 1000000 )) > "$b.{languages.replace("+", "_")}.ms"; done'
    )
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work.resolve()}:/work",
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
    ]
    subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603


def _score(labels: dict[str, str], text: str, tolerant: bool) -> tuple[dict[str, bool], int]:
    """(field correct?, number of fields given a wrong value rather than left empty)."""
    parsed = extract_invoice_text(text, ocr_tolerant=tolerant)
    wrong = sum(1 for f in FIELDS if parsed[f] is not None and parsed[f] != labels[f])
    return {f: parsed[f] == labels[f] for f in FIELDS}, wrong


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", required=True, help="a TrueType/OpenType font with Japanese glyphs")
    parser.add_argument("--ocr-image", default="procurax-api:local")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--seed", type=int, default=62)
    parser.add_argument(
        "--split", default="held-out", help="label for the report (dev seed 61 was used to tune)"
    )
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rng = Random(args.seed)  # noqa: S311 - seeded synthetic data, not security
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="ocr-ja-"))
    work.mkdir(parents=True, exist_ok=True)
    truth: dict[str, dict[str, str]] = {}
    for index in range(args.samples):
        labels, lines = _invoice(rng, index)
        clean = _render(lines, args.font, size=34)
        for condition, image in (("clean", clean), ("degraded", _degrade(clean, rng))):
            name = f"{condition}-{index:03d}"
            image.save(work / f"{name}.png")
            truth[name] = labels

    version = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--entrypoint", "tesseract", args.ocr_image, "--version"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()[0]
    results: dict[str, dict] = {}
    for languages in ("eng", "jpn+eng"):
        _ocr(work, args.ocr_image, languages)
        suffix = languages.replace("+", "_")
        for condition in ("clean", "degraded"):
            for tolerant in (False, True):
                names = sorted(n for n in truth if n.startswith(condition))
                per_field = {f: 0 for f in FIELDS}
                exact = 0
                wrong_values = 0
                latencies = []
                for name in names:
                    text = (work / f"{name}.{suffix}.txt").read_text(encoding="utf-8", errors="replace")
                    latencies.append(int((work / f"{name}.{suffix}.ms").read_text().strip()))
                    score, wrong = _score(truth[name], text, tolerant)
                    wrong_values += wrong
                    exact += all(score.values())
                    for f, ok in score.items():
                        per_field[f] += ok
                key = f"{languages}/{condition}/{'ocr_tolerant' if tolerant else 'strict'}"
                results[key] = {
                    "documents": len(names),
                    "average_field_accuracy": round(sum(per_field.values()) / (len(names) * len(FIELDS)), 4),
                    "documents_fully_correct": round(exact / len(names), 4),
                    "wrong_value_rate": round(wrong_values / (len(names) * len(FIELDS)), 4),
                    "field_accuracy": {f: round(v / len(names), 4) for f, v in per_field.items()},
                    "ocr_ms_p50": statistics.median(latencies),
                    "ocr_ms_p95": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)],
                }
    report = {
        "dataset": "synthetic_rendered_japanese_invoices_v1",
        "synthetic": True,
        "generated_on": datetime.now(UTC).date().isoformat(),
        "seed": args.seed,
        "split": args.split,
        "samples_per_condition": args.samples,
        "font": Path(args.font).name,
        "engine": version,
        "ocr_runtime": f"docker image {args.ocr_image} (tesseract-ocr + tesseract-ocr-jpn, --psm 6)",
        "parser": "app.ml.invoice_text.extract_invoice_text (strict / ocr_tolerant)",
        "conditions": {
            "clean": "rendered at 34 px",
            "degraded": "±1.5° rotation, blur, noise, 70 % scale, JPEG q45",
        },
        "results": results,
    }
    body = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(body + "\n", encoding="utf-8")
    print(body)
    print("work dir:", shlex.quote(str(work)))


if __name__ == "__main__":
    main()
